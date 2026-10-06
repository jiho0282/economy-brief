"""우리은행 대출기준금리 수집 (GitHub Actions에서 실행)

- 우리은행 대출기준금리표 조회 주소에 날짜를 POST로 보내 금리표를 받는다.
- '단기대출', '당좌대출' 글자 바로 뒤의 첫 소수점 숫자를 기준금리로 읽는다.
  (기존 Apps Script 방식 값과 원문 일부도 함께 저장해 검증할 수 있게 한다.)
- 결과를 data/woori_rates.json 에 날짜별로 누적 저장한다.

사용법:
  python scripts/woori_rates.py               # 이번 주/지난주 월요일 기준 자동 조회
  python scripts/woori_rates.py 20261005 ...  # 지정 날짜 조회
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

URL = "https://spot.wooribank.com/pot/jcc?withyou=POLON0021&__ID=c007973"
REFERER = "https://spot.wooribank.com/pot/Dream?withyou=POLON0021"
OUT = Path(__file__).resolve().parent.parent / "data" / "woori_rates.json"
KST = timezone(timedelta(hours=9))


def clean_text(html: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def fetch_one(date_str: str):
    payload = {
        "BA_DT": date_str,
        "START_DATE1": date_str,
        "START_DATE1Y": date_str[:4],
        "START_DATE1M": date_str[4:6],
        "START_DATE1D": date_str[6:8],
        "INFO_LOAN_DIS": "100",
        "INFO_TOTL_DIS": "3",
        "INFO_PRD_DIS": "2",
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": REFERER}
    r = requests.post(URL, data=payload, headers=headers, timeout=30)
    raw = r.content
    text = ""
    for enc in ("utf-8", "cp949"):
        t = clean_text(raw.decode(enc, errors="replace"))
        if "단기대출" in t and "당좌대출" in t:
            text = t
            break
    if not text:
        return None

    i_short, i_over = text.find("단기대출"), text.find("당좌대출")
    # 단기대출: '7일이내' 바로 뒤 숫자
    m_short = re.search(r"7일\s*이내\s*(\d{1,2}\.\d{1,3})", text[i_short:])
    # 당좌대출: '당좌대출' 이후 '기준금리' 표 머리글 뒤에 나오는 첫 금리 숫자 (조회기준일 2026.10 같은 날짜는 제외)
    over_part = text[i_over:]
    j = over_part.find("기준금리")
    m_over = re.search(r"(?<![\d.])(\d{1,2}\.\d{1,3})(?![\d.])", over_part[j:] if j >= 0 else over_part)
    if not m_short or not m_over:
        return None
    m_date = re.search(r"조회기준일\s*:\s*(\d{4})\.(\d{2})\.(\d{2})", text)
    nums = re.findall(r"\d+\.\d+", text)
    return {
        "shortLoanBaseRate": float(m_short.group(1)),
        "overdraftBaseRate": float(m_over.group(1)),
        "bankBaseDate": "".join(m_date.groups()) if m_date else None,
        "legacyShortLoanBaseRate": float(nums[1]) if len(nums) > 1 else None,
        "legacyOverdraftBaseRate": float(nums[-1]) if nums else None,
        "snippetShort": text[i_short:i_short + 150],
        "snippetOverdraft": text[i_over:i_over + 100],
    }


def fetch_with_fallback(base: str):
    d = datetime.strptime(base, "%Y%m%d")
    for _ in range(10):
        ds = d.strftime("%Y%m%d")
        try:
            res = fetch_one(ds)
        except Exception as e:  # noqa: BLE001
            print(f"조회 실패 {ds}: {e}")
            res = None
        if res:
            res["requestedDate"] = base
            res["date"] = ds
            return res
        d -= timedelta(days=1)
    return {"requestedDate": base, "error": "최근 10일 내 금리 데이터를 찾지 못했습니다."}


def default_dates():
    today = datetime.now(KST).date()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    return [last_monday.strftime("%Y%m%d"), this_monday.strftime("%Y%m%d")]


def main():
    dates = sys.argv[1:] or default_dates()
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    data.setdefault("byRequestedDate", {})
    for base in dates:
        res = fetch_with_fallback(base)
        print(json.dumps(res, ensure_ascii=False))
        data["byRequestedDate"][base] = res
    data["updatedAt"] = datetime.now(KST).isoformat(timespec="seconds")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
