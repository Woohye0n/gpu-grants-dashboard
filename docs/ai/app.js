"use strict";

// =====================================================================
// Static GitHub Pages build of the AIDAS monitoring dashboard.
// Instead of hitting a live backend (/api/*), it reads ONE static
// snapshot file (./data/dashboard.json) that the central server
// publishes periodically (see publish.py). All rendering below is
// identical to the live dashboard.
// =====================================================================

// ---- helpers --------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function fmt(n) {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n));
}
const fmtFull = (n) => (n || 0).toLocaleString();
function ago(ms) {
  if (!ms) return "—";
  const s = (Date.now() - ms) / 1000;
  if (s < 0) return "방금";
  if (s < 60) return Math.floor(s) + "초 전";
  if (s < 3600) return Math.floor(s / 60) + "분 전";
  if (s < 86400) return Math.floor(s / 3600) + "시간 전";
  return Math.floor(s / 86400) + "일 전";
}
function dt(ms) {
  if (!ms) return "—";
  return new Date(ms).toLocaleString("ko-KR", { hour12: false });
}
function dur(a, b) {
  if (!a || !b) return "—";
  let s = Math.max(0, (b - a) / 1000);
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  return (h ? h + "시간 " : "") + m + "분";
}
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function pctClass(pct) {
  if (pct >= 90) return "crit";
  if (pct >= 70) return "warn";
  return "";
}

// ---- static data source ---------------------------------------------------
// 화면 전체가 스냅샷 하나로 굴러간다. api() 는 그 스냅샷을 예전 라이브 대시보드의
// /api/* 모양으로 돌려주는 얇은 껍데기다.
//
// 읽는 곳은 **같은 출처의 이 파일 하나뿐**이다. 예전에는 브라우저가 스냅샷을 발행하는
// 다른 저장소(raw.githubusercontent + GitHub API)를 직접 읽었다. 그러면 이 사이트가 그
// 저장소 없이는 돌지 않고, 무인증 API 한도(시간당 60회 — 연구실이 IP 하나를 쓰니 약 2명)
// 에 걸리면 탭마다 옛 커밋에 고정되기까지 했다. 받아오는 일은 이제 서버에서
// scraper/sync_ai_snapshot.py 가 하고, 그 결과가 data/sync.json 에 남는다.
const DATA_URL = "./data/dashboard.json";
const SYNC_URL = "./data/sync.json";
// 중앙 서버는 5분 주기로 발행한다. 이만큼 지나면 멈춘 것으로 보고 배너를 띄운다.
const STALE_AFTER_MS = 30 * 60 * 1000;
let BUNDLE = null;
let SYNC = null;

async function loadBundle() {
  const res = await fetch(DATA_URL + "?t=" + Date.now(), { cache: "no-store" });
  if (!res.ok) {
    const err = new Error(DATA_URL + " -> " + res.status);
    // 공개 배포본에는 이 파일을 일부러 넣지 않는다 — 랩 구성원 이름과 작업 경로가
    // 들어 있기 때문이다. "고장" 이 아니라 "여기서는 안 본다" 라고 말해야 한다.
    err.absent = res.status === 404;
    throw err;
  }
  BUNDLE = await res.json();
  // 동기화 기록은 있으면 쓰고 없으면 만다 — 배포본에 따라 없을 수 있다.
  try {
    const r = await fetch(SYNC_URL + "?t=" + Date.now(), { cache: "no-store" });
    SYNC = r.ok ? await r.json() : null;
  } catch (e) { SYNC = null; }
  return BUNDLE;
}

async function api(path) {
  if (!BUNDLE) await loadBundle();
  if (path === "/api/summary") return BUNDLE.summary || {};
  if (path === "/api/sessions") return { sessions: BUNDLE.sessions || [] };
  if (path === "/api/alerts") return { alerts: BUNDLE.alerts || [] };
  if (path === "/api/config") return BUNDLE.config || {};
  if (path.indexOf("/api/usage/timeseries") === 0) {
    const w = (path.split("window=")[1] || "1d").split("&")[0];
    return (BUNDLE.timeseries && BUNDLE.timeseries[w]) || { window: w, bucket: 60, series: {} };
  }
  throw new Error("no static data for " + path);
}

// ---- tooltip (cursor-following; works on masked donuts & inside scroll areas) ----
let _tipEl = null;
function _ensureTip() {
  if (!_tipEl) { _tipEl = document.createElement("div"); _tipEl.className = "tip"; document.body.appendChild(_tipEl); }
  return _tipEl;
}
function _moveTip(e) {
  const t = _tipEl; if (!t) return;
  let x = e.clientX + 14, y = e.clientY + 16;
  const r = t.getBoundingClientRect();
  if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - 12;
  if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - 12;
  t.style.left = x + "px"; t.style.top = y + "px";
}
function setupTooltip() {
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el) return;
    const t = _ensureTip();
    t.textContent = el.getAttribute("data-tip");
    t.style.display = "block";
    _moveTip(e);
  });
  document.addEventListener("mousemove", (e) => {
    if (_tipEl && _tipEl.style.display === "block") _moveTip(e);
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest("[data-tip]") && _tipEl) _tipEl.style.display = "none";
  });
}

// ---- state ----------------------------------------------------------------
const state = {
  // 이 화면을 여는 이유는 대개 "누가 얼마나 썼나" 다. 계정·한도는 그 다음이라
  // 개인별 사용량을 첫 화면으로 둔다(index.html 의 active 탭과 맞춰야 한다).
  tab: "charts",
  metric: "total",
  summary: null,
  sessions: [],
  timer: null,
  chart: null,
  // 열려 있는 개인 상세 창 { owner, w }. 30초 자동 새로고침 뒤에도 같은 창을 다시 그린다.
  detail: null,
};

