# 주간 경제동향 메일 작성 규칙

> 데이터 수집은 GitHub Actions(`.github/workflows/weekly-collect.yml`)가 맡는다. 예약 작업은 수집된 JSON과 템플릿만 읽는다.

매주 월요일 오후, 아래 규칙대로 메일을 만들어 보낸다. 디자인은 `templates/mail_example.html`과 **완전히 같게** 유지하고 값만 바꾼다.
(배경색은 `bgcolor` 속성과 `background-color`를 같이 쓰고, 모든 글자 요소에 `font-family:'삼성금융체','Malgun Gothic',sans-serif;`를 직접 지정한다. 회사 메일 프로그램이 바깥쪽 스타일을 무시하기 때문.)

## 0. 데이터 (GitHub Actions가 월요일 오전~오후에 미리 수집)
- **모든 데이터는 `https://raw.githubusercontent.com/jiho0282/economy-brief/main/data/weekly/latest.json` 하나에 있다.** 다른 사이트(수출입은행·우리은행·한경)에는 직접 접속하지 않는다.
- `weekMonday`가 이번 주 월요일(YYYYMMDD, 한국시간)과 같은지 확인한다. 다르면 아직 수집 전이므로 5분 간격으로 최대 4번 다시 확인한다(캐시 회피용으로 URL 끝에 `?t=현재시각`).
- `errors`에 들어 있는 항목은 '확인 필요'로 처리하고, 헤더 위에 빨간 검토자용 안내 한 줄씩 넣는다.
- 메일 제목·헤더 제목: `title` 값 그대로. 헤더 오른쪽: 발송일 `YYYY.MM.DD (요일)`.

## 1. 환율 (`exchange`)
- 섹션 오른쪽 날짜: `exchange.dateRangeLabel`.
- `exchange.items`의 USD, JPY(100), EUR 각각: 이번 주 `current`, 변동 `diff`(▲ 빨강 #d0342c / ▼ 파랑 #1f62c9, 0이면 `-`), `전주 {previous}`. 소수 둘째 자리, 천 단위 쉼표.

## 2. 금리 (`rates`)
- 섹션 오른쪽 날짜: `rates.dateRangeLabel`. 표 열 이름: `rates.previousLabel`, `rates.currentLabel`.
- 일반대출 = `rates.generalLoan`, 당좌대출 = `rates.overdraftLoan` (previous/current는 % 소수 둘째 자리, `diffBp`가 0이면 `-`, 양수 `▲ 5bp` 빨강, 음수 `▼ 11bp` 파랑).
- 산식 칸: `formula` 값 그대로.
- 표 아래: `※ 기준금리: 단기대출 {base.shortLoan.previous}% → {current}%, 당좌대출 {base.overdraft.previous}% → {current}%` (소수 둘째 자리).

## 3. 주요 경제뉴스 (`news`)
- 섹션 오른쪽 날짜: `news.dateRangeLabel`.
- `news.selected`의 4개 기사를 순서대로 쓴다(환율 1, 금리 1, 관심 키워드 2는 수집 단계에서 이미 골라 둠). 각 기사의 `body`를 읽고 **직접 쓴 1~2문장 요약**(수치 포함, 원문 문장 복사 금지).
- `body`가 300자 미만이거나 기사 내용과 맞지 않으면 `news.spares`의 기사로 교체한다.
- 태그: `tag` 값. 색은 환율 #e6eefb/#1f62c9, 금리 #fde8e6/#c23a2f, 채권 #eaf5ee/#2b7a4b, 삼성 #efeafb/#5b3fb5, 에너지 #fff4e0/#b26a00, 유가 #fdeee3/#b4501f, 건축 #e9f3f6/#1f6f86, 원자재·금 #f6f0dd/#8a6d1c, 주요 #eef1f5/#4a5462.
- 기사 날짜 `pubDate`, 제목은 `title` 그대로, `기사 원문 →` 링크는 `url`.

## 4. 3분 경제 퀴즈
- 이번 주 기사 내용으로 3문항(객관식). 정답이 기사에 명확히 나온 사실이어야 한다. 최소 1문항은 개념 이해형(왜 그런가).
- 정답 칸: 배경 #e2eaf4, 글자색도 #e2eaf4(드래그해야 보임). 정답마다 한 줄 해설.

## 5. 하단 출처
- 환율: 한국수출입은행 현재환율 API (공휴일로 직전 영업일 값을 쓴 경우 그 사실을 괄호로)
- 금리: 우리은행 대출기준금리표 · 뉴스: 한국경제 모닝루틴 Pick! 오늘의 기사
- 기사 요약과 퀴즈는 Claude가 작성했습니다. 정확한 내용은 원문을 확인해 주세요.

## 6. 검증 후 발송
- 보내기 전 숫자(환율 변동 계산, 금리 가산, bp)를 계산으로 다시 확인한다.
- Gmail로 수신자에게 HTML 메일 발송. 제목은 `title` 값.
