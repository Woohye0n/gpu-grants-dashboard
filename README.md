# GPU 지원사업 트래커

여러 기관이 따로 올리는 **GPU 지원사업 공고**를 하루 한 번 모아, 한 화면에서
"어떤 GPU를 몇 장, 언제까지 신청해서, 언제까지 쓸 수 있는지"를 보여주는 정적 대시보드입니다.

```
docs/index.html   대시보드 (정적 HTML/CSS/JS, 외부 의존성 없음)
docs/data.js      대시보드가 읽는 수집 결과  ← scraper 가 갱신
docs/data.json    같은 내용의 JSON (새로고침 버튼·외부 연동용)
scraper/          수집기
overrides.json    자동 추출이 틀렸을 때 손으로 고치는 파일
update.sh         하루 1회 실행 진입점
daily_loop.sh     cron 이 없는 서버용 데몬 (매일 09:10 KST)
data/history/     날짜별 스냅샷 (공고 변화 추적용)
```

## 보기

```bash
./serve.sh            # http://localhost:8765
```
`docs/index.html` 을 브라우저로 직접 열어도 됩니다(데이터는 `data.js` 로 함께 들어갑니다).

## 수집

```bash
python -m scraper.run              # 전체 수집 (첨부 공고문까지 파싱)
python -m scraper.run --shallow    # 목록만 빠르게
python -m scraper.run --dry-run    # 파일을 쓰지 않고 결과만 출력
./update.sh                        # 위를 로그(logs/update.log)와 함께 실행
```

### 하루 한 번 자동 실행

* **cron 이 있는 서버**
  ```cron
  10 0 * * *  /home/jovyan/data-1/woohyeon/gpu-grants-dashboard/update.sh   # 00:10 UTC = 09:10 KST
  ```
* **cron 이 없는 서버(이 서버가 그렇습니다)**
  ```bash
  setsid nohup ./daily_loop.sh > /dev/null 2>&1 &   # 시작
  ./stop_daily.sh                                    # 중지
  ```
* **GitHub Pages 로 공개** — `.github/workflows/daily.yml` 이 매일 09:10 KST 에 수집하고
  `docs/` 를 Pages 로 배포합니다. 저장소 Settings → Pages → Source 를 **GitHub Actions** 로 두세요.

## 수집 대상

| 사이트 | 게시판 | 읽는 방법 |
|---|---|---|
| 국가 AI컴퓨팅자원 지원포털 (aiinfrahub.kr) | 사업 공고 | 목록이 JS 앱이라 뒤의 `/api/projects` 를 직접 호출하고, 상세의 `.hwp` 첨부를 파싱 |
| 정보통신산업진흥원 NIPA (nipa.kr) | 알림마당 > 사업공고 | 최근 12페이지를 훑어 GPU·고성능컴퓨팅 공고만 추리고, 상세의 `.hwp/.hwpx/.pdf` 첨부를 파싱 |
| 서울 AI 허브 (seoulaihub.kr) | 허브소식 > 공지사항 | 최근 3페이지. 본문에 GPU 표가 그대로 있어 본문 우선, 첨부는 보조 |

새 사이트 추가는 `scraper/sources/` 에 `SOURCE`, `SOURCE_LABEL`, `fetch(deep=True)` 를 가진
모듈을 하나 더 만들고 `scraper/run.py` 의 `SOURCES`/`SOURCE_SITES` 에 등록하면 됩니다.

## 값이 만들어지는 방식

* **관련 공고 선별** — 제목·사업명에 GPU / 고성능컴퓨팅 / AI컴퓨팅 / 연산자원 / H100·H200·A100·B200 …
  가 있으면 수집하고, 입찰·채용·성과공유회·선정결과는 제외합니다.
* **GPU 기종·수량** — 본문과 첨부 공고문 텍스트에서 기종명을 찾고, **그 기종 주변의 수량**만 묶습니다.
  HWP 표는 칸마다 줄이 나뉘므로 `H200` 다음 줄의 `최소 서버 2대(16장)~` 같은 칸도 짝지어 읽습니다.
  카드의 `원문` 에 마우스를 올리면 근거 문장이 그대로 나옵니다.
* **신청 마감** — 게시판이 주는 신청기간을 우선 쓰고, 없으면 본문의 `(모집기간) …` 을 파싱합니다.
* **사용 가능 기간** — `지원기간 / 이용기간 / 사업기간 / 자원 이용` 문장에서 날짜 범위를 뽑고,
  날짜가 없으면(`협약체결일로부터 ~`) 문장을 그대로 보여줍니다.
* **상태** — 제목에 `접수마감`이 있거나 마감일이 지났으면 마감, 시작 전이면 예정, 그 외 모집중.
  마감 7일 이내면 상단에 배너가 뜹니다.
* **중복 공고** — 같은 사업이 두 사이트에 올라오면(예: 첨단 GPU 활용 지원 사업 = aiinfrahub + NIPA)
  사업명·차수·대상(산업계/산학연)을 비교해 한 카드로 합치고 링크는 둘 다 답니다.

정규식 기반 추출이라 **틀릴 수 있습니다.** 카드에 늘 원문 링크와 근거 문장을 같이 두는 이유입니다.
틀린 값은 `overrides.json` 에서 uid 로 덮어쓰면 다음 수집부터 반영됩니다.

## 한계

* `.hwp` 는 본문 텍스트만 읽습니다. 이미지로 넣은 표(스캔 공고문)는 읽지 못합니다.
* NIPA 는 최근 12페이지, 서울 AI 허브는 3페이지만 봅니다
  (`scraper/sources/*.py` 의 `PAGES` 로 조정).
* 이 서버는 일부 사이트의 인증서 체인이 끊겨 있어 `aiinfrahub.kr` 은 검증 실패 시 1회 우회합니다
  (`scraper/common.py` 의 `_NO_VERIFY`). 다른 망에서는 그대로 검증합니다.