// ---- tabs -----------------------------------------------------------------
function switchTab(name) {
  state.tab = name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + name));
  render();
}

// ---- overview -------------------------------------------------------------
function renderTotals(s) {
  const m = state.metric;
  const row = $("#totalsRow");
  const cards = [];
  cards.push(`<div class="totalcard"><div class="label">라이브 세션</div>
    <div class="value">${s.live_session_count}</div>
    <div class="small">프로세스 실행 중</div></div>`);
  for (const w of s.windows) {
    const t = s.totals[w] || {};
    cards.push(`<div class="totalcard"><div class="label">전체 합계 · ${w} · ${m}</div>
      <div class="value">${fmt(t[m])}</div>
      <div class="small">${fmtFull(t[m])} 토큰 · 메시지 ${fmtFull(t.messages)}</div></div>`);
  }
  row.innerHTML = cards.join("");
}

const RL_LABEL = {
  five_hour: "5시간", seven_day: "주간 (전체)",
  seven_day_fable: "주간 (Fable)",
  seven_day_opus: "주간 (Opus)", seven_day_sonnet: "주간 (Sonnet)",
};
function shortReset(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("ko-KR",
      { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  } catch (e) { return ""; }
}
// nodes (sender --host) this account has been seen on; live ones marked
function nodesHTML(a) {
  const hosts = a.hosts || [];
  const live = new Set(a.live_hosts || []);
  if (!hosts.length) return '<span class="hint">아직 없음</span>';
  return hosts.map((h) =>
    `<span class="chip ${live.has(h) ? "nodelive" : ""}">${esc(h)}${live.has(h) ? " ●" : ""}</span>`).join(" ");
}

// gauge for a REAL utilization threshold (value & limit are percentages 0-100)
function gaugeHTML(th) {
  const label = RL_LABEL[th.window] || th.window;
  if (!th.available || th.value == null) {
    return `<div class="gauge"><div class="gauge-top">
        <span class="w">${label} <span class="hint">한도 ${th.limit}%</span></span>
        <span class="v hint">데이터 없음</span></div>
      <div class="bar"><span style="width:0%"></span></div></div>`;
  }
  const val = th.value;
  const cls = val >= th.limit ? "crit" : (val >= th.limit * 0.75 ? "warn" : "");
  const reset = th.resets_at ? `리셋 ${shortReset(th.resets_at)}` : "";
  return `<div class="gauge">
    <div class="gauge-top"><span class="w">${label} <span class="hint">경고 ${th.limit}%</span></span>
      <span class="v">${val.toFixed(0)}%</span></div>
    <div class="bar"><span class="${cls}" style="width:${Math.min(100, val)}%"></span>
      <i class="limitmark" style="left:${Math.min(100, th.limit)}%"></i></div>
    <div class="meta"><span>${val.toFixed(1)}% 사용</span><span>${reset}</span></div>
  </div>`;
}

function renderAccounts(s) {
  const m = state.metric;
  const box = $("#accounts");
  if (!s.accounts.length) {
    box.innerHTML = `<div class="empty">아직 수집된 계정 데이터가 없습니다.</div>`;
    return;
  }
  box.innerHTML = s.accounts.map((a) => {
    const status = a.status || "ok";
    const suspended = status === "suspended";
    const authErr = !suspended && status === "auth_error";
    const stale = !!a.usage_stale;
    // A frozen (suspended/stale) percentage must not keep screaming "임박".
    const breach = !suspended && !stale && a.thresholds.some((t) => t.breached);
    const hasRL = a.rate_limits && (a.rate_limits.five_hour || a.rate_limits.seven_day);
    const gauges = a.thresholds.length
      ? a.thresholds.map(gaugeHTML).join("")
      : `<div class="nolimit">설정된 한도 임계치가 없습니다.</div>`;
    const api = hasRL && a.rate_limits.source === "claude_oauth"
      ? "Anthropic usage API" : "Codex rollout 기준";
    let src;
    if (suspended)
      src = hasRL ? `⛔ 계정 정지됨 — 마지막 성공 조회 ${ago(a.usage_updated_at)} (${api})`
                  : "⛔ 계정 정지됨 — 사용량 조회 불가";
    else if (authErr)
      src = `⚠ 사용량 API 인증 실패 (정지/로그아웃?) — 마지막 성공 조회 ${ago(a.usage_updated_at)}`;
    else if (stale)
      src = `⚠ 오래된 데이터 — 마지막 성공 조회 ${ago(a.usage_updated_at)} (${api})`;
    else if (!hasRL)
      src = "실제 사용량 데이터 없음 (아직 수집 전이거나 미사용)";
    else
      src = `실제 한도 · ${api} · ${ago(a.usage_updated_at)}`;
    const statusTag = suspended ? '<span class="tag suspended">SUSPENDED</span>'
      : authErr ? '<span class="tag autherr">인증 오류</span>'
      : stale ? '<span class="tag staletag">스테일</span>' : "";
    const tok = s.windows.map((w) =>
      `<span class="tokchip">${w} ${fmt(a.windows[w][m])}</span>`).join("");
    return `<div class="card ${breach ? "breach" : ""}${suspended ? " suspended" : ""}">
      <div class="acct-head">
        <span class="email">${esc(a.email)}</span>
        <span>${statusTag}${breach ? ' <span class="tag" style="color:var(--red)">한도 임박</span>' : ""}</span>
      </div>
      <div class="chips">
        <span class="chip prov ${esc(a.provider || "claude")}">${esc(a.provider || "claude")}</span>
        ${a.rate_limit_tier ? `<span class="chip">${esc(a.rate_limit_tier)}</span>` : ""}
        ${a.org_type ? `<span class="chip max">${esc(a.org_type)}</span>` : ""}
      </div>
      <div class="nodes">노드: ${nodesHTML(a)}</div>
      <div class="usagehead">실제 사용 한도</div>
      <div class="${suspended || stale ? "gauges dim" : "gauges"}">${gauges}</div>
      <div class="src">${src}</div>
      <div class="tokrow">참고 토큰량 (${m}): ${tok} · 누적 ${fmt(a.lifetime[m])}</div>
    </div>`;
  }).join("");
}

function renderBanner(s) {
  const breaches = [];
  for (const a of s.accounts) {
    // suspended/stale accounts carry frozen percentages — not a live breach
    if (a.status === "suspended" || a.usage_stale) continue;
    for (const t of a.thresholds)
      if (t.breached) breaches.push(`${a.email} · ${RL_LABEL[t.window] || t.window} ${(t.value || 0).toFixed(0)}% ≥ ${t.limit}%`);
  }
  const b = $("#breachBanner");
  if (breaches.length) {
    b.classList.remove("hidden");
    b.textContent = "⚠ 한도 초과: " + breaches.join("  |  ");
  } else b.classList.add("hidden");
}

// 스냅샷이 늙으면 배너로 말한다 — 그리고 **어느 쪽이 멈췄는지**까지 말한다.
// 숫자만 보면 멀쩡해 보이는데 며칠 전 것일 수 있고, 그때 고칠 곳은 이 서버의 집계와
// 각 서버의 송신기 중 하나다. 둘을 구분해 주지 않으면 화면이 거짓말을 하는 셈이다.
// (실제로 발행이 4.5일 멈춘 동안 화면은 아무 말도 하지 않았다.)
function renderStale() {
  const b = $("#staleBanner");
  const gen = BUNDLE && BUNDLE.generated_at;
  if (gen && Date.now() - gen < STALE_AFTER_MS) { b.classList.add("hidden"); return; }
  const lines = [gen
    ? `데이터가 ${ago(gen)} 기준입니다 — 자동 갱신이 멈춰 있습니다.`
    : "스냅샷에 생성 시각이 없습니다."];
  if (!SYNC)
    lines.push("집계 기록이 없습니다 — 이 배포본은 사내 수집 서버가 아닙니다.");
  else if (!SYNC.ok)
    lines.push(`이 서버가 집계를 만들지 못하고 있습니다 — ${esc(SYNC.error || "원인 미상")}`);
  else if (SYNC.checked_at && Date.now() - SYNC.checked_at > STALE_AFTER_MS)
    lines.push(`집계 자체가 ${ago(SYNC.checked_at)}부터 돌지 않았습니다 — daily_loop / cron 을 확인하세요.`);
  else
    lines.push(`집계는 ${ago(SYNC.checked_at)}에 정상이었습니다 — 각 서버의 송신기가 새 기록을 보내지 않고 있습니다.`);
  b.innerHTML = lines.map((t) => `<div>${t}</div>`).join("");
  b.classList.remove("hidden");
}

// ---- live + sessions ------------------------------------------------------
// small pie showing what % of the real 5h/weekly limit this session used
function donut(pct, label) {
  if (pct == null)
    return `<span class="donut none" data-tip="${label} 한도: 데이터 없음"></span>`;
  const c = pct >= 60 ? "var(--red)" : (pct >= 40 ? "var(--yellow)" : "var(--green)");
  const p = Math.max(0, Math.min(100, pct));
  return `<span class="donut" style="--p:${p};--c:${c}" `
    + `data-tip="${label} 한도의 ${pct.toFixed(2)}% 를 이 세션이 사용 (추정)"></span>`;
}

function sessionRows(list) {
  if (!list.length) return `<div class="empty">표시할 세션이 없습니다.</div>`;
  const m = state.metric;
  const rows = list.map((x) => {
    const dotCls = x.live ? (x.status === "busy" ? "live" : "idle") : "ended";
    const statusTag = x.live
      ? `<span class="tag ${x.status === "busy" ? "busy" : "idle"}">${x.status || "live"}</span>`
      : `<span class="tag">ended</span>`;
    const sid = x.session_id ? x.session_id.slice(0, 8) : "—";
    const dir = x.cwd || "—";
    return `<tr>
      <td><span class="dot ${dotCls}"></span>${statusTag}</td>
      <td class="sesscell" data-tip="${esc(x.cwd || "")}\n세션 ${esc(x.session_id || "")}">
        <div class="dir">${esc(dir)}</div>
        <div class="sid">${esc(sid)}</div>
      </td>
      <td><span class="tag prov ${esc(x.provider || "claude")}">${esc(x.provider || "claude")}</span> ${esc(x.account_email || "—")}</td>
      <td>${x.owner ? esc(x.owner) : '<span class="hint">공용</span>'}</td>
      <td class="num tokcell">${fmt(x.metrics[m])}<span class="donuts">${x.provider === "codex" ? donut(x.share_7d, "주간") : donut(x.share_5h, "5시간") + donut(x.share_7d, "주간")}</span></td>
      <td class="num">${fmtFull(x.messages)}</td>
      <td>${(x.models || []).map((md) => `<span class="tag">${esc(md)}</span>`).join(" ") || "—"}</td>
      <td>${ago(x.updated_at || x.last_ts)}</td>
      <td>${dur(x.started_at || x.first_ts, x.updated_at || x.last_ts)}</td>
    </tr>`;
  }).join("");
  return `<table><thead><tr>
    <th>상태</th><th>세션 · 디렉토리</th><th>계정</th><th>담당자</th>
    <th class="num">${m}</th><th class="num">메시지</th><th>모델</th><th>최근활동</th><th>지속</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

function renderLive() {
  const live = state.sessions.filter((s) => s.live);
  $("#liveSessions").innerHTML = sessionRows(live);
}
function renderSessions() {
  const q = ($("#sessionFilter").value || "").toLowerCase();
  const list = state.sessions.filter((s) =>
    !q || [s.project, s.session_id, s.account_email, s.cwd, s.host]
      .some((v) => (v || "").toLowerCase().includes(q)));
  $("#sessionsTable").innerHTML = sessionRows(list);
}

// ---- charts: 계정별 한도 사용률 도넛 -------------------------------------
// 시계열(호스트별)을 대체합니다. 한도는 계정 단위 개념이라 호스트로 쪼개는 것보다
// "이 계정이 5시간/주간 한도를 얼마나 썼나"가 실제로 필요한 정보입니다.
let donutCharts = [];
// ---- charts: 사람별 사용량 -------------------------------------------------
// 카드 하나 = 사람 하나. 도넛 두 개(5시간·주간)를 나란히 두고, 조각은 계정 x
// 도구(claude/codex)로 나눕니다. 사람에게는 한도가 없으므로 %는 그 사람 사용량의
// 구성비이며, 한도 소진율은 계정 속성이라 개요 탭의 계정 카드에 있습니다.
//
// 값은 토큰 합계가 아니라 **크레딧**입니다(scraper/ai_pricing.py). 토큰 1개의 값은
// 종류에 따라 수백 배 차이가 나서 — Opus 5.5 캐시 읽기 $0.20, 출력 $20 /1M — 토큰을
// 1:1 로 더한 합계는 대개 '캐시를 얼마나 다시 읽었나' 만 보여 줍니다. 크레딧은 각
// 토큰을 그 종류·모델·속도의 공식 API 단가로 환산해 더한 값이고 1 크레딧 = $10 입니다.
// 창은 지금부터 거꾸로 센 롤링 창입니다(scraper/ai_bundle.py window_start).

function fmtCredit(c) {
  c = c || 0;
  if (c >= 100) return c.toFixed(0);
  if (c >= 10) return c.toFixed(1);
  if (c >= 0.01) return c.toFixed(2);
  return c > 0 ? "<0.01" : "0";
}
const fmtPct = (p) => (p > 0 && p < 0.1 ? "<0.1" : (p || 0).toFixed(1)) + "%";
// 토큰 종류 — 단가가 서로 다른 칸. 같은 '캐시 쓰기' 도 TTL 에 따라 단가가 다르다.
const COMPONENT_LABEL = {
  input: "입력",
  output: "출력",
  cache_read: "캐시 읽기",
  cache_write: "캐시 쓰기",
  cache_write_5m: "캐시 쓰기 (5분)",
  cache_write_1h: "캐시 쓰기 (1시간)",
  cache_write_ttl_unknown: "캐시 쓰기 (TTL 미상)",
};
const SPEED_LABEL = { standard: "표준", fast: "fast ×2", flex: "flex ×0.5" };
// 단가 표기: $0.20 · $0.025 처럼 의미 있는 자리까지만.
const fmtRate = (r) => "$" + (Math.round((r || 0) * 100) === (r || 0) * 100
  ? (r || 0).toFixed(2) : String(+(r || 0).toFixed(4)));
const PA_COLORS = ["#3fb950", "#58a6ff", "#d29922", "#bc8cff", "#f85149",
                   "#39c5cf", "#db6d28", "#8b949e"];
const CHART_WINDOWS = [
  { key: "5h", label: "5시간" },
  { key: "7d", label: "주간" },
];

// 색은 (도구, 계정) 조합에 고정한다. 카드마다 인덱스로 칠하면 같은 초록이 어떤
// 카드에서는 lab1, 다른 카드에서는 lab2 를 뜻하게 되어 비교가 불가능해진다.
let paColorMap = {};
const paKey = (b) => `${b.provider || "?"}|${b.account_email || ""}`;
const paColor = (b) => paColorMap[paKey(b)] || PA_COLORS[PA_COLORS.length - 1];
const paLabel = (b) =>
  `${b.provider || "?"} · ${(b.account_email || "").split("@")[0] || "계정 없음"}`;

function buildPaColors(people) {
  const keys = new Set();
  people.forEach((p) => CHART_WINDOWS.forEach((w) =>
    ((p.breakdown || {})[w.key] || []).forEach((b) => keys.add(paKey(b)))));
  paColorMap = {};
  [...keys].sort().forEach((k, i) => { paColorMap[k] = PA_COLORS[i % PA_COLORS.length]; });
}

function renderCharts() {
  const s = state.summary;
  const box = $("#chartArea");
  donutCharts.forEach((c) => { try { c.destroy(); } catch (e) { /* ignore */ } });
  donutCharts = [];
  const people = (s && s.people) || [];
  if (!people.length) {
    box.innerHTML = `<div class="empty">담당자 데이터가 없습니다.
      설정 탭의 people 규칙(cwd 기준)을 확인하세요.</div>`;
    return;
  }
  buildPaColors(people);
  // 크레딧은 스냅샷을 만드는 집계(scraper/ai_bundle.py)가 계산해 싣는다. 화면은 main
  // 푸시로 바로 배포되지만 스냅샷은 사내 서버가 새 코드를 받아야 바뀌므로, 그 사이에는
  // 크레딧이 없는 옛 스냅샷이 온다. 그때 크레딧만 찾으면 전원이 '사용 없음' 으로 보인다
  // (실제로 그 조합을 띄워 보니 도넛 32개가 전부 비었다). 없으면 예전처럼 토큰으로 보인다.
  const hasCredits = !!(s && s.credit);
  const m = hasCredits ? "credits" : state.metric;
  const fmtV = hasCredits ? fmtCredit : fmt;
  const pending = [];

  const cards = people.map((p, pi) => {
    // 두 창에 등장하는 계정을 합쳐 한 줄씩 보여준다 (한쪽만 있어도 빠지지 않게)
    const seen = new Map();
    CHART_WINDOWS.forEach((w) => ((p.breakdown || {})[w.key] || []).forEach((b) => {
      const e = seen.get(paKey(b)) || { ...b, vals: {} };
      e.vals[w.key] = (e.vals[w.key] || 0) + (b[m] || 0);
      seen.set(paKey(b), e);
    }));
    const legendRows = [...seen.values()]
      .filter((e) => CHART_WINDOWS.some((w) => (e.vals[w.key] || 0) > 0))
      .sort((a, b) => (b.vals["7d"] || 0) - (a.vals["7d"] || 0));

    const cells = CHART_WINDOWS.map((w) => {
      const parts = ((p.breakdown || {})[w.key] || []).filter((b) => (b[m] || 0) > 0);
      const total = parts.reduce((a, b) => a + (b[m] || 0), 0);
      const id = `dn-${pi}-${w.key}`;
      if (total > 0) pending.push({ id, parts, m, total, hasCredits });
      const face = total > 0
        ? `<canvas id="${id}" width="104" height="104"></canvas>`
        : `<div class="dnut-none">사용 없음</div>`;
      return `<div class="dnut">${face}<div class="dnut-cap">${w.label}</div></div>`;
    }).join("");

    const legend = legendRows.map((e) => `<div class="pa-row">
        <span class="pa-dot" style="background:${paColor(e)}"></span>
        <span class="pa-name">${esc(paLabel(e))}</span>
        <span class="pa-tok">${fmtV(e.vals["5h"] || 0)}</span>
        <span class="pa-tok">${fmtV(e.vals["7d"] || 0)}</span>
      </div>`).join("");
    const live = p.live_sessions
      ? `<span class="chip live">라이브 ${p.live_sessions}</span>` : "";
    // 단가를 몰라 크레딧에서 빠진 토큰이 있으면 카드에서부터 알린다 — 0 으로 숨기면
    // 비싼 신모델을 쓴 사람이 오히려 적게 쓴 것처럼 보인다.
    const unpriced = (((p.credit_detail || {})["7d"] || {}).unpriced || [])
      .reduce((a, u) => a + (u.tokens || 0), 0);
    const warn = unpriced
      ? `<div class="hint pa-warn">단가 미등록 ${fmt(unpriced)} 토큰은 크레딧에서 제외</div>` : "";
    // 단가표가 '거의 0' 으로 둔 모델(예: codex-auto-review — 자동 리뷰)은 경고가 아니라
    // 정체만 밝힌다. 토큰은 많아 보여도 비용으로 읽으면 안 되는 사용이다.
    const noted = new Map();
    ((((p.credit_detail || {})["7d"] || {}).groups) || []).filter((g) => g.note)
      .forEach((g) => noted.set(g.model, (noted.get(g.model) || 0) + (g.tokens || 0)));
    const notes = [...noted.entries()].filter(([, t]) => t > 0)
      .map(([model, t]) => `<div class="hint pa-note">${esc(model)} ${fmt(t)} 토큰 — 단가 ≈ 0 (크레딧 제외)</div>`)
      .join("");
    return `<div class="card person" data-owner="${esc(p.owner)}" role="button" tabindex="0"
        title="눌러서 모델·fast·effort·토큰 종류별 계산 보기">
      <div class="acct-head"><span class="email">${esc(p.owner)}</span>${live}</div>
      <div class="dnut-row">${cells}</div>
      ${legendRows.length ? `<div class="pa-legend">
        <div class="pa-row pa-head"><span class="pa-dot"></span>
          <span class="pa-name">${hasCredits ? "크레딧" : "토큰 · " + esc(m)}</span>
          <span class="pa-tok">5시간</span><span class="pa-tok">주간</span></div>
        ${legend}</div>`
        : `<div class="pa-legend"><div class="hint">이 기간에는 사용이 없습니다.</div></div>`}
      ${warn}${notes}
      <div class="pa-more">상세 보기 ›</div>
    </div>`;
  }).join("");
  const notice = hasCredits ? "" : `<div class="hint pa-warn" style="margin-bottom:10px">
      이 스냅샷에는 아직 크레딧 정보가 없어 토큰(상단 '지표')으로 표시합니다 — 집계 서버가
      새 코드를 받아 다음 스냅샷을 만들면 크레딧으로 바뀝니다.</div>`;
  box.innerHTML = `${notice}<div class="cards">${cards}</div>`;

  pending.forEach((d) => {
    const el = document.getElementById(d.id);
    if (!el || typeof Chart === "undefined") return;
    donutCharts.push(new Chart(el, {
      type: "doughnut",
      data: {
        labels: d.parts.map(paLabel),
        datasets: [{
          data: d.parts.map((b) => b[d.m]),
          backgroundColor: d.parts.map(paColor),
          borderWidth: 0,
        }],
      },
      options: {
        cutout: "66%", responsive: false, animation: { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (c) => (d.hasCredits ? `${c.label}: ${fmtCredit(c.parsed)} 크레딧`
                                          : `${c.label}: ${fmtFull(c.parsed)}`) +
                ` (${((c.parsed / d.total) * 100).toFixed(1)}%)`,
            },
          },
        },
      },
      plugins: [{
        id: "centerTotal",
        afterDraw(ch) {
          const { ctx, chartArea } = ch;
          if (!chartArea) return;
          ctx.save();
          ctx.fillStyle = "#e6edf3";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.font = "700 13px -apple-system, sans-serif";
          const cx = (chartArea.left + chartArea.right) / 2;
          const cy = (chartArea.top + chartArea.bottom) / 2;
          ctx.fillText(d.hasCredits ? fmtCredit(d.total) : fmt(d.total), cx, cy - 6);
          ctx.fillStyle = "#8b949e";
          // canvas 는 CSS 폰트를 따르지 않으므로 본문과 같은 목록을 직접 준다(한글 포함).
          ctx.font = '500 10px -apple-system, "Segoe UI", "Noto Sans KR", sans-serif';
          ctx.fillText(d.hasCredits ? "크레딧" : "토큰", cx, cy + 9);
          ctx.restore();
        },
      }],
    }));
  });
  if (state.detail) renderPersonDetail();
}

// ---- 개인 상세: 크레딧이 어떻게 계산됐는가 ---------------------------------
// 카드의 숫자 하나를 '어디서 얼마가 나왔는지' 로 풀어 보여 준다.
//   1) 토큰 종류별 합계 — 입력·출력·캐시 읽기·캐시 쓰기(TTL별) 각각의 크레딧과 비중
//   2) 모델 · 속도(fast) · effort 별 묶음 — 묶음마다 종류별 계산식
//        크레딧 = 토큰 수 × 단가($/1M) ÷ 1,000,000 ÷ $10
//   3) 단가 미등록 — 크레딧에 넣지 못한 토큰
// 비중은 전부 '그 사람의 그 창 크레딧 합계' 대비다.
function openPersonDetail(owner) {
  state.detail = { owner, w: (state.detail && state.detail.w) || "7d" };
  renderPersonDetail();
}

function closePersonDetail() {
  state.detail = null;
  $("#personDetail").classList.add("hidden");
}

function pctBar(p) {
  return `<span class="pctbar"><i style="width:${Math.min(100, Math.max(0, p || 0))}%"></i></span>`;
}

function renderPersonDetail() {
  const box = $("#personDetail");
  const d = state.detail;
  const s = state.summary || {};
  const p = (s.people || []).find((x) => x.owner === d.owner);
  if (!p) { closePersonDetail(); return; }
  if (!s.credit) {
    // 옛 스냅샷 — 계산 근거가 실려 오지 않았다. 빈 표 대신 이유를 말한다.
    box.querySelector(".modal-box").innerHTML = `
      <div class="modal-head"><div><h2 id="pdTitle">${esc(p.owner)} · 크레딧 상세</h2></div>
        <div class="modal-ctl"><button class="btn" data-close>닫기</button></div></div>
      <div class="empty">이 스냅샷에는 아직 크레딧 계산 내역이 없습니다. 집계 서버가 새 코드를
        받아 다음 스냅샷을 만들면 모델·fast·effort·토큰 종류별 내역이 표시됩니다.</div>`;
    box.classList.remove("hidden");
    return;
  }
  const det = (p.credit_detail || {})[d.w] || { total: 0, groups: [], components: [], unpriced: [] };
  const cr = s.credit || { usd_per_credit: 10 };
  const usd = (det.total || 0) * (cr.usd_per_credit || 10);
  const wl = (CHART_WINDOWS.find((x) => x.key === d.w) || {}).label || d.w;

  const seg = CHART_WINDOWS.map((w) =>
    `<button class="${w.key === d.w ? "on" : ""}" data-w="${w.key}">${w.label}</button>`).join("");

  const compRows = (det.components || []).map((c) => `<tr>
      <td>${esc(COMPONENT_LABEL[c.component] || c.component)}</td>
      <td class="num">${fmtFull(c.tokens)}</td>
      <td class="num">${fmtCredit(c.credits)}</td>
      <td class="num">${fmtPct(c.pct)} ${pctBar(c.pct)}</td></tr>`).join("");

  const groupRows = (det.groups || []).map((g) => {
    const speed = SPEED_LABEL[g.speed] || g.speed;
    const tags = [
      `<span class="chip prov ${esc(g.provider)}">${esc(g.provider)}</span>`,
      `<span class="chip">${esc((g.account_email || "").split("@")[0] || "계정 없음")}</span>`,
      `<b>${esc(g.model || "(모델 미상)")}</b>`,
      `<span class="chip ${g.speed === "fast" ? "fast" : ""}">${esc(speed)}</span>`,
      `<span class="chip">effort ${esc(g.effort || "미상")}</span>`,
      g.long_context ? `<span class="chip fast" data-tip="한 요청의 입력이 272K 를 넘어 OpenAI 장문맥 단가(입력·캐시 ×2, 출력 ×1.5)">장문맥</span>` : "",
      g.note ? `<span class="chip">${esc(g.note)}</span>` : "",
    ].join(" ");
    const head = `<tr class="cr-group"><td colspan="3">${tags}
        <span class="hint">· ${fmtFull(g.messages)}턴</span></td>
      <td class="num">${fmtCredit(g.credits)}</td>
      <td class="num">${fmtPct(g.pct)} ${pctBar(g.pct)}</td></tr>`;
    const subs = (g.components || []).map((c) => {
      const note = c.component === "cache_write_ttl_unknown"
        ? ` <span class="hint" data-tip="송신기 업데이트 전 기록이라 5분/1시간 구분이 없습니다. Claude Code 의 캐시 쓰기는 실측 97~100%가 1시간짜리라 1시간 단가로 계산했습니다.">ⓘ</span>` : "";
      return `<tr class="cr-sub">
        <td>${esc(COMPONENT_LABEL[c.component] || c.component)}${note}</td>
        <td class="num">${fmtFull(c.tokens)}</td>
        <td class="num cr-calc">× ${fmtRate(c.usd_per_mtok)}/1M ÷ $10</td>
        <td class="num">${fmtCredit(c.credits || (c.tokens && c.usd_per_mtok ? 1e-9 : 0))}</td>
        <td class="num">${fmtPct(c.pct)}</td></tr>`;
    }).join("");
    return head + subs;
  }).join("");

  const unp = det.unpriced || [];
  const unpBlock = unp.length ? `<h3>단가 미등록 — 크레딧에서 제외</h3>
    <p class="hint">공식 단가를 확인하지 못한 모델·처리 티어입니다. 0 으로 처리하면 그만큼 적게 쓴 것처럼
      보이므로 따로 둡니다. 단가가 확인되면 <code>scraper/ai_pricing.py</code> 에 추가하세요.</p>
    <div class="tablewrap"><table><thead><tr><th>도구</th><th>모델</th><th>처리</th>
      <th class="num">토큰</th><th>사유</th></tr></thead><tbody>
    ${unp.map((u) => `<tr><td>${esc(u.provider)}</td><td>${esc(u.model)}</td>
      <td>${esc(SPEED_LABEL[u.speed] || u.speed)}</td><td class="num">${fmtFull(u.tokens)}</td>
      <td>${esc(u.reason)}</td></tr>`).join("")}</tbody></table></div>` : "";

  const empty = !(det.groups || []).length;
  box.querySelector(".modal-box").innerHTML = `
    <div class="modal-head">
      <div><h2 id="pdTitle">${esc(p.owner)} · 크레딧 상세</h2>
        <div class="hint">${wl} 창 · 단가 기준 ${esc(cr.pricing_as_of || "")}</div></div>
      <div class="modal-ctl"><div class="seg">${seg}</div>
        <button class="btn" data-close>닫기</button></div>
    </div>
    <div class="cr-total"><span class="cr-big">${fmtCredit(det.total)}</span> 크레딧
      <span class="hint">≈ $${usd.toFixed(2)} (API 단가 환산 — 구독제라 실제 청구액은 아님)</span></div>
    <div class="cr-formula">크레딧 = 토큰 수 × 공식 API 단가($/1M 토큰) ÷ 1,000,000 ÷ $10
      &nbsp;·&nbsp; 1 크레딧 = $10. 단가는 모델·토큰 종류·처리 속도(fast)·장문맥에 따라 다르고,
      effort 는 단가가 아니라 생성량을 바꿉니다.</div>
    ${empty ? `<div class="empty">이 창에는 크레딧으로 환산할 사용이 없습니다.</div>` : `
    <h3>토큰 종류별</h3>
    <div class="tablewrap"><table class="cr"><thead><tr><th>종류</th><th class="num">토큰</th>
      <th class="num">크레딧</th><th class="num">전체 대비</th></tr></thead>
      <tbody>${compRows}</tbody></table></div>
    <h3>모델 · 속도 · effort 별 계산</h3>
    <div class="tablewrap"><table class="cr"><thead><tr><th>종류</th><th class="num">토큰</th>
      <th class="num">단가</th><th class="num">크레딧</th><th class="num">전체 대비</th></tr></thead>
      <tbody>${groupRows}</tbody></table></div>`}
    ${unpBlock}`;
  box.classList.remove("hidden");
}

// ---- alerts ---------------------------------------------------------------
async function renderAlerts() {
  const data = await api("/api/alerts");
  const s = state.summary;
  if (s) $("#notifierInfo").textContent =
    `활성 채널: ${(s.notifiers || []).join(", ")}` + (s.email_enabled ? "" : " · 이메일 비활성");
  const rows = data.alerts.map((a) => `<tr>
    <td>${dt(a.ts)}</td><td>${esc(a.account)}</td><td>${esc(RL_LABEL[a.window] || a.window)}</td>
    <td class="num">${a.value}%</td><td class="num">${a.limit_value}%</td>
    <td>${esc(a.name)}</td></tr>`).join("");
  $("#alertsTable").innerHTML = data.alerts.length
    ? `<table><thead><tr><th>시각</th><th>계정</th><th>윈도우</th>
        <th class="num">사용률</th><th class="num">경고선</th><th>이름</th></tr></thead>
       <tbody>${rows}</tbody></table>`
    : `<div class="empty">발생한 알림이 없습니다.</div>`;
}

// ---- settings (read-only) -------------------------------------------------
async function renderSettings() {
  const cfg = await api("/api/config");

  const tr = (cfg.tracking || {}).allowed_accounts || [];
  $("#trackingInfo").innerHTML = tr.length
    ? `<div class="kv"><span class="k">추적 계정</span> ${tr.map(esc).join(", ")}</div>
       <p class="hint">이 목록의 계정만 추적합니다. (빈 목록이면 전체) 변경은 중앙
       <code>config.json</code>의 <code>tracking.allowed_accounts</code>에서.</p>`
    : `<div class="kv">전체 계정 추적 중 (allowlist 비어 있음)</div>`;

  const ths = (cfg.alerts || {}).thresholds || [];
  const rows = ths.map((t) =>
    `<div class="kv"><span class="k">${RL_LABEL[t.window] || t.window}</span> `
    + `사용률 ${t.limit}% 도달 시 알림 · 쿨다운 ${t.cooldown_minutes != null ? t.cooldown_minutes : 30}분</div>`).join("");
  $("#alertInfo").innerHTML =
    (rows || '<div class="kv">설정된 알림이 없습니다.</div>')
    + `<p class="hint">알림은 <b>실제 사용률(%)</b> 기준입니다. 경고 수준(%)은 중앙
       <code>config.json</code>의 <code>alerts.thresholds[].limit</code>에서 변경.
       (알림 발송 자체는 중앙 서버에서 동작 — 이 페이지는 읽기 전용)</p>`;

  const e = cfg.email || {};
  $("#emailStatus").innerHTML = `
    <div class="kv"><span class="k">상태</span> ${e.enabled ? "✅ 활성" : "⛔ 비활성"}</div>
    <div class="kv"><span class="k">SMTP</span> ${esc(e.smtp_host)}:${esc(e.smtp_port)}</div>
    <div class="kv"><span class="k">발신/수신</span> ${esc(e.from)} → ${esc((e.to || []).join(", "))}</div>`;

  const c = cfg.collect || {};
  $("#collectInfo").innerHTML = `
    <div class="kv"><span class="k">수집 주기</span> ${esc(c.interval_seconds)}초</div>
    <div class="kv"><span class="k">Claude dirs</span> ${esc((c.config_dirs || []).join(", "))}</div>
    <div class="kv"><span class="k">Codex dirs</span> ${esc((c.codex_dirs || []).join(", ")) || "-"}</div>
    <div class="kv"><span class="k">NAS 수집</span> ${(cfg.nas || {}).enabled ? esc((cfg.nas || {}).dropdir) : "비활성"}</div>`;
}

// ---- render (uses already-loaded BUNDLE) ----------------------------------
async function render() {
  const s = await api("/api/summary");
  state.summary = s;
  state.metric = $("#metricSelect").value;
  $("#hostline").textContent = `중앙: ${s.host} · Claude/Codex 토큰 사용량 추적`;
  $("#liveBadge").textContent = s.live_session_count;
  renderBanner(s);

  if (state.tab === "overview") { renderTotals(s); renderAccounts(s); }
  if (state.tab === "live" || state.tab === "sessions") {
    const d = await api("/api/sessions");
    state.sessions = d.sessions;
    if (state.tab === "live") renderLive(); else renderSessions();
  }
  if (state.tab === "charts") await renderCharts();
  if (state.tab === "alerts") await renderAlerts();
  if (state.tab === "settings") await renderSettings();
}

// ---- refresh: reload the snapshot, then render ----------------------------
async function refresh() {
  try {
    await loadBundle();
    const gen = BUNDLE.generated_at;
    $("#updated").textContent = "데이터 기준 "
      + new Date(gen).toLocaleString("ko-KR", { hour12: false }) + ` (${ago(gen)})`;
    renderStale();
    await render();
  } catch (e) {
    console.error(e);
    const b = $("#staleBanner");
    if (e && e.absent) {
      $("#updated").textContent = "이 배포본에는 데이터가 없습니다";
      b.innerHTML = "<div>사용량 데이터는 연구실 안에서만 봅니다 — "
        + "랩 구성원 이름과 세션 작업 경로가 들어 있어 공개 배포본에는 싣지 않습니다.</div>"
        + "<div>사내 서버에 올라간 같은 페이지에서 확인하세요.</div>";
    } else {
      $("#updated").textContent = "데이터 로드 실패";
      b.innerHTML = `<div>스냅샷(${esc(DATA_URL)})을 읽지 못했습니다 — `
        + `${esc((e && e.message) || e)}</div>`;
    }
    b.classList.remove("hidden");
  }
}

function setupAutoRefresh() {
  if (state.timer) clearInterval(state.timer);
  if ($("#autoRefresh").checked) state.timer = setInterval(refresh, 30000);
}

// ---- wire up --------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  $$(".tab").forEach((t) => t.onclick = () => switchTab(t.dataset.tab));
  $("#refreshBtn").onclick = refresh;
  $("#metricSelect").onchange = () => { state.metric = $("#metricSelect").value; render(); };
  $("#autoRefresh").onchange = setupAutoRefresh;
  $("#sessionFilter").oninput = renderSessions;
  // 개인 카드 → 상세. 카드는 30초마다 다시 그려지므로 영역에 한 번만 건다.
  const openFrom = (e) => {
    const card = e.target.closest(".card.person");
    if (card) openPersonDetail(card.dataset.owner);
  };
  $("#chartArea").addEventListener("click", openFrom);
  $("#chartArea").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFrom(e); }
  });
  $("#personDetail").addEventListener("click", (e) => {
    if (e.target.id === "personDetail" || e.target.closest("[data-close]")) closePersonDetail();
    const seg = e.target.closest(".seg button");
    if (seg && state.detail) { state.detail.w = seg.dataset.w; renderPersonDetail(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.detail) closePersonDetail();
  });
  setupTooltip();
  setupAutoRefresh();
  refresh();
});
