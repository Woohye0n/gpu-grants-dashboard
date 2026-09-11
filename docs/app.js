/* GPU 지원사업 트래커 — renders window.GPU_GRANTS (written by scraper/run.py). */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const STATUS = {
    open:     { label: '모집중',   cls: 'open' },
    soon:     { label: '마감임박', cls: 'soon' },
    upcoming: { label: '접수예정', cls: 'upcoming' },
    closed:   { label: '마감',     cls: 'closed' },
    unknown:  { label: '상시·미정', cls: 'unknown' },
  };

  let DATA = window.GPU_GRANTS || { programs: [], sources: [], counts: {} };
  let tab = 'open';

  /* ---------------------------------------------------------------- utils */
  const day = (s) => (s || '').slice(0, 10);
  const hhmm = (s) => (s && s.length > 10 ? s.slice(11, 16) : '');
  const fmtDate = (s) => {
    const d = day(s);
    if (!d) return '—';
    const t = hhmm(s);
    return t ? `${d} ${t}` : d;
  };
  const view = (p) => (p.status === 'open' && p.closing_soon ? 'soon' : p.status);
  const nz = (v, fallback) => (v === null || v === undefined ? fallback : v);
  const isAcademic = (a) => /학계|연구계/.test(a || '');
  // 비용은 {audience, text} 목록이다. 예전 스냅샷(문자열)도 그대로 읽는다.
  const costRows = (p) => (p.cost || []).map(
    (c) => (typeof c === 'string' ? { audience: '', text: c } : c));

  function ddayText(p) {
    if (p.dday == null) return '';
    if (p.dday === 0) return 'D-DAY';
    return p.dday > 0 ? `D-${p.dday}` : `D+${-p.dday}`;
  }

  function spanDays(p) {
    if (!p.apply_start || !p.apply_end) return null;
    const a = new Date(day(p.apply_start)), b = new Date(day(p.apply_end));
    const total = Math.round((b - a) / 86400000);
    return total > 0 ? total : null;
  }

  function periodText(u) {
    if (!u) return null;
    if (u.start && u.end) return `${u.start} ~ ${u.end}`;
    if (u.end) return `~ ${u.end}`;
    if (u.start) return `${u.start} ~`;
    return null;
  }

  /* tooltip that can show the source sentence behind an extracted number */
  const tip = $('tip');
  function bindTip(node, text) {
    if (!text) return node;
    node.addEventListener('mousemove', (e) => {
      tip.textContent = text;
      tip.style.display = 'block';
      tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 440) + 'px';
      tip.style.top = (e.clientY + 18) + 'px';
    });
    node.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
    return node;
  }

  /* ------------------------------------------------------------- filtering */
  function filtered() {
    const q = $('q').value.trim().toLowerCase();
    const src = $('fSource').value, gpu = $('fGpu').value, aud = $('fAud').value;
    const sort = $('fSort').value;

    let rows = DATA.programs.filter((p) => {
      if (src && !(p.links || []).some((l) => l.source === src)) return false;
      if (gpu && !(p.gpu_models || []).includes(gpu)) return false;
      if (aud && p.audience !== aud) return false;
      if (q) {
        const hay = [p.title, p.program, p.org, p.source_label,
                     (p.gpu_models || []).join(' '), (p.scale || []).join(' ')]
                     .join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    if (tab === 'open') rows = rows.filter((p) => p.status === 'open' || p.status === 'upcoming' || p.status === 'unknown');

    const rank = { open: 0, upcoming: 1, unknown: 2, closed: 3 };
    const cmp = {
      deadline: (a, b) => (rank[a.status] - rank[b.status]) ||
                          (nz(a.dday, 9999) - nz(b.dday, 9999)) ||
                          String(b.apply_end || '').localeCompare(String(a.apply_end || '')),
      posted:   (a, b) => String(b.posted || '').localeCompare(String(a.posted || '')),
      source:   (a, b) => String(a.source_label).localeCompare(String(b.source_label)) ||
                          (rank[a.status] - rank[b.status]),
    }[sort];
    return rows.sort(cmp);
  }

  /* ----------------------------------------------------------------- card */
  function card(p) {
    const v = view(p);
    const node = el('div', `card ${v}`);

    const head = el('div', 'head');
    const left = el('div');
    const title = el('div', 'title');
    const a = el('a', null, p.title);
    a.href = p.url; a.target = '_blank'; a.rel = 'noopener';
    title.appendChild(a);
    left.appendChild(title);
    const meta = el('div', 'meta');
    meta.innerHTML = `<b>${escapeHtml(p.program || '—')}</b> · ${escapeHtml(p.org || '')}`;
    left.appendChild(meta);
    head.appendChild(left);

    const pill = el('span', `pill ${STATUS[v].cls}`,
                    v === 'closed' || v === 'unknown' ? STATUS[v].label
                                                      : `${STATUS[v].label} ${ddayText(p)}`.trim());
    head.appendChild(pill);
    node.appendChild(head);

    const chips = el('div', 'chips');
    (p.links || []).forEach((l) => chips.appendChild(el('span', 'chip src', l.label)));
    chips.appendChild(el('span', 'chip aud', p.audience === 'supplier' ? '공급사·운영기관 모집' : '수요자 모집'));
    if (p.stale) {
      bindTip(chips.appendChild(el('span', 'chip stale', `${day(p.stale_since) || '이전'} 기준`)),
              '이 사이트를 이번에 읽지 못해 직전 수집 결과를 그대로 보여줍니다.');
    }
    (p.gpu_models || []).forEach((m) => chips.appendChild(el('span', 'chip gpu', m)));
    if (!(p.gpu_models || []).length) chips.appendChild(el('span', 'chip', 'GPU 기종 미표기'));
    node.appendChild(chips);

    if ((p.gpu_specs || []).length) {
      const specs = el('div', 'specs');
      specs.appendChild(el('div', 'hint', 'GPU 기종 · 수량'));
      p.gpu_specs.slice(0, 5).forEach((s) => {
        const row = el('div', 'spec');
        row.appendChild(el('span', 'm', s.vram ? `${s.model} ${s.vram}` : s.model));
        row.appendChild(el('span', 'qty', s.qty || '—'));
        const why = el('span', 'src', '원문');
        bindTip(why, s.line);
        row.appendChild(why);
        specs.appendChild(row);
      });
      node.appendChild(specs);
    }

    const kv = el('div', 'kv');
    const add = (k, valueNode) => { kv.appendChild(el('div', 'k', k)); kv.appendChild(valueNode); };

    const dl = el('div', 'v');
    dl.textContent = p.apply_end ? fmtDate(p.apply_end) : '상시 / 미표기';
    if (p.apply_start) {
      const note = el('div', 'note', `접수 시작 ${fmtDate(p.apply_start)}`);
      dl.appendChild(note);
    }
    const total = spanDays(p);
    if (p.status === 'open' && p.dday != null && total) {
      const bar = el('div', 'deadline-bar');
      const fill = el('span');
      const remaining = Math.max(0, Math.min(1, p.dday / total));
      fill.style.width = (remaining * 100).toFixed(1) + '%';
      if (p.dday <= 3) fill.className = 'crit';
      else if (p.dday <= 7) fill.className = 'warn';
      bar.appendChild(fill);
      dl.appendChild(bar);
    }
    add('신청 마감', dl);

    const up = el('div', 'v');
    const ptext = periodText(p.usage_period);
    up.textContent = ptext || '공고 원문 참조';
    if (p.usage_period && p.usage_period.text) {
      const note = el('div', 'note', p.usage_period.text.slice(0, 110));
      bindTip(note, p.usage_period.text);
      up.appendChild(note);
    }
    add('사용 기간', up);

    if ((p.cost || []).length) {
      const c = el('div', 'v');
      costRows(p).forEach((row, i) => {
        const line = el('div', 'costline' + (i === 0 ? ' primary' : ''));
        if (row.audience) {
          line.appendChild(el('span', 'aud' + (isAcademic(row.audience) ? ' academic' : ''), row.audience));
        }
        line.appendChild(el('span', 'txt', row.text));
        c.appendChild(line);
      });
      add('비용', c);
    }
    if ((p.scale || []).length) {
      const s = el('div', 'v');
      s.appendChild(el('div', null, p.scale[0]));
      add('지원 규모', s);
    }
    node.appendChild(kv);

    const actions = el('div', 'actions');
    (p.links || [{ label: p.source_label, url: p.url }]).forEach((l) => {
      const link = el('a', 'golink', `${l.label} 공고 →`);
      link.href = l.url; link.target = '_blank'; link.rel = 'noopener';
      actions.appendChild(link);
    });
    const detail = el('div', 'detail hidden');
    detail.appendChild(detailBody(p));
    const more = el('button', 'more', '자세히 보기');
    more.addEventListener('click', () => {
      detail.classList.toggle('hidden');
      more.textContent = detail.classList.contains('hidden') ? '자세히 보기' : '접기';
    });
    actions.appendChild(more);
    node.appendChild(actions);
    node.appendChild(detail);
    return node;
  }

  function detailBody(p) {
    const frag = document.createDocumentFragment();
    const block = (heading, items, render) => {
      if (!items || !items.length) return;
      frag.appendChild(el('div', 'h', heading));
      const ul = el('ul');
      items.forEach((it) => ul.appendChild(render(it)));
      frag.appendChild(ul);
    };
    if (p.summary) {
      frag.appendChild(el('div', 'h', '개요'));
      frag.appendChild(el('div', null, p.summary));
    }
    block('지원 규모 (원문)', p.scale, (t) => el('li', null, t));
    block('비용 (원문)', costRows(p), (c) => el('li', null, (c.audience ? c.audience + ' — ' : '') + c.text));
    block('GPU 기종·수량 근거', (p.gpu_specs || []).map((s) => s.line), (t) => el('li', null, t));
    block('첨부파일', p.attachments, (att) => {
      const li = el('li');
      const a = el('a', null, att.name);
      a.href = att.url; a.target = '_blank'; a.rel = 'noopener';
      li.appendChild(a);
      return li;
    });
    const foot = el('div', null, `공고일 ${p.posted || '—'} · 출처 ${(p.links || []).map((l) => l.label).join(', ')}`);
    frag.appendChild(foot);
    return frag;
  }

  /* ---------------------------------------------------------------- table */
  function table(rows) {
    const t = el('table');
    const head = el('thead');
    head.innerHTML = '<tr><th>상태</th><th>출처</th><th>사업 / 공고</th><th>GPU 기종 · 수량</th>' +
                     '<th>신청 마감</th><th>사용 가능 기간</th></tr>';
    t.appendChild(head);
    const body = el('tbody');
    rows.forEach((p) => {
      const v = view(p);
      const tr = el('tr');

      const st = el('td', 'nowrap');
      st.appendChild(el('span', `pill ${STATUS[v].cls}`,
        v === 'closed' || v === 'unknown' ? STATUS[v].label : `${STATUS[v].label} ${ddayText(p)}`.trim()));
      tr.appendChild(st);

      tr.appendChild(el('td', 'nowrap', (p.links || []).map((l) => l.label).join(' / ')));

      const td = el('td');
      const a = el('a', null, p.title);
      a.href = p.url; a.target = '_blank'; a.rel = 'noopener';
      td.appendChild(a);
      td.appendChild(el('div', 'hint', p.program || ''));
      tr.appendChild(td);

      const g = el('td');
      if ((p.gpu_specs || []).length) {
        p.gpu_specs.slice(0, 4).forEach((s) => {
          const line = el('div', null, `${s.vram ? s.model + ' ' + s.vram : s.model} — ${s.qty || '—'}`);
          bindTip(line, s.line);
          g.appendChild(line);
        });
      } else {
        g.appendChild(el('div', 'hint', (p.gpu_models || []).join(', ') || '미표기'));
      }
      tr.appendChild(g);

      tr.appendChild(el('td', 'nowrap', p.apply_end ? fmtDate(p.apply_end) : '상시/미표기'));
      tr.appendChild(el('td', 'nowrap', periodText(p.usage_period) || '원문 참조'));
      body.appendChild(tr);
    });
    t.appendChild(body);
    return t;
  }

  /* --------------------------------------------------------------- render */
  function renderTotals() {
    const c = DATA.counts || {};
    const box = $('totals');
    box.innerHTML = '';
    const make = (label, value, note, cls) => {
      const n = el('div', 'totalcard' + (cls ? ' ' + cls : ''));
      n.appendChild(el('div', 'label', label));
      n.appendChild(el('div', 'value', String(value)));
      n.appendChild(el('div', 'small', note));
      return n;
    };
    box.appendChild(make('모집중', c.open || 0, '지금 신청 가능한 공고', 'live'));
    box.appendChild(make('마감 임박', c.closing_soon || 0, '7일 이내 마감', (c.closing_soon ? 'hot' : '')));
    box.appendChild(make('접수 예정', c.upcoming || 0, '아직 시작 전'));
    box.appendChild(make('추적 중인 공고', c.total || 0, `마감 ${c.closed || 0}건 포함`));
    box.appendChild(make('수집 사이트', (DATA.sources || []).length,
      (DATA.sources || []).every((s) => s.ok) ? '전부 정상 수집' : '일부 수집 실패'));
  }

  function renderBanner() {
    const b = $('banner');
    const soon = DATA.programs.filter((p) => p.closing_soon);
    const stale = DATA.stale_sources || [];
    b.innerHTML = '';
    b.classList.remove('warn');
    if (!soon.length && !stale.length) { b.classList.add('hidden'); return; }
    b.classList.remove('hidden');
    if (soon.length) {
      b.appendChild(el('div', null, '마감 임박 ' + soon.length + '건 — ' +
        soon.map((p) => `${p.program || p.title} (${ddayText(p)}, ~${day(p.apply_end)})`).join(' · ')));
    }
    if (stale.length) {
      b.classList.add('warn');
      b.appendChild(el('div', null,
        `수집 실패 ${stale.length}곳 (${stale.join(', ')}) — 해당 사이트는 직전 수집 결과를 보여주는 중입니다.`));
    }
  }

  function renderSources() {
    const box = $('sourceCards');
    box.innerHTML = '';
    (DATA.sources || []).forEach((s) => {
      const n = el('div', 'card');
      const head = el('div', 'head');
      const left = el('div');
      const title = el('div', 'title');
      title.innerHTML = `<span class="dot ${s.ok ? 'ok' : 'fail'}"></span>${escapeHtml(s.label)}`;
      left.appendChild(title);
      const a = el('a', 'hint', s.url);
      a.href = s.url; a.target = '_blank'; a.rel = 'noopener';
      left.appendChild(a);
      head.appendChild(left);
      head.appendChild(el('span', `pill ${s.ok ? 'open' : (s.stale ? 'soon' : 'closed')}`,
                          s.ok ? '정상' : (s.stale ? '직전 데이터 사용' : '실패')));
      n.appendChild(head);
      const kv = el('div', 'kv');
      kv.appendChild(el('div', 'k', 'GPU 공고'));
      kv.appendChild(el('div', 'v', `${s.count}건`));
      if (s.collected_at) {
        kv.appendChild(el('div', 'k', '수집 시각'));
        kv.appendChild(el('div', 'v', s.collected_at + ' KST'));
      }
      if (s.error) {
        kv.appendChild(el('div', 'k', '사유'));
        kv.appendChild(el('div', 'v', s.error));
      }
      n.appendChild(kv);
      box.appendChild(n);
    });

    $('howbox').innerHTML = `
      <p><span class="k">수집 주기</span> 하루 1회 (기본 09:10 KST) — <code>update.sh</code></p>
      <p><span class="k">수집 범위</span> 각 게시판의 최근 공고를 훑어 GPU·고성능컴퓨팅 키워드가 있는 건만 추립니다.</p>
      <p><span class="k">GPU 기종·수량</span> 공고 본문과 첨부 공고문(.hwp/.hwpx/.pdf)의 텍스트에서 자동 추출합니다.
         각 값 옆의 <b>원문</b>에 마우스를 올리면 근거 문장이 보입니다.</p>
      <p><span class="k">중복 공고</span> 같은 사업이 두 사이트에 올라오면 한 카드로 합치고 두 링크를 모두 답니다.</p>
      <p><span class="k">마지막 수집</span> ${escapeHtml(DATA.generated_at_kst || '—')} KST</p>`;
  }

  function render() {
    const rows = filtered();
    renderTotals();
    renderBanner();

    $('openBadge').textContent = (DATA.counts || {}).open || 0;
    $('allBadge').textContent = (DATA.programs || []).length;
    $('updated').textContent = DATA.generated_at_kst ? `업데이트 ${DATA.generated_at_kst} KST` : '';
    $('resultCount').textContent = `${rows.length}건 표시`;

    const target = tab === 'open' ? $('openCards') : $('allCards');
    if (tab === 'open' || tab === 'all') {
      target.innerHTML = '';
      target.classList.toggle('few', rows.length > 0 && rows.length <= 2);
      if (!rows.length) {
        target.appendChild(el('div', 'empty', tab === 'open'
          ? '지금 접수 중인 GPU 지원사업이 없습니다. "전체 공고" 탭에서 지난 공고와 사업 주기를 확인하세요.'
          : '조건에 맞는 공고가 없습니다.'));
      } else {
        rows.forEach((p) => target.appendChild(card(p)));
      }
    }
    if (tab === 'table') {
      const wrap = $('tableWrap');
      wrap.innerHTML = '';
      wrap.appendChild(rows.length ? table(rows) : el('div', 'empty', '표시할 공고가 없습니다.'));
    }
    if (tab === 'sources') renderSources();

    $('openHint').textContent = tab === 'open'
      ? '접수 중·예정·상시 공고만 보여줍니다. 지난 공고는 “전체 공고” 탭에 있습니다.' : '';
  }

  function fillFilters() {
    const src = $('fSource'), gpu = $('fGpu');
    const sources = new Map(), models = new Set();
    DATA.programs.forEach((p) => {
      (p.links || []).forEach((l) => sources.set(l.source, l.label));
      (p.gpu_models || []).forEach((m) => models.add(m));
    });
    src.innerHTML = '<option value="">전체</option>';
    sources.forEach((label, key) => src.appendChild(new Option(label, key)));
    gpu.innerHTML = '<option value="">전체</option>';
    [...models].sort().forEach((m) => gpu.appendChild(new Option(m, m)));
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* ----------------------------------------------------------------- wire */
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      tab = btn.dataset.tab;
      document.querySelectorAll('.panel').forEach((p) => p.classList.add('hidden'));
      $('tab-' + tab).classList.remove('hidden');
      $('filterbar').style.display = tab === 'sources' ? 'none' : '';
      $('totals').style.display = tab === 'sources' ? 'none' : '';
      render();
    });
  });
  ['q', 'fSource', 'fGpu', 'fAud', 'fSort'].forEach((id) => {
    $(id).addEventListener('input', render);
    $(id).addEventListener('change', render);
  });
  const refreshBtn = $('refreshBtn');   // absent in the single-file build
  if (refreshBtn) refreshBtn.addEventListener('click', async () => {
    try {
      const r = await fetch('./data.json?t=' + Date.now(), { cache: 'no-store' });
      DATA = await r.json();
      fillFilters();
      render();
    } catch (e) {
      $('updated').textContent = '새로고침 실패 (파일에서 직접 열었다면 새로고침은 동작하지 않습니다)';
    }
  });

  fillFilters();
  render();
})();
