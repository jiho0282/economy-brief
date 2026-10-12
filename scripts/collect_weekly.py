"""주간 경제동향 메일용 데이터 수집 (GitHub Actions에서 실행)

환율(한국수출입은행), 금리(우리은행), 뉴스(한국경제 모닝루틴 Pick)를 모아
data/weekly/latest.json 과 data/weekly/{이번주월요일}.json 에 저장한다.
Claude 예약 작업은 이 파일만 읽어서 요약·퀴즈·메일을 만든다.

사용법:
  python scripts/collect_weekly.py            # 이번 주 월요일 기준
  python scripts/collect_weekly.py 20261012   # 기준 월요일 지정
  python scripts/collect_weekly.py --if-missing  # 이번 주 데이터가 이미 있으면 건너뜀
"""
import html as htmllib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import woori_rates  # noqa: E402

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "weekly"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
WEEKDAY = "월화수목금토일"


def fmt_md(ds: str) -> str:
    d = datetime.strptime(ds, "%Y%m%d").date()
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"


def week_label(ds: str) -> str:
    d = datetime.strptime(ds, "%Y%m%d").date()
    return f"{d.month}월 {(d.day + 6) // 7}주차"


# ---------------- 환율 ----------------
def exim_one(ds: str, key: str):
    url = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"
    r = requests.get(url, params={"authkey": key, "searchdate": ds, "data": "AP01"},
                     headers=UA, timeout=30)
    data = r.json()
    if not isinstance(data, list) or not data:
        return None
    out = {}
    for it in data:
        v = float(str(it.get("deal_bas_r", "0")).replace(",", "") or 0)
        if it.get("cur_unit") == "USD":
            out["USD"] = v
        elif it.get("cur_unit") == "JPY(100)":
            out["JPY(100)"] = v
        elif it.get("cur_unit") == "EUR":
            out["EUR"] = v
    return out if len(out) == 3 else None


def exim_with_fallback(base: str, key: str):
    d = datetime.strptime(base, "%Y%m%d")
    last_err = None
    for _ in range(10):
        ds = d.strftime("%Y%m%d")
        try:
            res = exim_one(ds, key)
            if res:
                return {"requestedDate": base, "date": ds, "rates": res}
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            print(f"환율 조회 실패 {ds}: {e}")
        d -= timedelta(days=1)
    return {"requestedDate": base, "error": last_err or "최근 10일 내 환율 데이터 없음"}


def collect_exchange(prev: str, cur: str):
    key = os.environ.get("EXIM_AUTH_KEY", "").strip()
    if not key:
        return {"error": "EXIM_AUTH_KEY 비밀값이 설정되지 않았습니다."}
    p, c = exim_with_fallback(prev, key), exim_with_fallback(cur, key)
    if "error" in p or "error" in c:
        return {"error": p.get("error") or c.get("error"), "previous": p, "current": c}
    items = []
    for cu in ("USD", "JPY(100)", "EUR"):
        pv, cv = p["rates"][cu], c["rates"][cu]
        items.append({"currency": cu, "previous": pv, "current": cv, "diff": round(cv - pv, 2)})
    return {
        "previousDate": p["date"], "currentDate": c["date"],
        "dateRangeLabel": f"{fmt_md(p['date'])} → {fmt_md(c['date'])}",
        "items": items,
    }


# ---------------- 금리 ----------------
def collect_rates(prev: str, cur: str):
    p, c = woori_rates.fetch_with_fallback(prev), woori_rates.fetch_with_fallback(cur)
    if "error" in p or "error" in c:
        return {"error": p.get("error") or c.get("error"), "previous": p, "current": c}
    gp, gc = p["shortLoanBaseRate"] + 1.50, c["shortLoanBaseRate"] + 1.50
    op, oc = p["overdraftBaseRate"] + 1.10, c["overdraftBaseRate"] + 1.10
    return {
        "previousDate": p["date"], "currentDate": c["date"],
        "dateRangeLabel": f"{fmt_md(p['date'])} → {fmt_md(c['date'])}",
        "previousLabel": week_label(prev), "currentLabel": week_label(cur),
        "base": {
            "shortLoan": {"previous": p["shortLoanBaseRate"], "current": c["shortLoanBaseRate"]},
            "overdraft": {"previous": p["overdraftBaseRate"], "current": c["overdraftBaseRate"]},
        },
        "generalLoan": {"formula": "단기대출 기준금리 + 1.50%p", "previous": round(gp, 2),
                        "current": round(gc, 2), "diffBp": round((gc - gp) * 100)},
        "overdraftLoan": {"formula": "당좌대출 기준금리 + 1.10%p", "previous": round(op, 2),
                          "current": round(oc, 2), "diffBp": round((oc - op) * 100)},
        "snippets": {"previous": p.get("snippetShort"), "current": c.get("snippetShort")},
    }


