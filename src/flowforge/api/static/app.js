// FlowForge dashboard. Reads the local API (flowforge.api.app); the only write is the kill switch.
(() => {
  'use strict';
  const MINUS = '−';
  const TZ = 'America/New_York';
  const SETUP_NAMES = {
    H1: 'Stacked imbalance pullback', H2: 'Large print pullback', H3: 'Multiple node pullback',
    H4: 'Support zone + confirmation', H5: 'Buy-side sweep',
  };
  const view = document.getElementById('view');
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const nf = (d) => new Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const num = (v, d = 2) => (v == null ? '<span class="muted">–</span>' : nf(d).format(v));
  const signed = (v, d = 2) => (v == null ? '<span class="muted">–</span>' : (v > 0 ? '+' : v < 0 ? MINUS : '') + nf(d).format(Math.abs(v)));
  const usd = (v, d = 2) => `${signed(v, d)}<small>USD</small>`;
  const clock = (ns, sec = false) => new Date(Number(ns) / 1e6).toLocaleTimeString('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: sec ? '2-digit' : undefined });
  const clockIso = (iso, sec = false) => new Date(iso).toLocaleTimeString('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: sec ? '2-digit' : undefined });
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  let timer = null, lastGood = null, statusCache = null;

  async function api(path, opts) {
    const r = await fetch(path, { cache: 'no-store', ...opts });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try { detail = (await r.json()).detail || detail; } catch { /* body was not JSON */ }
      throw new Error(detail);
    }
    lastGood = new Date();
    return r.json();
  }

  function titleRow(title, sub, { bad = false, actions = '' } = {}) {
    return `<div class="title-row"><div><h1>${esc(title)}</h1>${sub ? `<p class="sub${bad ? ' bad' : ''}">${esc(sub)}</p>` : ''}</div><div class="actions">${actions}</div></div>`;
  }
  const stamp = () => `<span class="stamp">Updated ${clock(Date.now() * 1e6, true)} ET</span>`;
  function killControl(s) {
    if (s && s.kill.on) return `<span class="pill neg">Kill switch on</span>`;
    return `<button class="btn danger" data-kill>Kill switch</button>`;
  }
  function failed(err) {
    const when = lastGood ? ` Last good data at ${lastGood.toLocaleTimeString('en-GB')}.` : '';
    return `<div class="panel"><p class="error"><b>Couldn't load this screen:</b> ${esc(err.message)}.${esc(when)} <button class="btn" data-retry>Retry</button></p></div>`;
  }
  function scope(s) {
    if (!s) return;
    document.getElementById('scope-account').textContent = s.account;
    document.getElementById('scope-session').textContent = s.session.label;
    document.getElementById('demo').hidden = !s.demo;
  }
  function track(used, limit) {
    const ratio = limit > 0 ? Math.min(1, used / limit) : 0;
    return `<div class="track${used >= limit ? ' full' : ''}" role="img" aria-label="${esc(used)} of ${esc(limit)}"><i style="width:${(ratio * 100).toFixed(2)}%"></i></div>`;
  }
  function outcomePill(o) {
    const cls = { traded: 'pos', 'in trade': '', 'refused by risk': 'neg', 'vetoed by Jev': 'warn', skipped: 'warn', 'not filled': 'warn' }[o] ?? '';
    return `<span class="pill ${cls}">${esc(o[0].toUpperCase() + o.slice(1))}</span>`;
  }
  function tradePill(t) {
    if (t.status === 'open') return '<span class="pill">Open</span>';
    if (t.status === 'not filled') return '<span class="pill warn">Not filled</span>';
    const label = { target: 'Target', stop: 'Stop', eod: 'End of day', kill: 'Kill switch', halt: 'Loss limit', exit: 'Exit' }[t.exit_reason] || 'Closed';
    return `<span class="pill ${t.net > 0 ? 'pos' : t.net < 0 ? 'neg' : ''}">${label}</span>`;
  }
  const emptyRow = (cols, text) => `<tr><td class="empty-cell" colspan="${cols}"><div class="empty">${esc(text)}</div></td></tr>`;

  // ---------- Live ----------
  async function live() {
    const s = await api('/api/status');
    statusCache = s; scope(s);
    const t = s.today;
    const marked = s.equity != null && s.day_start_equity ? s.equity - s.day_start_equity : null;
    const open = marked != null ? marked - t.net : null;
    const bad = s.kill.on || !!s.halt || !s.node || s.node.stale;
    const heldNote = s.positions.length ? s.positions.map((p) => `${p.symbol} ${p.shares - p.sold} sh`).join(', ') : 'flat';
    const node = s.node
      ? `<div class="kpi-value">${num(s.node.age_s, 1)}<small>s ago</small></div><div class="note">${s.node.stale ? 'stale: watchdog takes over' : 'heartbeat, every second'}</div>`
      : `<div class="kpi-value muted">–</div><div class="note">not running</div>`;
    const dailyLoss = s.budget.find((b) => b.key === 'daily_loss');
    const floor = s.day_start_equity && dailyLoss ? s.day_start_equity - dailyLoss.limit : null;
    view.innerHTML = `
      ${titleRow('Live', s.finding, { bad, actions: stamp() + killControl(s) })}
      <section class="kpis" aria-label="Today">
        <div class="kpi lead"><div class="kpi-label">Today, marked now</div><div class="kpi-value">${usd(marked)}</div><div class="note">closed trades ${signed(t.net)}, open ${signed(open)}</div></div>
        <div class="kpi"><div class="kpi-label">Equity</div><div class="kpi-value">${num(s.equity)}<small>USD</small></div><div class="note">day start ${num(s.day_start_equity)}</div></div>
        <div class="kpi"><div class="kpi-label">Closed trades</div><div class="kpi-value">${t.trades}</div><div class="note">${t.wins} won, ${t.losses} lost, fees ${num(t.commission)}</div></div>
        <div class="kpi"><div class="kpi-label">Open positions</div><div class="kpi-value">${s.positions.length}</div><div class="note">${esc(heldNote)}</div></div>
        <div class="kpi"><div class="kpi-label">Trading node</div>${node}</div>
      </section>
      <div class="grid" style="margin-top:24px">
        <section class="panel s8" id="eq-panel"><header><h2>Equity today</h2><span class="meta">${floor ? `Daily loss stop at ${num(floor)} USD` : ''}</span></header><div class="chart" id="eq"></div></section>
        <section class="panel s4" id="budget-panel"><header><h2>Risk budget</h2><span class="meta">from risk_limits.yaml</span></header>
          <ul class="budget">${s.budget.map((b) => `<li><div class="top"><span>${esc(b.label)}</span><span class="fig">${b.unit === 'usd' ? num(b.used) : b.used}<small> of ${b.unit === 'usd' ? num(b.limit) : b.limit}</small></span></div>${track(b.used, b.limit)}<span class="why">${esc(b.note)}</span></li>`).join('')}</ul>
        </section>
        <section class="panel s8" id="trades-panel"><header><h2>Trades today</h2><a class="meta" href="#/journal">Journal</a></header>
          <div class="table-wrap"><table class="stack"><thead><tr><th>Symbol</th><th>Result</th><th class="n">Shares</th><th class="n">Entry</th><th class="n">Stop</th><th class="n">Exit / last</th><th class="n">Net USD</th><th class="n">R</th></tr></thead><tbody>
          ${[...s.positions, ...s.trades.filter((x) => x.status !== 'open').reverse()].slice(0, 6).map((x) => `<tr>
            <td>${esc(x.symbol)}<span class="sub">${esc(x.setup)} · ${clock(x.opened_ns)}</span></td><td class="more">${tradePill(x)}</td>
            <td class="n more">${x.shares}</td><td class="n more">${num(x.entry_price)}</td><td class="n more">${num(x.stop)}</td>
            <td class="n more">${x.status === 'open' ? num(x.last) : num(x.exit_price)}</td>
            <td class="n key">${x.status === 'open' ? `<span class="muted">${signed(x.unrealized)}</span>` : signed(x.net)}</td><td class="n more">${x.r == null ? '<span class="muted">–</span>' : signed(x.r)}</td></tr>`).join('') || emptyRow(8, 'No trades yet today.')}
          </tbody></table></div></section>
        <section class="panel s4" id="latest-panel"><header><h2>Latest</h2><a class="meta" href="#/journal">All of ${esc(s.day)}</a></header>
          <ul class="rows" id="latest">${s.recent.slice().reverse().map(eventRow).join('') || '<li><div class="empty" style="grid-column:1/-1">Nothing to report today.</div></li>'}</ul></section>
      </div>`;
    const marks = s.equity_curve;
    const el = document.getElementById('eq');
    const wide = matchMedia('(min-width: 1101px)').matches;
    // cards that share a row share a bottom edge: the chart fills the risk budget's height, and the
    // latest-events list keeps as many rows as fit beside the trades table
    const fill = wide ? document.getElementById('budget-panel').offsetHeight - (document.getElementById('eq-panel').offsetHeight - el.offsetHeight) : 236;
    if (marks.length > 1) {
      const values = marks.map((m) => m[1]);
      const low = Math.min(...values, s.day_start_equity ?? Infinity), high = Math.max(...values, s.day_start_equity ?? -Infinity);
      const pad = Math.max(5, (high - low) * 0.25);
      Charts.line(el, { labels: marks.map((m) => clock(m[0])), series: [{ name: 'Equity', values }], format: Charts.fmt.dec(2), tickFormat: Charts.fmt.int, height: Math.max(200, fill), min: Math.floor(low - pad), reference: s.day_start_equity ? { value: s.day_start_equity, label: 'Day start' } : undefined });
    } else el.innerHTML = '<div class="empty">No equity marks yet today. The node writes one a minute while it runs.</div>';
    if (wide) document.fonts.ready.then(() => balanceLatest());
  }

  // Keep as many latest events as fit beside the trades table, then spread the rest of the
  // height over the rows, so the two cards end on one line.
  function balanceLatest() {
    const list = document.getElementById('latest'), left = document.getElementById('trades-panel'), right = document.getElementById('latest-panel');
    if (!list || !left || !right) return;
    [...list.children].forEach((li) => { li.style.paddingTop = li.style.paddingBottom = ''; });
    while (list.children.length > 2 && right.offsetHeight > left.offsetHeight) list.lastElementChild.remove();
    const spare = left.offsetHeight - right.offsetHeight, rows = list.children.length;
    if (spare > 0 && rows) [...list.children].forEach((li) => { const extra = spare / rows / 2; li.style.paddingTop = li.style.paddingBottom = `${9 + extra}px`; });
  }

  function eventRow(r) {
    const name = {
      alert: 'Alert', kill_engaged: 'Kill switch on', watchdog_alert: 'Watchdog', watchdog_flatten: 'Watchdog flatten',
      order_rejected: 'Order rejected', risk_decision: r.allowed ? 'Risk: allowed' : 'Risk: refused', premarket_check: 'Pre-market check', eod_report: 'End of day',
    }[r.event] || r.event;
    const detail = r.event === 'risk_decision' ? `${r.symbol || ''} ${(r.reasons || []).join('; ') || (r.plan ? `${r.plan.quantity} sh, stop ${r.plan.stop_price}` : '')}`
      : r.message || r.reason || r.title || '';
    return `<li><span class="t">${clockIso(r.ts)}</span><div class="what">${esc(name)}<span title="${esc(detail)}">${esc(detail)}</span></div></li>`;
  }

  // ---------- Tape ----------
  let tapeSymbol = null;
  async function tape() {
    const [t, s] = await Promise.all([api('/api/tape'), statusCache ? Promise.resolve(statusCache) : api('/api/status')]);
    scope(s);
    const syms = Object.keys(t.symbols);
    if (!t.available || !syms.length) {
      view.innerHTML = titleRow('Tape', 'Order flow per minute from the trading node.') + '<div class="panel"><div class="empty">No tape yet. The node writes it every 5 seconds once it is running and receiving trades.</div></div>';
      return;
    }
    if (!syms.includes(tapeSymbol)) tapeSymbol = syms[0];
    const d = t.symbols[tapeSymbol], bars = d.bars, last = bars.at(-1) || {};
    const sum = (a, k) => a.reduce((x, b) => x + (b[k] || 0), 0);
    const delta60 = sum(bars, 'delta');
    const lead = delta60 > 0 ? 'buyers' : delta60 < 0 ? 'sellers' : 'neither side';
    const sub = `${tapeSymbol}: ${lead} ahead over the last ${bars.length} minutes (${signed(delta60, 0).replace(/<[^>]+>/g, '')} shares net).` + (t.stale ? ` Tape is ${Math.round(t.age_s)} s old.` : '');
    view.innerHTML = `
      ${titleRow('Tape', sub, { bad: t.stale, actions: stamp() })}
      <div class="tabs" role="tablist">${syms.map((x) => `<button role="tab" aria-selected="${x === tapeSymbol}" data-sym="${esc(x)}">${esc(x)}</button>`).join('')}</div>
      <div class="grid">
        <section class="panel s8"><header><h2>Delta per minute</h2><span class="meta">aggressive buys minus sells, shares</span></header><div class="chart" id="delta"></div></section>
        <section class="panel s4"><header><h2>${esc(tapeSymbol)}</h2><span class="meta">${last.end ? clockIso(last.end) : ''} ET</span></header>
          <dl class="kv"><dt>Last</dt><dd>${num(last.close)}</dd><dt>Delta, last minute</dt><dd>${signed(last.delta, 0)}</dd><dt>CVD, session</dt><dd>${signed(last.cvd, 0)}</dd><dt>Volume, ${bars.length} min</dt><dd>${num(sum(bars, 'volume'), 0)}</dd><dt>Spread, last minute</dt><dd>${num(last.spread_bps, 2)} bps</dd></dl>
          <h3 class="muted" style="font-size:12px;font-weight:400;margin:16px 0 4px">Large prints and sweeps</h3>
          <ul class="rows">${d.events.slice(-5).reverse().map((e) => `<li><span class="t">${clockIso(e.ts)}</span><div class="what">${esc(e.kind[0].toUpperCase() + e.kind.slice(1))}, ${e.side > 0 ? 'buy' : e.side < 0 ? 'sell' : 'unsided'}<span>${num(e.shares, 0)} sh at ${num(e.price)} · ${num(e.notional / 1e6, 2)}M USD</span></div></li>`).join('') || '<li><div class="empty" style="grid-column:1/-1">None in the last hour.</div></li>'}</ul>
        </section>
        <section class="panel s12"><header><h2>All symbols</h2><span class="meta">last ${bars.length} one-minute bars</span></header>
          <div class="table-wrap"><table class="stack"><thead><tr><th>Symbol</th><th class="n">Last</th><th class="n">Delta, last min</th><th class="n">Delta, 60 min</th><th class="n">CVD</th><th class="n">Volume, 60 min</th><th class="n">Spread bps</th><th class="n">Prints / sweeps</th></tr></thead><tbody>
          ${syms.map((x) => { const b = t.symbols[x].bars, l = b.at(-1) || {}, ev = t.symbols[x].events; return `<tr><td>${esc(x)}</td><td class="n key">${num(l.close)}</td><td class="n more">${signed(l.delta, 0)}</td><td class="n more">${signed(sum(b, 'delta'), 0)}</td><td class="n hide-sm">${signed(l.cvd, 0)}</td><td class="n hide-sm">${num(sum(b, 'volume'), 0)}</td><td class="n more">${num(l.spread_bps, 2)}</td><td class="n hide-sm">${ev.filter((e) => e.kind === 'large print').length} / ${ev.filter((e) => e.kind === 'sweep').length}</td></tr>`; }).join('')}
          </tbody></table></div></section>
      </div>`;
    const labels = bars.map((b) => clockIso(b.end));
    const wide = matchMedia('(min-width: 1101px)').matches, every = matchMedia('(max-width: 600px)').matches ? 20 : 10;
    const xAt = labels.map((l, i) => (Number(l.slice(3)) % every === 0 ? i : -1)).filter((i) => i >= 0);
    const chart = document.getElementById('delta'), panel = chart.closest('.panel'), side = panel.nextElementSibling;
    const height = wide ? Math.max(220, side.offsetHeight - (panel.offsetHeight - chart.offsetHeight)) : 240;
    Charts.diverging(chart, { labels, values: bars.map((b) => b.delta), above: 'Buying', below: 'Selling', format: Charts.fmt.int, height, xAt });
  }

  // ---------- Signals ----------
  async function signals() {
    const [d, s] = await Promise.all([api('/api/signals'), statusCache ? Promise.resolve(statusCache) : api('/api/status')]);
    scope(s);
    const o = d.outcomes, rows = d.signals, c = (k) => o[k] || 0;
    const traded = c('traded') + c('in trade');
    const refused = c('refused by risk'), skipped = c('skipped') + c('vetoed by Jev') + c('not filled');
    const sub = rows.length ? `${plural(rows.length, 'signal')} on ${d.day}: ${traded} traded, ${refused} refused by the risk limits.` : `No signals on ${d.day}.`;
    const r = d.research;
    view.innerHTML = `
      ${titleRow('Signals', sub, { actions: stamp() })}
      <section class="kpis"><div class="kpi"><div class="kpi-label">Signals</div><div class="kpi-value">${rows.length}</div></div>
        <div class="kpi"><div class="kpi-label">Traded</div><div class="kpi-value">${traded}</div></div>
        <div class="kpi"><div class="kpi-label">Refused by risk</div><div class="kpi-value">${refused}</div></div>
        <div class="kpi"><div class="kpi-label">Skipped</div><div class="kpi-value">${skipped}</div></div>
        <div class="kpi"><div class="kpi-label">Setup not enabled</div><div class="kpi-value">${c('not enabled')}</div></div></section>
      <div class="grid" style="margin-top:24px">
        <section class="panel s8"><header><h2>Today's signals</h2><span class="meta">newest first</span></header>
          <div class="table-wrap"><table class="stack"><thead><tr><th>Time</th><th>Setup</th><th>What the tape showed</th><th>Outcome</th><th class="n">Net USD</th></tr></thead><tbody>
          ${rows.slice(0, 12).map((x) => `<tr><td class="more">${clock(x.ts_ns)}</td><td>${esc(x.symbol)}<span class="sub">${esc(x.setup)} ${esc(SETUP_NAMES[x.setup] || '')}</span></td><td class="wrap more">${esc(x.reason)}${x.why ? `<span class="sub">${esc(x.why)}</span>` : ''}</td><td class="key">${outcomePill(x.outcome)}</td><td class="n more">${x.net == null ? '<span class="muted">–</span>' : signed(x.net)}</td></tr>`).join('') || emptyRow(5, 'No signals yet today.')}
          </tbody></table></div></section>
        <section class="panel s4"><header><h2>Phase 5 verdict</h2><span class="meta">${r ? `${r.days} days, out of sample` : ''}</span></header>
          ${r ? `<div class="table-wrap"><table><thead><tr><th>Setup</th><th class="n">Trades</th><th class="n">Mean R</th><th class="n">p (Holm)</th><th>Result</th></tr></thead><tbody>
            ${r.setups.map((x) => `<tr><td title="${esc(SETUP_NAMES[x.setup] || '')}">${esc(x.setup)}</td><td class="n">${x.trades}</td><td class="n">${signed(x.mean_r, 2)}</td><td class="n">${num(x.p_holm, 2)}</td><td>${x.passes ? '<span class="pill pos">Passed</span>' : '<span class="pill">Not passed</span>'}</td></tr>`).join('')}
            </tbody></table></div><p class="muted" style="margin-top:12px;line-height:18px">${r.passed.length ? `On paper: ${esc(r.passed.join(', '))}.` : 'No setup passed, so none trades on paper.'} Net of costs; a setup passes only with 30+ test trades, p &lt; 0.05 after Holm, deflated Sharpe 0.95+ and a win over random entries.</p>`
            : '<div class="empty">The Phase 5 walk-forward test has not run yet. Its results show here when reports/phase5/walkforward.json exists.</div>'}
        </section>
      </div>`;
  }

  // ---------- Approvals ----------
  async function approvals() {
    const [d, s] = await Promise.all([api('/api/approvals'), statusCache ? Promise.resolve(statusCache) : api('/api/status')]);
    scope(s);
    view.innerHTML = titleRow('Approvals', 'Nothing waits for your approval in paper trading.') +
      `<section class="panel"><header><h2>Waiting for you</h2><span class="meta">0</span></header><div class="empty">${esc(d.note)}</div></section>`;
  }

  // ---------- Journal ----------
  const jf = { day: '', event: '', source: '', q: '', limit: 200, exclude: 'equity_mark' };
  const SKIP = new Set(['ts', 'ts_ns', 'event', 'source']);
  const detailOf = (r) => Object.entries(r).filter(([k]) => !SKIP.has(k)).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
  async function journal() {
    const q = new URLSearchParams(Object.entries(jf).filter(([, v]) => v !== ''));
    const [d, s] = await Promise.all([api('/api/journal?' + q), statusCache ? Promise.resolve(statusCache) : api('/api/status')]);
    scope(s);
    jf.day = d.day;
    const shown = d.rows.length;
    const evOpts = Object.entries(d.events).map(([k, n]) => `<option value="${esc(k)}"${jf.event === k ? ' selected' : ''}>${esc(k)} (${n})</option>`).join('');
    view.innerHTML = `
      ${titleRow('Journal', `Every signal, risk decision, order, fill, alert and error, newest first.${d.bad_lines ? ` ${d.bad_lines} unreadable lines on this day.` : ''}`, { bad: d.bad_lines > 0 })}
      <div class="filters">
        <select class="input" id="j-day" aria-label="Day">${(d.days.length ? d.days : [d.day]).map((x) => `<option${x === d.day ? ' selected' : ''}>${esc(x)}</option>`).join('')}</select>
        <select class="input" id="j-event" aria-label="Event"><option value="">All but equity marks</option>${evOpts}</select>
        <select class="input" id="j-source" aria-label="Written by"><option value="">All writers</option>${['node', 'watchdog', 'ops', 'dashboard'].map((x) => `<option${jf.source === x ? ' selected' : ''}>${x}</option>`).join('')}</select>
        <input class="input" id="j-q" type="search" placeholder="Search, e.g. SPY or rejected" value="${esc(jf.q)}">
        <span class="spacer"></span><span class="stamp">${shown ? `1–${shown} of ${d.total}` : '0 records'}</span>
      </div>
      <section class="panel"><div class="table-wrap"><table class="stack"><thead><tr><th>Time ET</th><th>Writer</th><th>Event</th><th>Detail</th></tr></thead><tbody>
        ${d.rows.map((r) => `<tr><td class="n" style="text-align:left">${clockIso(r.ts, true)}</td><td class="more">${esc(r.source)}</td><td class="key">${esc(r.event)}</td><td class="more"><div class="truncate" title="${esc(detailOf(r))}">${esc(detailOf(r))}</div></td></tr>`).join('') || emptyRow(4, 'No records match.')}
      </tbody></table></div>
      ${shown < d.total ? `<div style="margin-top:16px"><button class="btn" data-more>Show ${Math.min(200, d.total - shown)} more</button></div>` : ''}</section>`;
    const on = (id, k) => document.getElementById(id).addEventListener('change', (e) => { jf[k] = e.target.value; jf.limit = 200; if (k === 'event') jf.exclude = e.target.value ? '' : 'equity_mark'; render(); });
    on('j-day', 'day'); on('j-event', 'event'); on('j-source', 'source');
    const qi = document.getElementById('j-q');
    qi.addEventListener('keydown', (e) => { if (e.key === 'Enter') { jf.q = qi.value.trim(); render(); } });
    view.querySelector('[data-more]')?.addEventListener('click', () => { jf.limit += 200; render(); });
  }

  // ---------- Proposals ----------
  async function proposals() {
    const [d, s] = await Promise.all([api('/api/proposals'), statusCache ? Promise.resolve(statusCache) : api('/api/status')]);
    scope(s);
    view.innerHTML = titleRow('Proposals', 'Changes the learning process suggests. Each one is a branch you approve; nothing changes the running bot by itself.') +
      `<section class="panel"><header><h2>Open proposals</h2><span class="meta">${d.items.length}</span></header><div class="table-wrap"><table class="stack"><thead><tr><th>Proposal</th><th>Branch</th><th>Walk-forward</th><th>Paper</th><th>Status</th></tr></thead><tbody>
      ${d.items.map((p) => `<tr><td>${esc(p.title || p.file)}</td><td class="id more">${esc(p.branch || '–')}</td><td class="more">${esc(p.walk_forward || '–')}</td><td class="more">${esc(p.paper || '–')}</td><td class="key"><span class="pill">${esc(p.status || 'proposed')}</span></td></tr>`).join('') || emptyRow(5, 'No proposals yet. They start after Phase 7: each one arrives as a pull request with walk-forward and paper evidence for you to approve.')}
      </tbody></table></div></section>`;
  }

  // ---------- Health ----------
  async function health() {
    const [h, rep, pp, s] = await Promise.all([api('/api/health'), api('/api/reports'), api('/api/paper'), statusCache ? Promise.resolve(statusCache) : api('/api/status')]);
    scope(s);
    const bad = h.checks.filter((c) => c.state === 'bad');
    const sub = bad.length ? `${plural(bad.length, 'problem')}: ${bad.map((c) => c.name).join(', ')}.` : 'Everything FlowForge depends on is answering.';
    const pill = (st) => ({ ok: '<span class="pill pos">OK</span>', bad: '<span class="pill neg">Problem</span>', warn: '<span class="pill warn">Check</span>', info: '<span class="pill">Info</span>' })[st];
    view.innerHTML = `
      ${titleRow('Health', sub, { bad: bad.length > 0, actions: stamp() })}
      <div class="grid">
        <section class="panel s8"><header><h2>Checks</h2><span class="meta">every 10 seconds</span></header><div class="table-wrap"><table class="stack"><thead><tr><th>Check</th><th>Area</th><th>Result</th><th>Detail</th></tr></thead><tbody>
          ${h.checks.map((c) => `<tr><td>${esc(c.name)}</td><td class="more">${esc(c.group)}</td><td class="key">${pill(c.state)}</td><td class="more wrap">${esc(c.detail)}</td></tr>`).join('')}
        </tbody></table></div></section>
        <section class="panel s4"><header><h2>Daily reports</h2><span class="meta">reports/daily</span></header>
          <ul class="rows">${rep.items.slice(0, 8).map((r) => `<li><span class="t">${esc(r.day.slice(5))}</span><div class="what">${r.kind === 'eod' ? 'End of day' : 'Pre-market check'} ${r.level === 'critical' ? '<span class="pill neg">Problem</span>' : ''}<span>${esc((r.checks || []).filter((c) => c.ok === false).map((c) => c.name).join(', ') || 'all clear')}</span></div></li>`).join('') || '<li><div class="empty" style="grid-column:1/-1">No reports yet. The daily service writes one at 09:00 and one after the close.</div></li>'}</ul></section>
        ${paperGate(pp)}
      </div>`;
  }

  function paperGate(pp) {
    const card = pp.scorecard;
    const sub = card ? `${plural(card.trades, 'trade')} in ${plural(card.sessions, 'session')}` : 'after each close';
    const yes = (ok) => (ok ? '<span class="pill pos">Met</span>' : '<span class="pill neg">Not yet</span>');
    const DRILLS = [['instruments', 'symbols load from IBKR'], ['round-trip', 'buy 1, stop at IBKR, sell'], ['stop', 'stop triggers and sells'], ['takeover', 'watchdog sells after a crash'], ['restart', 'restart finds unknown shares']];
    const last = (name) => pp.drills.find((d) => d.drill === name);
    const drillRows = DRILLS.map(([name, what]) => {
      const d = last(name);
      const res = !d ? '<span class="pill">Not run</span>' : d.passed ? '<span class="pill pos">Pass</span>' : '<span class="pill neg">Fail</span>';
      return `<li><span class="t">${d ? esc((d.started || '').slice(5, 10)) : '–'}</span><div class="what">${esc(name)} ${res}<span>${esc(d && d.failing.length ? d.failing.join(', ') : what)}</span></div></li>`;
    });
    const k = pp.soaks[0];
    drillRows.push(`<li><span class="t">${k ? esc(k.file.slice(9, 11) + '-' + k.file.slice(11, 13)) : '–'}</span><div class="what">restart soak ${!k ? '<span class="pill">Not run</span>' : k.clean === k.cycles ? '<span class="pill pos">Pass</span>' : '<span class="pill neg">Fail</span>'}<span>${k ? `${k.clean} of ${k.cycles} restarts clean` : '8 stops and starts, each must come back clean'}</span></div></li>`);
    return `
        <section class="panel s8"><header><h2>Paper gate</h2><span class="meta">${sub}</span></header><div class="table-wrap"><table class="stack"><thead><tr><th>Phase 7 check</th><th>Result</th><th>Detail</th></tr></thead><tbody>
          ${card ? card.checks.map((c) => `<tr><td>${esc(c.name)}</td><td class="key">${yes(c.ok)}</td><td class="more wrap">${esc(c.detail)}</td></tr>`).join('') : '<tr><td colspan="3" class="empty">No scorecard yet. It is written after the first paper session closes.</td></tr>'}
        </tbody></table></div></section>
        <section class="panel s4"><header><h2>Drills</h2><span class="meta">reports/paper</span></header>
          <ul class="rows">${drillRows.join('')}</ul></section>`;
  }

  // ---------- Settings ----------
  async function settings() {
    const [d, s] = await Promise.all([api('/api/settings'), statusCache ? Promise.resolve(statusCache) : api('/api/status')]);
    scope(s);
    const L = d.risk_limits;
    const label = (k) => k.replace(/_/g, ' ').replace(/\bpct\b/, '%').replace(/\bbps\b/, 'bps').replace(/^./, (c) => c.toUpperCase());
    const val = (v) => (typeof v === 'boolean' ? (v ? 'yes' : 'no') : typeof v === 'number' ? nf(Number.isInteger(v) ? 0 : 2).format(v) : String(v));
    const groups = Object.entries(L).filter(([, v]) => v && typeof v === 'object');
    const j = d.jev;
    view.innerHTML = `
      ${titleRow('Settings', 'Read only. Limits change by editing config/risk_limits.yaml and committing it; nothing on this screen can change them.')}
      <section class="setting"><div><h2>Kill switch</h2><p class="desc">Stops new trades, sells what is held and keeps the day halted. Anyone can turn it on. Turning it off is deliberate and only from the command line: <code>docker compose run --rm riskctl release</code></p></div>
        <div class="panel"><dl class="kv"><dt>State</dt><dd>${s.kill.on ? `<span class="neg">on</span>` : 'off'}</dd><dt>Reason</dt><dd>${esc(s.kill.reason || '–')}</dd><dt>Flag file</dt><dd>${esc(d.kill_file)}</dd></dl>
          <div style="margin-top:16px">${s.kill.on ? '<span class="muted">Already on.</span>' : '<button class="btn danger" data-kill>Turn kill switch on</button>'}</div></div></section>
      <section class="setting"><div><h2>Risk limits</h2><p class="desc">Read once when the node starts and checked by its fingerprint. Only you change these, through a git commit.</p><p class="desc">sha256 <code>${esc(d.risk_limits_sha256.slice(0, 16))}</code></p></div>
        <div class="panel"><dl class="kv">${groups.map(([g, v]) => `<dt class="group">${esc(label(g))}</dt>${Object.entries(v).map(([k, x]) => `<dt>${esc(label(k))}</dt><dd>${esc(val(x))}</dd>`).join('')}`).join('')}</dl></div></section>
      <section class="setting"><div><h2>Setups on paper</h2><p class="desc">Only setups that passed the Phase 5 walk-forward test may trade on paper.</p></div>
        <div class="panel">${!d.research_ran ? '<div class="empty">The Phase 5 test has not run yet, so no setup is enabled.</div>' : d.paper_setups.length ? `<dl class="kv">${d.paper_setups.map((x) => `<dt>${esc(x)}</dt><dd>${esc(SETUP_NAMES[x] || '')}</dd>`).join('')}</dl>` : '<div class="empty">None. No setup passed the Phase 5 test.</div>'}</div></section>
      <section class="setting"><div><h2>Jev gate</h2><p class="desc">An outside check asked before an entry. A timeout or any error means no trade. Off until it beats the rules alone out of sample.</p></div>
        <div class="panel"><dl class="kv">${j.valid ? `<dt>State</dt><dd>${j.enabled ? (j.shadow ? 'shadow: asks, never blocks' : 'on: can veto') : 'off'}</dd><dt>Model</dt><dd>${esc(j.model)}</dd><dt>Deadline</dt><dd>${j.deadline_ms} ms</dd><dt>Threshold</dt><dd>${num(j.threshold, 2)}</dd>` : `<dt>Config</dt><dd class="neg">unreadable (${esc(j.error)})</dd>`}</dl></div></section>
      <section class="setting"><div><h2>Phone alerts</h2><p class="desc">Set in .env: <code>NTFY_TOPIC</code>, or <code>TELEGRAM_BOT_TOKEN</code> and <code>TELEGRAM_CHAT_ID</code>. Values are never shown here.</p></div>
        <div class="panel"><dl class="kv"><dt>ntfy</dt><dd>${d.alerts.ntfy ? 'set' : '<span class="muted">not set</span>'}</dd><dt>Telegram</dt><dd>${d.alerts.telegram ? 'set' : '<span class="muted">not set</span>'}</dd></dl></div></section>
      <section class="setting"><div><h2>Other screens</h2><p class="desc">Also on the rail on a computer.</p></div>
        <div class="panel"><dl class="kv"><dt><a href="#/approvals">Approvals</a></dt><dd>0 waiting</dd><dt><a href="#/proposals">Proposals</a></dt><dd>learning changes</dd></dl></div></section>`;
  }

  // ---------- router ----------
  const ROUTES = { live: [live, 5000], tape: [tape, 5000], signals: [signals, 10000], approvals: [approvals, 0], journal: [journal, 0], proposals: [proposals, 0], health: [health, 10000], settings: [settings, 0] };
  const current = () => (location.hash.replace(/^#\//, '') || 'live').split('?')[0];
  async function render() {
    const name = ROUTES[current()] ? current() : 'live';
    document.querySelectorAll('.rail a').forEach((a) => { if (a.dataset.route === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    document.title = `${name[0].toUpperCase() + name.slice(1)} · FlowForge`;
    try { await ROUTES[name][0](); } catch (err) { view.innerHTML = failed(err); }
  }
  function schedule() {
    clearInterval(timer);
    const every = (ROUTES[current()] || ROUTES.live)[1];
    if (every) timer = setInterval(() => { if (!document.getElementById('kill-dialog').open) { statusCache = null; render(); } }, every);
  }
  window.addEventListener('hashchange', () => { statusCache = null; render(); schedule(); });

  // ---------- the kill switch (on only) ----------
  const dlg = document.getElementById('kill-dialog'), form = document.getElementById('kill-form'), kerr = document.getElementById('kill-error');
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-kill]')) { kerr.hidden = true; dlg.showModal(); }
    if (e.target.closest('[data-retry]')) render();
    const tab = e.target.closest('[data-sym]');
    if (tab) { tapeSymbol = tab.dataset.sym; render(); }
  });
  form.addEventListener('submit', async (e) => {
    if (e.submitter?.value !== 'go') return;
    e.preventDefault();
    const go = document.getElementById('kill-go');
    go.disabled = true;
    try {
      await api('/api/kill', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-FlowForge-Action': 'engage-kill' }, body: JSON.stringify({ reason: document.getElementById('kill-reason').value.trim() }) });
      dlg.close(); statusCache = null; render();
    } catch (err) {
      kerr.innerHTML = `<b>Not turned on:</b> ${esc(err.message)}. Use the command line: docker compose run --rm riskctl kill`;
      kerr.hidden = false;
    } finally { go.disabled = false; }
  });

  render(); schedule();
})();
