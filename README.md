# AIDAS 대시보드 — GPU 지원사업 · AI 사용량

정적 사이트 하나에 두 화면이 들어 있습니다. 상단에서 서로 오갑니다.

| 화면 | 경로 | 내용 | 데이터 출처 |
|---|---|---|---|
| **GPU 지원사업** | `/` | 기관별 GPU 지원사업 공고 — 기종·수량·마감·사용기간 | 이 저장소가 직접 수집 (하루 1회) |
| **AI 사용량** | `/ai/` | 연구실 Claude Code·Codex 토큰 사용량 | 중앙 서버가 발행하는 스냅샷 (5분 주기) |

아래 문서는 **GPU 지원사업** 쪽 이야기입니다. AI 사용량 화면은 맨 아래 "AI 사용량 화면" 절을 보세요.

---

## GPU 지원사업

여러 기관이 따로 올리는 **GPU 지원사업 공고**를 하루 한 번 모아, 한 화면에서
"어떤 GPU를 몇 장, 언제까지 신청해서, 언제까지 쓸 수 있는지"를 보여줍니다.

```
docs/index.html   GPU 지원사업 화면 (정적 HTML/CSS/JS, 외부 의존성 없음)
docs/ai/          AI 사용량 화면 (index.html · app.js · style.css · vendor/chart.umd.min.js)
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

### 사이트 추가

수집 대상은 `sources.json` 이 정합니다. 두 종류가 있습니다.

* `kind: builtin` — 손으로 짠 모듈(위 세 곳). 사이트별 별난 점(JSON API, 첨부 다운로드 방식)을 다룹니다.
* `kind: generic` — **설정만으로 도는 범용 수집기.** `scraper/probe.py` 가 '같은 모양의 행이 반복되고
  각 행에 제목과 날짜가 있다'는 게시판 구조를 찾아 읽습니다. `<a href>` 목록도, `goDetail('...')`
  처럼 자바스크립트로 글을 여는 목록도 됩니다(글 번호는 *행마다 값이 달라지는 인자*로 찾습니다).

붙이기 전에 읽히는지 먼저 확인하세요:

```bash
python -m scraper.probe "https://example.kr/board/list"
```

**대시보드 "수집 상태" 탭의 폼**으로도 추가할 수 있습니다. 이름과 목록 URL 을 넣고 `GitHub 이슈로
등록` 을 누르면 `[사이트 추가]` 이슈가 열리고, `add-source` 워크플로가 **그 주소를 실제로 읽어본 뒤**
`sources.json` 에 넣고 수집을 바로 돌립니다. 못 읽으면 추가하지 않고 이유를 이슈에 답니다.
이슈는 **저장소 소유자가 연 것만** 반영되고, 내부/사설 IP 로 해석되는 주소는 거부합니다.

손으로 짠 모듈이 필요할 만큼 특이한 사이트라면 `scraper/sources/` 에 `SOURCE`, `SOURCE_LABEL`,
`fetch(deep=True)` 를 가진 모듈을 만들고 `scraper/run.py` 의 `BUILTIN` 에 등록한 뒤
`sources.json` 에 `kind: builtin` 으로 적으면 됩니다.

## 값이 만들어지는 방식

* **관련 공고 선별** — 제목·사업명에 GPU / 고성능컴퓨팅 / AI컴퓨팅 / 연산자원 / H100·H200·A100·B200 …
  가 있으면 수집하고, 입찰·채용·성과공유회·선정결과는 제외합니다.
* **GPU 기종·수량** — 본문과 첨부 공고문 텍스트에서 기종명을 찾고, **그 기종 주변의 수량**만 묶습니다.
  HWP 표는 칸마다 줄이 나뉘므로 `H200` 다음 줄의 `최소 서버 2대(16장)~` 같은 칸도 짝지어 읽습니다.
  카드의 `원문` 에 마우스를 올리면 근거 문장이 그대로 나옵니다.
* **비용** — 공고는 지원 대상별로 부담금을 따로 적습니다(예: 첨단 GPU 사업은 산업계 유상 / 학계·연구계 무상).
  줄 앞의 `(산업계)` `(학계·연구계)` 라벨과, 라벨이 없어도 `대학교(원)…는 자부담금 부여 대상에서 제외`
  같은 문장을 대상별로 묶어 **학계를 맨 위**에 두고 나머지도 함께 보여줍니다.
* **신청 마감** — 게시판이 주는 신청기간을 우선 쓰고, 없으면 본문의 `(모집기간) …` 을 파싱합니다.
* **사용 가능 기간** — `지원기간 / 이용기간 / 사업기간 / 자원 이용` 문장에서 날짜 범위를 뽑고,
  날짜가 없으면(`협약체결일로부터 ~`) 문장을 그대로 보여줍니다.
* **상태·D-day** — **브라우저가 볼 때마다 다시 계산합니다.** 수집 시점에 계산해 JSON 에 박아두면
  수집이 멈춘 날 카운트다운도 같이 멈춰서 낡은 데이터가 멀쩡해 보입니다(2026-09-12 에 실제로 그랬습니다).
  마지막 수집이 2일 이상 지나면 상단에 그 사실을 띄웁니다.
* **중복 공고** — 같은 사업이 두 사이트에 올라오면(예: 첨단 GPU 활용 지원 사업 = aiinfrahub + NIPA)
  사업명·차수·대상(산업계/산학연)을 비교해 한 카드로 합치고 링크는 둘 다 답니다.

* **모르는 기종 알림** — 기종 이름은 `scraper/extract.py` 의 `GPU_MODELS` 목록 기반이라, 새 칩
  (B300·GB300·MI355X 같은)이 나오면 못 잡습니다. 그래서 "가속기 이름 같은데 목록에 없는" 토큰을
  따로 찾아 **"수집 상태" 탭과 수집 로그에 띄웁니다.** 한 줄 추가하면 다음 수집부터 잡힙니다.
  (실제 첨부문서 300여 개에 돌려 오탐 0건, 가짜 공고문으로 B300/GB300/MI355X/R100 탐지 확인)

정규식 기반 추출이라 **틀릴 수 있습니다.** 카드에 늘 원문 링크와 근거 문장을 같이 두는 이유입니다.
틀린 값은 `overrides.json` 에서 uid 로 덮어쓰면 다음 수집부터 반영됩니다.

## 수집 실패를 어떻게 다루나

* **직전 데이터 유지** — 사이트를 못 읽으면 `data/last_good/` 의 마지막 성공분을 그대로 쓰고,
  카드·배너·"수집 상태" 탭에 그 사실을 표시합니다. 화면이 비지 않습니다.
* **종료 코드로 저하와 고장을 나눕니다** — `0` 전부 정상 / `1` 일부가 직전 데이터로 대체(저하)
  / `2` 대비책도 없이 못 가져옴(고장). 워크플로는 **`2` 일 때만 실패로 남깁니다.**
  둘을 같게 두면 매일 빨간불이 켜져서, 정작 고장났을 때 알아챌 수 없습니다.
* **알려진 제약** — `sources.json` 의 `ci_blocked: true` 인 사이트는 러너에서 막히는 것이
  이미 확인된 곳이라, 실패가 아니라 "사이트가 자동수집 차단" 으로 표시합니다.
  현재 AICA(자동등록방지 페이지)와 KISTI(해외 IP 연결 차단)가 그렇습니다.

## 붙일 수 없는 사이트

* **자동 수집을 막는 사이트** — 사람 확인(자동등록방지) 페이지를 내려주면 그렇다고 알리고 멈춥니다.
  사이트가 의도적으로 세운 접근 통제라 우회하지 않습니다.
* **해외 IP 를 막는 사이트** — GitHub 러너에서 연결 자체가 안 됩니다 (`kisti.re.kr`).
  다만 같은 기관의 `ksc.re.kr` 게시판(`/notice/gjsh/gjsh/include/list`)은 러너에서도 열리고,
  6호기「한강」공고는 두 곳에 함께 올라와 병합되므로 KSC 쪽에서 갱신됩니다.
* **목록을 자바스크립트로만 그리는 사이트** — 서버가 주는 HTML 에 글이 없습니다
  (예: `aidc.atops.or.kr`, 880자짜리 껍데기).

## 한계

* `.hwp`(157/157) · `.hwpx`(127/127) · `.pdf`(27/28) 는 본문 텍스트를 읽습니다. 실패한 1건은
  **스캔 이미지 PDF** 로 텍스트 레이어가 없습니다 — 읽으려면 OCR(tesseract)이 필요합니다.
  `.docx` 는 아직 지원하지 않습니다(공공기관 공고에서 거의 안 쓰여 넣지 않았습니다).
* NIPA 는 최근 12페이지, 서울 AI 허브는 3페이지만 봅니다
  (`scraper/sources/*.py` 의 `PAGES` 로 조정).
* 이 서버는 일부 사이트의 인증서 체인이 끊겨 있어 `aiinfrahub.kr` 은 검증 실패 시 1회 우회합니다
  (`scraper/common.py` 의 `_NO_VERIFY`). 다른 망에서는 그대로 검증합니다.


---

## AI 사용량 화면 (`/ai/`)

연구실의 Claude Code·Codex 토큰 사용량 대시보드입니다. 원래
[`AIDASLab/aidas-ai-monitoring-dashboard`](https://github.com/AIDASLab/aidas-ai-monitoring-dashboard)
에 있던 화면을 이 사이트로 옮겨, 한 주소에서 GPU 공고와 함께 보게 했습니다.

### 데이터가 흐르는 길

```
GPU 서버들 (ai-monitoring-send)   각 서버에 설치되는 송신 에이전트
   │ scp → NAS inbox
   ▼
중앙 서버 ADS-A100                monitoring.db + backend (별도 저장소, 비공개)
   │ publish.py (cron 5분)
   ▼
AIDASLab/aidas-ai-monitoring-dashboard   data/dashboard.json 스냅샷
   │
   ▼
이 사이트 /ai/                    프론트만 이식. 데이터는 위에서 읽어온다
```

**수집 파이프라인은 그대로 두었습니다.** 중앙 서버의 cron도, 발행 경로도 건드리지
않았습니다. 이식한 것은 화면(프론트)뿐입니다.

### 데이터를 어디서 읽나 — 2단계

`docs/ai/app.js` 가 순서대로 시도합니다.

1. `raw.githubusercontent.com/AIDASLab/aidas-ai-monitoring-dashboard/<SHA>/data/dashboard.json`
   — `main` 을 커밋 SHA 로 풀어 불변 URL 로 받습니다(브랜치 URL 은 몇 분간 캐시됨).
2. 실패하면 `./data/dashboard.json` — 이 저장소에 함께 두는 사본

사본은 수집 워크플로가 매일 갱신합니다(`scraper/sync_ai_snapshot.py`).

```bash
python3 -m scraper.sync_ai_snapshot                 # 공개 저장소
GH_TOKEN=<토큰> python3 -m scraper.sync_ai_snapshot  # 비공개로 바뀐 뒤
```

> **원본 저장소를 private 으로 돌리면** 브라우저는 1번 경로를 읽지 못합니다(인증이
> 없으므로). 그때는 2번 사본이 **유일한 데이터원**이 되고, 신선도는 하루 1회가 됩니다.
> 워크플로가 사본을 계속 받아오려면 저장소 시크릿 `AI_SNAPSHOT_TOKEN` 에
> 원본 저장소 `contents:read` 권한의 토큰을 넣어 주세요.

### 왜 한 페이지로 합치지 않았나

두 화면이 CSS 클래스 이름 **29개**를 공유하면서(`card` `cards` `chip` `panel` `tab`
`badge` `banner` `btn` `kv` …) 규칙은 서로 다릅니다. GPU 화면이 이 대시보드의 디자인
시스템을 그대로 가져다 썼기 때문입니다. 한 페이지에 두 CSS 를 올리면 서로를 덮습니다.

서브페이지로 나누면 충돌이 **0** 이고, CSS 변수 11개(`--bg` `--panel` `--accent` …)가
이미 값까지 같아서 두 화면이 한 사이트로 읽힙니다. 나중에 한 페이지로 합치고 싶으면
그때 클래스에 접두사를 붙여 옮기면 됩니다.