# ---------------- 뉴스 ----------------
EXCHANGE_KW = [("환율", 80), ("원·달러", 70), ("원달러", 70), ("원/달러", 70), ("달러", 45),
               ("원화", 45), ("강달러", 45), ("외환", 40), ("엔화", 30), ("유로", 30)]
INTEREST_KW = [("금리", 80), ("국채금리", 75), ("국채 금리", 75), ("국고채", 60), ("국채", 45),
               ("채권", 45), ("연준", 40), ("FOMC", 40), ("기준금리", 60), ("10년물", 30), ("30년물", 30)]
# 관심 키워드 (태그명, 정규식)
FOCUS_KW = [
    ("원자재", r"원자재|구리|니켈|리튬|철광석|광물"),
    ("금", r"금값|금 ?시세|금 ?가격|국제 ?금|금 ?선물|골드|金값"),
    ("유가", r"유가|원유|석유|WTI|브렌트|OPEC|유조선"),
    ("에너지", r"에너지|LNG|가스|전력|발전|원전|태양광"),
    ("건축", r"건축|건설|시공|재건축|분양|부동산|아파트|주택|집값"),
    ("채권", r"채권|국채|국고채|회사채"),
]
# 관심 키워드가 아닌 기사의 태그 표시용 (선정 우선순위에는 영향 없음)
TAG_ONLY_KW = [("삼성", r"삼성|삼전"), ("반도체", r"반도체|HBM|메모리"), ("증시", r"코스피|코스닥|증시|주가"),
               ("부동산", r"부동산|아파트|집값")]
PREMIUM_MARKERS = ("한경 프리미엄9의 모든 콘텐츠", "무료 열람 혜택", "5회 무료 열람", "한경 프리미엄9을 구독하고")


def clean(s: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", s or "", flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(htmllib.unescape(s)).replace("\xa0", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def hk_date_tabs():
    html = requests.get("https://www.hankyung.com/mr", headers=UA, timeout=30).text
    tabs, seen = [], set()
    for b in re.findall(r"<button\b[^>]*>[\s\S]*?</button>", html, flags=re.I):
        m_id = re.search(r'data-id=["\']([^"\']+)["\']', b)
        label = clean(b)
        m_lab = re.search(r"(\d{2})월\s*(\d{2})일", label)
        if not m_id or not m_lab:
            continue
        key = m_id.group(1)
        if key in seen:
            continue
        seen.add(key)
        m_pub = re.search(r'data-pub_date=["\']([^"\']+)["\']', b)
        tabs.append({"id": key, "label": f"{m_lab.group(1)}월 {m_lab.group(2)}일",
                     "pubDate": m_pub.group(1) if m_pub else ""})
    return tabs


def hk_tab_articles(tab_id: str):
    r = requests.get("https://www.hankyung.com/mr/action/getVideoInfo", params={"id": tab_id},
                     headers={**UA, "Referer": "https://www.hankyung.com/mr",
                              "X-Requested-With": "XMLHttpRequest"}, timeout=30)
    data = r.json()
    out = []
    for i, n in enumerate(data.get("news_list") or []):
        title, url = clean(n.get("title", "")), n.get("url") or ""
        if len(title) < 6 or "hankyung.com" not in url:
            continue
        m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(n.get("pub_date", "")))
        pub = f"{m.group(1)}.{int(m.group(2)):02d}.{int(m.group(3)):02d}" if m else ""
        out.append({"title": title, "url": url, "pubDate": pub, "rank": i + 1})
    return out


def is_premium(url: str, html: str) -> bool:
    """한경 프리미엄9(유료) 기사 여부: 기사번호가 영문으로 끝나거나 유료 안내 문구가 있으면 유료로 본다."""
    if re.search(r"/article/\d+[a-zA-Z]+$", url.split("?")[0]):
        return True
    if re.search(r"hk_premium_code=\d", html):
        return True
    return any(m in html for m in PREMIUM_MARKERS)


def article_body(url: str, debug=None, info=None) -> str:
    try:
        html = requests.get(url, headers=UA, timeout=30).text
    except Exception as e:  # noqa: BLE001
        return f"[본문 조회 실패: {e}]"
    if info is not None:
        info["premium"] = is_premium(url, html)
    cands = []
    # 1) JSON-LD articleBody
    for m in re.finditer(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', html):
        try:
            cands.append(json.loads('"' + m.group(1) + '"'))
        except Exception:  # noqa: BLE001
            pass
    # 2) 본문 컨테이너
    for pat in (r'id=["\']articletxt["\']', r'class=["\'][^"\']*article-body[^"\']*["\']',
                r'itemprop=["\']articleBody["\']', r'class=["\'][^"\']*article-contents?[^"\']*["\']'):
        m = re.search(pat, html)
        if m:
            chunk = html[m.end():m.end() + 40000]
            chunk = re.split(r'<div[^>]+class=["\'][^"\']*(?:article-copyright|byline|article-ad|related|reporter|tag-list)', chunk)[0]
            cands.append(clean(chunk.split(">", 1)[-1]))
    # 3) 메타 설명
    for pat in (r'property=["\']og:description["\'][^>]+content=["\']([^"\']*)', r'name=["\']description["\'][^>]+content=["\']([^"\']*)'):
        d = re.search(pat, html)
        if d:
            cands.append(clean(d.group(1)))
    text = max(cands, key=len) if cands else ""
    if debug is not None and len(text) < 300:
        debug.append({"url": url, "htmlLen": len(html),
                      "markers": {k: html.find(k) for k in ("articletxt", "article-body", "articleBody", "og:description", "premium", "로그인", "구독")}})
    return re.sub(r"\n\s*\n+", "\n", text).strip()[:3500]


def kw_score(title, kws):
    return sum(sc for w, sc in kws if w in title)


def focus_tag(title):
    for tag, pat in FOCUS_KW:
        if re.search(pat, title):
            return tag
    return None


def display_tag(title):
    t = focus_tag(title)
    if t:
        return t
    for tag, pat in TAG_ONLY_KW:
        if re.search(pat, title):
            return tag
    return "주요"


def collect_news():
    tabs = hk_date_tabs()[:5]
    blocks = []
    for di, t in enumerate(tabs):
        try:
            arts = hk_tab_articles(t["id"])
        except Exception as e:  # noqa: BLE001
            print(f"뉴스 탭 조회 실패 {t}: {e}")
            arts = []
        for a in arts:
            a["dateIndex"], a["tabLabel"] = di, t["label"]
        blocks.append({**t, "articles": arts})
    pool, seen = [], set()
    for b in blocks:
        for a in b["articles"]:
            if a["url"] not in seen:
                seen.add(a["url"])
                pool.append(a)
    if not pool:
        return {"error": "한국경제 모닝루틴 기사 목록을 가져오지 못했습니다.", "tabs": tabs}

    rank_sc = {1: 50, 2: 35, 3: 25, 4: 15}
    date_sc = {0: 10, 1: 8, 2: 6, 3: 4}
    used = set()

    def best(kws):
        cands = []
        for a in pool:
            if a["url"] in used:
                continue
            k = kw_score(a["title"], kws)
            if k > 0:
                cands.append((k + rank_sc.get(a["rank"], 5) + date_sc.get(a["dateIndex"], 2), a))
        cands.sort(key=lambda x: -x[0])
        return [a for _, a in cands]

    dbg, skipped, cache = [], [], {}

    def ok(a):
        """본문을 받아 보고 무료·본문 충분하면 True."""
        if a["url"] not in cache:
            info = {}
            body = article_body(a["url"], dbg, info)
            cache[a["url"]] = (body, info.get("premium", False))
        body, prem = cache[a["url"]]
        if prem or len(body) < 300:
            skipped.append({"title": a["title"], "url": a["url"], "reason": "유료기사" if prem else "본문 부족"})
            used.add(a["url"])
            return False
        return True

    def add(a, tag, role):
        used.add(a["url"])
        selected.append({**a, "tag": tag, "role": role, "body": cache[a["url"]][0]})

    selected = []
    # 필수: 환율 1, 금리 1
    for tag, kws in (("환율", EXCHANGE_KW), ("금리", INTEREST_KW)):
        for a in best(kws):
            if a["url"] not in used and ok(a):
                add(a, tag, "필수")
                break

    # 관심 키워드: 날짜별 상위 3개 중에서 (원자재·금·유가·에너지·건축·채권), 서로 다른 주제 우선
    def top3():
        return sorted([a for a in pool if a["rank"] <= 3 and a["url"] not in used],
                      key=lambda x: (x["dateIndex"], x["rank"]))
    n_focus = lambda: len([x for x in selected if x["role"] == "관심"])
    tags_used = set()
    for distinct in (True, False):
        for a in top3():
            t = focus_tag(a["title"])
            if n_focus() >= 2 or not t or (distinct and t in tags_used):
                continue
            if ok(a):
                tags_used.add(t)
                add(a, t, "관심")
    # 부족하면 날짜별 1위 → 2위 → 3위 기사 순으로 채움
    for a in sorted(top3(), key=lambda x: (x["rank"], x["dateIndex"])):
        if len(selected) >= 4:
            break
        if a["url"] not in used and ok(a):
            add(a, display_tag(a["title"]), "보충")

    # 예비 후보(요약이 어려울 때 교체용)
    spares = []
    for a in sorted(top3(), key=lambda x: (x["rank"], x["dateIndex"])):
        if len(spares) >= 3:
            break
        if a["url"] not in used and ok(a):
            used.add(a["url"])
            spares.append({**a, "tag": display_tag(a["title"]), "body": cache[a["url"]][0]})

    labels = [b["label"] for b in blocks]
    def to_ds(lbl, ref_year):
        m = re.match(r"(\d{2})월 (\d{2})일", lbl)
        return date(ref_year, int(m.group(1)), int(m.group(2)))
    year = datetime.now(KST).year
    ds_list = sorted(to_ds(l, year) for l in labels)
    rng = f"{fmt_md(ds_list[0].strftime('%Y%m%d'))} ~ {fmt_md(ds_list[-1].strftime('%Y%m%d'))}" if ds_list else ""
    return {"tabs": labels, "dateRangeLabel": rng, "selected": selected, "spares": spares, "skipped": skipped, "bodyDebug": dbg,
            "allArticles": [{k: a[k] for k in ("title", "url", "pubDate", "rank", "tabLabel")} for a in pool]}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    today = datetime.now(KST).date()
    cur_mon = datetime.strptime(args[0], "%Y%m%d").date() if args else today - timedelta(days=today.weekday())
    prev_mon = cur_mon - timedelta(days=7)
    cur, prev = cur_mon.strftime("%Y%m%d"), prev_mon.strftime("%Y%m%d")
    out_file = OUT_DIR / f"{cur}.json"

    if "--if-missing" in sys.argv and out_file.exists():
        old = json.loads(out_file.read_text(encoding="utf-8"))
        if not old.get("errors"):
            print("이번 주 데이터가 이미 있어 건너뜁니다.")
            return

    result = {
        "weekMonday": cur, "previousMonday": prev,
        "title": f"{week_label(cur)} 경제 동향",
        "collectedAt": datetime.now(KST).isoformat(timespec="seconds"),
    }
    for name, fn in (("exchange", lambda: collect_exchange(prev, cur)),
                     ("rates", lambda: collect_rates(prev, cur)),
                     ("news", collect_news)):
        try:
            result[name] = fn()
        except Exception as e:  # noqa: BLE001
            result[name] = {"error": f"{type(e).__name__}: {e}"}
    result["errors"] = {k: result[k]["error"] for k in ("exchange", "rates", "news") if "error" in result[k]}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    out_file.write_text(text, encoding="utf-8")
    (OUT_DIR / "latest.json").write_text(text, encoding="utf-8")
    print(json.dumps(result.get("errors"), ensure_ascii=False))


if __name__ == "__main__":
    main()
