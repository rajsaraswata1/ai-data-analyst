/* AI Data Analyst – single-page UI (vanilla JS). All dynamic text is inserted with textContent (no innerHTML). */
(function () {
  'use strict';
  const state = { user: null, datasets: [], rels: [], view: 'dashboard', cid: null, busy: false };
  const $ = (id) => document.getElementById(id);

  function h(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
    return n;
  }
  function toast(msg, kind) {
    const t = h('div', { class: 'toast ' + (kind || '') }, msg);
    $('toasts').appendChild(t);
    setTimeout(() => t.remove(), kind === 'err' ? 6000 : 3500);
  }
  async function api(path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' };
    if (opts.json) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.json); }
    if (opts.form) init.body = opts.form;
    let r;
    try { r = await fetch(path, init); } catch (e) { throw new Error('Cannot reach the server. Check your connection.'); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* non-JSON */ }
    if (r.status === 401 && !path.startsWith('/api/auth/')) { showAuth(); throw new Error('Please sign in.'); }
    if (!r.ok) {
      const msg = (data && data.error && data.error.message) || (data && data.failed && data.failed.map((f) => `${f.filename}: ${f.error}`).join(' ')) || 'Request failed.';
      throw new Error(msg);
    }
    return data;
  }

  // ---------------------------------------------------------------- auth
  function showAuth() { state.user = null; $('app').classList.add('hidden'); $('auth').classList.remove('hidden'); }
  function showApp() { $('auth').classList.add('hidden'); $('app').classList.remove('hidden'); $('whoami').textContent = 'Signed in as ' + state.user.username; buildNav(); go(state.view); }
  async function authSubmit(path) {
    $('auth-error').textContent = '';
    try {
      const d = await api(path, { method: 'POST', json: { username: $('auth-user').value.trim(), password: $('auth-pass').value } });
      state.user = d.user; $('auth-pass').value = ''; showApp();
    } catch (e) { $('auth-error').textContent = e.message; }
  }

  // ---------------------------------------------------------------- nav
  const NAV = [['dashboard', 'Dashboard'], ['datasets', 'Datasets'], ['analysis', 'Analysis'], ['quality', 'Data Quality'], ['anomalies', 'Anomalies'], ['forecast', 'Forecast'], ['reports', 'Reports'], ['settings', 'Settings']];
  function buildNav() {
    const nav = $('nav'); nav.textContent = '';
    NAV.forEach(([id, label]) => nav.appendChild(h('button', { class: 'nav-item' + (state.view === id ? ' active' : ''), 'data-view': id, onclick: () => go(id) }, label)));
  }
  async function go(view) {
    state.view = view; buildNav();
    const main = $('main'); main.textContent = '';
    try {
      if (!['settings', 'datasets'].includes(view) || view === 'datasets') await refreshDatasets();
      await ({ dashboard: viewDashboard, datasets: viewDatasets, analysis: viewAnalysis, quality: viewQuality, anomalies: viewAnomalies, forecast: viewForecast, reports: viewReports, settings: viewSettings })[view](main);
    } catch (e) { main.appendChild(h('div', { class: 'errbox' }, e.message)); }
  }
  async function refreshDatasets() { const d = await api('/api/datasets'); state.datasets = d.datasets; state.rels = d.relationships; }
  const head = (title, sub, ...actions) => h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, title), h('div', { class: 'muted' }, sub || '')), h('div', { class: 'row' }, actions));
  const loading = (msg) => h('div', { class: 'card' }, h('span', { class: 'spinner' }), msg || 'Loading…');

  function emptyState() {
    return h('div', { class: 'empty' }, h('h2', {}, 'No datasets yet'), h('p', { class: 'muted' }, 'Upload one or more CSV files, or load the bundled sample data (sales, customers, products) to explore.'),
      h('div', { class: 'row', style: 'justify-content:center' }, h('button', { class: 'btn primary', onclick: loadSamples }, 'Load sample datasets'), h('button', { class: 'btn', onclick: () => go('datasets') }, 'Upload CSV files')));
  }
  async function loadSamples() {
    try { const d = await api('/api/datasets/samples', { method: 'POST' }); toast(d.loaded.length ? `Loaded ${d.loaded.join(', ')}` : 'Sample data already loaded.', 'ok'); go(state.view === 'datasets' ? 'datasets' : 'dashboard'); }
    catch (e) { toast(e.message, 'err'); }
  }

  // ---------------------------------------------------------------- dashboard
  async function viewDashboard(main) {
    main.appendChild(head('Dashboard', 'Auto-generated from your data'));
    if (!state.datasets.length) return main.appendChild(emptyState());
    const slot = h('div', {}, loading('Computing KPIs…')); main.appendChild(slot);
    const d = await api('/api/dashboard'); slot.textContent = '';
    if (d.empty || !d.kpis || !d.kpis.length) return slot.appendChild(h('div', { class: 'card' }, d.note || 'No numeric metric found to build a dashboard.'));
    slot.appendChild(h('div', { class: 'grid kpis' }, d.kpis.map((k) => h('div', { class: 'card kpi' }, h('div', { class: 'label' }, k.label), h('div', { class: 'value ' + (k.positive === true ? 'pos' : k.positive === false ? 'neg' : '') }, k.display)))));
    slot.appendChild(h('div', { class: 'grid charts' }, d.charts.map((c) => h('div', { class: 'card' }, Charts.render(c)))));
    const a = d.anomaly_summary, q = d.quality;
    slot.appendChild(h('div', { class: 'grid charts', style: 'margin-top:14px' },
      h('div', { class: 'card' }, h('h2', {}, 'Anomaly summary'), h('div', {}, `${a.total} anomalous ${a.metric} values among ${(a.rows_scanned || 0).toLocaleString()} rows.`), h('div', { class: 'muted small' }, a.rule || ''),
        ...a.top.map((t) => h('div', { class: 'small', style: 'margin-top:6px' }, `${t.id || 'row ' + t.row}: ${t.actual} (expected ≤ ${t.expected_high})`)), h('button', { class: 'btn small', style: 'margin-top:10px', onclick: () => go('anomalies') }, 'Open anomaly analysis')),
      h('div', { class: 'card' }, h('h2', {}, 'Data quality'), h('div', { class: 'score' }, q.score.toFixed(1) + ' / 100'), h('div', { class: 'bar' }, h('div', { style: `width:${q.score}%` })), h('div', { class: 'muted small', style: 'margin-top:6px' }, `${q.issue_count} issue(s) in ${d.fact_table}`), h('button', { class: 'btn small', style: 'margin-top:10px', onclick: () => go('quality') }, 'Open data quality'))));
    slot.appendChild(h('div', { class: 'card', style: 'margin-top:14px' }, h('h2', {}, 'Key insights'), h('ul', { class: 'insights' }, d.insights.map((i) => h('li', {}, i)))));
  }

  // ---------------------------------------------------------------- datasets
  async function uploadFiles(files) {
    if (!files.length) return;
    const fd = new FormData(); [...files].forEach((f) => fd.append('files', f));
    try {
      const d = await api('/api/datasets', { method: 'POST', form: fd });
      toast(`Uploaded ${d.uploaded.length} file(s).`, 'ok'); d.failed.forEach((f) => toast(`${f.filename}: ${f.error}`, 'err'));
    } catch (e) { toast(e.message, 'err'); }
    go('datasets');
  }
  async function viewDatasets(main) {
    main.appendChild(head('Datasets', 'Upload and inspect CSV files. Multiple files are joined automatically when keys match.', h('button', { class: 'btn', onclick: loadSamples }, 'Load sample datasets')));
    const input = h('input', { type: 'file', accept: '.csv,text/csv', multiple: true, class: 'hidden', onchange: (e) => uploadFiles(e.target.files) });
    const drop = h('div', { class: 'drop', tabindex: 0, onclick: () => input.click(), ondragover: (e) => { e.preventDefault(); drop.classList.add('over'); }, ondragleave: () => drop.classList.remove('over'), ondrop: (e) => { e.preventDefault(); drop.classList.remove('over'); uploadFiles(e.dataTransfer.files); } }, h('strong', {}, 'Drop CSV files here or click to browse'), h('div', { class: 'muted small' }, 'Up to 10 files · validated for type, size, encoding, headers and duplicates'));
    main.appendChild(drop); main.appendChild(input);
    if (!state.datasets.length) return main.appendChild(h('div', { style: 'margin-top:14px' }, emptyState()));
    main.appendChild(h('h3', {}, 'Uploaded datasets'));
    state.datasets.forEach((d) => {
      const body = h('div', { class: 'card', style: 'margin-bottom:10px' },
        h('div', { class: 'ds' }, h('div', {}, h('strong', {}, d.filename), h('div', { class: 'muted small' }, `table “${d.name}” · ${d.rows.toLocaleString()} rows · ${d.columns} columns · ${(d.size_bytes / 1024).toFixed(0)} KB`)),
          h('div', { class: 'row' }, h('span', { class: 'badge ' + (d.quality_score >= 90 ? 'good' : d.quality_score >= 75 ? 'medium' : 'high') }, 'Quality ' + d.quality_score.toFixed(1)), h('button', { class: 'btn small', onclick: () => showDetail(d.dataset_id, body) }, 'Details'), h('button', { class: 'btn small danger', onclick: () => removeDs(d) }, 'Remove'))),
        ...d.ingest_warnings.map((w) => h('div', { class: 'warn' }, w)));
      main.appendChild(body);
    });
    main.appendChild(h('h3', {}, 'Detected relationships (join suggestions)'));
    main.appendChild(h('div', { class: 'card' }, state.rels.length ? h('ul', { class: 'insights' }, state.rels.map((r) => h('li', {}, r.description + ` · confidence ${(r.confidence * 100).toFixed(0)}%`))) : h('span', { class: 'muted' }, state.datasets.length > 1 ? 'No shared key columns were found between the files.' : 'Upload a second file to detect joins.')));
  }
  async function removeDs(d) {
    if (!confirm(`Remove ${d.filename}?`)) return;
    try { await api('/api/datasets/' + d.dataset_id, { method: 'DELETE' }); toast('Dataset removed.', 'ok'); } catch (e) { toast(e.message, 'err'); }
    go('datasets');
  }
  async function showDetail(id, host) {
    const old = host.querySelector('.detail'); if (old) return old.remove();
    const d = await api('/api/datasets/' + id); const box = h('div', { class: 'detail' });
    box.appendChild(h('h3', {}, 'Schema'));
    box.appendChild(h('div', { class: 'tbl-wrap' }, h('table', {}, h('tr', {}, ['column', 'type', 'role', 'unique', 'missing %', 'stats / top values'].map((x) => h('th', {}, x))),
      d.schema.map((c) => h('tr', {}, h('td', {}, c.name), h('td', {}, c.kind), h('td', {}, c.role), h('td', { class: 'num' }, c.unique), h('td', { class: 'num' }, c.missing_pct), h('td', {}, c.stats ? Object.entries(c.stats).slice(0, 4).map(([k, v]) => `${k}: ${typeof v === 'number' ? +v.toFixed(2) : v}`).join(' · ') : (c.top_values || []).map((t) => `${t.value} (${t.count})`).join(', ')))))));
    box.appendChild(h('h3', {}, 'Preview (first 10 rows)'));
    box.appendChild(h('div', { class: 'tbl-wrap' }, h('table', {}, h('tr', {}, d.preview.columns.map((c) => h('th', {}, c))), d.preview.data.map((r) => h('tr', {}, r.map((v) => h('td', {}, v === null ? '' : String(v))))))));
    host.appendChild(box);
  }

  // ---------------------------------------------------------------- result card (shared by chat + quick views)
  function copyText(text) {
    const done = () => toast('Copied to clipboard.', 'ok');
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => toast('Copy failed.', 'err'));
    else { const ta = h('textarea', {}, text); document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); done(); } catch (e) { toast('Copy failed.', 'err'); } ta.remove(); }
  }
  const codeBlock = (label, code) => h('details', {}, h('summary', {}, label), h('div', {}, h('button', { class: 'btn small', onclick: () => copyText(code) }, 'Copy ' + label), h('pre', { class: 'code' }, code)));
  function tableEl(t) {
    if (!t) return null;
    const shown = t.rows.slice(0, 100);
    return h('details', {}, h('summary', {}, `Result table (${t.total_rows.toLocaleString()} rows)`), h('div', {}, h('div', { class: 'tbl-wrap' }, h('table', {}, h('tr', {}, t.columns.map((c) => h('th', {}, c))),
      shown.map((r) => h('tr', {}, r.map((v) => h('td', { class: typeof v === 'number' ? 'num' : '' }, v === null ? '' : typeof v === 'number' ? (+v.toFixed(4)).toLocaleString() : String(v))))))), t.truncated || t.rows.length > 100 ? h('div', { class: 'muted small' }, 'Showing the first 100 rows. Export CSV for the full result.') : null));
  }
  function renderResult(res, mid, onAsk) {
    const box = h('div', {});
    if (res.ok === false) { box.appendChild(h('div', { class: 'errbox' }, res.error || res.answer)); }
    else {
      box.appendChild(h('p', { class: 'answer' }, res.answer));
      if (res.insights && res.insights.length) box.appendChild(h('ul', { class: 'insights' }, res.insights.map((i) => h('li', {}, i))));
    }
    (res.warnings || []).forEach((w) => box.appendChild(h('div', { class: 'warn' }, w)));
    (res.charts || []).forEach((c) => box.appendChild(h('div', { class: 'card', style: 'margin:8px 0' }, Charts.render(c))));
    if (res.table) box.appendChild(tableEl(res.table));
    if (res.anomalies && res.anomalies.length) box.appendChild(h('details', {}, h('summary', {}, `Why were these flagged? (${res.anomalies.length} shown)`), h('div', {}, res.anomalies.slice(0, 10).map((a) => h('div', { class: 'small', style: 'margin:6px 0' }, h('strong', {}, (a.id || 'row ' + a.row) + ' · '), a.reason)))));
    if (res.sql) box.appendChild(codeBlock('SQL', res.sql));
    if (res.pandas_code) box.appendChild(codeBlock('Pandas', res.pandas_code));
    if (res.method && res.method.length) box.appendChild(h('details', {}, h('summary', {}, 'Analysis method'), h('div', {}, h('ol', { class: 'insights' }, res.method.map((m) => h('li', {}, m))))));
    if (res.validation && res.validation.length) box.appendChild(h('details', {}, h('summary', {}, 'Result validation'), h('div', {}, h('ul', { class: 'insights' }, res.validation.map((m) => h('li', {}, m))))));
    if (res.assumptions && res.assumptions.length) box.appendChild(h('details', {}, h('summary', {}, 'Assumptions'), h('div', {}, h('ul', { class: 'insights' }, res.assumptions.map((m) => h('li', {}, m))))));
    const meta = [];
    if (res.sources && res.sources.length) meta.push('Sources: ' + res.sources.join(', '));
    if (res.tools_used && res.tools_used.length) meta.push('Tools: ' + res.tools_used.join(' → '));
    meta.push(res.llm && res.llm.used_for_narration ? `Explained by ${res.llm.provider} (numbers verified)` : 'Local deterministic engine');
    if (res.elapsed_ms !== undefined) meta.push(res.elapsed_ms + ' ms');
    box.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' }, meta.join(' · ')));
    if (mid && res.ok !== false) box.appendChild(h('div', { class: 'row', style: 'margin-top:8px' }, h('span', { class: 'muted small' }, 'Export:'), ...['md', 'html', 'csv'].filter((f) => f !== 'csv' || res.table).map((f) => h('a', { class: 'btn small', href: `/api/reports/message/${mid}.${f}`, download: '' }, f.toUpperCase()))));
    if (onAsk && res.follow_ups && res.follow_ups.length) box.appendChild(h('div', { class: 'chips', style: 'margin-top:10px' }, res.follow_ups.map((q) => h('button', { class: 'chip', onclick: () => onAsk(q) }, q))));
    return box;
  }

  // ---------------------------------------------------------------- analysis (chat)
  async function ensureConversation() {
    if (state.cid) return state.cid;
    const list = await api('/api/conversations');
    state.cid = list.conversations.length ? list.conversations[0].id : (await api('/api/conversations', { method: 'POST' })).id;
    return state.cid;
  }
  async function askStreaming(cid, question, onStatus) {
    const r = await fetch(`/api/conversations/${cid}/ask/stream`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify({ question }) });
    if (!r.ok || !r.body) {
      let msg = 'Request failed.'; try { msg = (await r.json()).error.message; } catch (e) { /* ignore */ }
      if (r.status === 401) showAuth();
      throw new Error(msg);
    }
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '', final = null;
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const ev = (block.match(/^event: (.*)$/m) || [])[1], data = (block.match(/^data: (.*)$/m) || [])[1];
        if (!ev || !data) continue;
        const payload = JSON.parse(data);
        if (ev === 'status') onStatus(payload.message); else if (ev === 'result') final = payload; else if (ev === 'error') throw new Error(payload.message);
      }
    }
    if (!final) throw new Error('The analysis ended without a result.');
    return final;
  }
  async function viewAnalysis(main) {
    main.appendChild(head('Analysis', 'Ask questions about your data in plain English', h('button', { class: 'btn', onclick: async () => { state.cid = (await api('/api/conversations', { method: 'POST' })).id; go('analysis'); } }, 'New conversation'), state.cid ? h('a', { class: 'btn', href: `/api/reports/conversation/${state.cid}.html`, download: '' }, 'Export session (HTML)') : null));
    if (!state.datasets.length) return main.appendChild(emptyState());
    const cid = await ensureConversation();
    const log = h('div', { class: 'chat-log' }), input = h('input', { placeholder: 'e.g. Which region generated the highest revenue?', maxlength: 1000, 'aria-label': 'Question' }), send = h('button', { class: 'btn primary', type: 'submit' }, 'Ask');
    const hist = await api(`/api/conversations/${cid}/messages`);
    const ask = async (q) => {
      if (state.busy || !q.trim()) return; state.busy = true; send.disabled = true; input.value = '';
      log.appendChild(h('div', { class: 'msg user' }, q));
      const status = h('div', { class: 'msg assistant status' }, h('span', { class: 'spinner' }), 'Starting…'); log.appendChild(status); status.scrollIntoView({ block: 'end' });
      try {
        const f = await askStreaming(cid, q, (m) => { status.textContent = ''; status.appendChild(h('span', { class: 'spinner' })); status.appendChild(document.createTextNode(m)); });
        status.replaceWith(h('div', { class: 'msg assistant' }, renderResult(f.result, f.message_id, ask)));
      } catch (e) { status.replaceWith(h('div', { class: 'msg assistant' }, h('div', { class: 'errbox' }, e.message))); toast(e.message, 'err'); }
      state.busy = false; send.disabled = false; input.focus();
    };
    if (!hist.messages.length) log.appendChild(h('div', { class: 'card' }, h('strong', {}, 'Try asking:'), h('div', { class: 'chips', style: 'margin-top:8px' }, ['Which region generated the highest revenue?', 'Show monthly sales trends.', 'Which products are underperforming?', 'What are the top five customers?', 'Detect anomalies in the dataset.', 'Forecast sales for the next 6 months.', 'Which customer segment generated the highest revenue?', 'Give me an executive summary'].map((q) => h('button', { class: 'chip', onclick: () => ask(q) }, q)))));
    hist.messages.forEach((m) => log.appendChild(m.role === 'user' ? h('div', { class: 'msg user' }, m.content) : h('div', { class: 'msg assistant' }, m.result ? renderResult(m.result, m.id, ask) : m.content)));
    const form = h('form', { class: 'composer', onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, input, send);
    main.appendChild(h('div', { class: 'chat' }, log, form)); input.focus();
  }

  // ---------------------------------------------------------------- quality / anomalies / forecast
  async function viewQuality(main) {
    main.appendChild(head('Data Quality', 'Computed deterministically at upload – score = 40% completeness + 20% uniqueness + 25% validity + 15% structure'));
    if (!state.datasets.length) return main.appendChild(emptyState());
    for (const ds of state.datasets) {
      const d = await api('/api/datasets/' + ds.dataset_id), q = d.quality;
      main.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' }, h('div', { class: 'ds' }, h('div', {}, h('h2', {}, d.filename), h('div', { class: 'muted small' }, Object.entries(q.components).map(([k, v]) => `${k} ${v}%`).join(' · '))), h('div', { class: 'score' }, q.score.toFixed(1))),
        h('div', { class: 'bar' }, h('div', { style: `width:${q.score}%` })),
        q.issues.length ? h('div', { class: 'tbl-wrap', style: 'margin-top:10px' }, h('table', {}, h('tr', {}, ['severity', 'issue', 'affected rows', 'recommendation'].map((x) => h('th', {}, x))), q.issues.map((i) => h('tr', {}, h('td', {}, h('span', { class: 'badge ' + i.severity }, i.severity)), h('td', {}, i.message), h('td', { class: 'num' }, i.affected_rows.toLocaleString()), h('td', {}, i.recommendation))))) : h('div', { class: 'muted', style: 'margin-top:8px' }, 'No issues detected.')));
    }
  }
  async function quick(main, kind, label) {
    const slot = h('div', {}, loading(label)); main.appendChild(slot);
    try { const r = await api('/api/quick/' + kind); slot.textContent = ''; slot.appendChild(h('div', { class: 'card' }, renderResult(r, null, null))); } catch (e) { slot.textContent = ''; slot.appendChild(h('div', { class: 'errbox' }, e.message)); }
  }
  async function viewAnomalies(main) { main.appendChild(head('Anomalies', 'IQR / z-score (auto-selected) plus Isolation Forest, with an explanation for every flag')); if (!state.datasets.length) return main.appendChild(emptyState()); await quick(main, 'anomalies', 'Scanning for anomalies…'); }
  async function viewForecast(main) {
    main.appendChild(head('Forecast', 'Monthly baseline forecast with automatic model selection (estimates, not guarantees)'));
    if (!state.datasets.length) return main.appendChild(emptyState());
    const sel = h('select', {}, [3, 6, 12].map((n) => h('option', { value: n, selected: n === 6 }, n + ' months'))), out = h('div', {});
    const run = async () => { out.textContent = ''; const slot = h('div', {}, loading('Fitting models…')); out.appendChild(slot); try { const r = await api('/api/quick/forecast?horizon=' + sel.value); slot.textContent = ''; slot.appendChild(h('div', { class: 'card' }, renderResult(r, null, null))); } catch (e) { slot.textContent = ''; slot.appendChild(h('div', { class: 'errbox' }, e.message)); } };
    main.appendChild(h('div', { class: 'row', style: 'margin-bottom:12px' }, h('label', {}, 'Horizon', sel), h('button', { class: 'btn primary', onclick: run }, 'Run forecast'))); main.appendChild(out); run();
  }

  // ---------------------------------------------------------------- reports / settings
  async function viewReports(main) {
    main.appendChild(head('Reports', 'Export any conversation as Markdown or HTML (with charts, SQL, methodology, anomalies and assumptions). Single answers can be exported from the Analysis page, including CSV.'));
    const d = await api('/api/conversations');
    if (!d.conversations.length) return main.appendChild(h('div', { class: 'empty' }, h('h2', {}, 'Nothing to export yet'), h('p', { class: 'muted' }, 'Ask a question in Analysis first.'), h('button', { class: 'btn primary', onclick: () => go('analysis') }, 'Go to Analysis')));
    d.conversations.forEach((c) => main.appendChild(h('div', { class: 'card ds', style: 'margin-bottom:8px' }, h('div', {}, h('strong', {}, c.title || 'Conversation'), h('div', { class: 'muted small' }, new Date(c.created_at * 1000).toLocaleString())), h('div', { class: 'row' }, ['md', 'html'].map((f) => h('a', { class: 'btn small', href: `/api/reports/conversation/${c.id}.${f}`, download: '' }, f.toUpperCase())), h('button', { class: 'btn small', onclick: () => { state.cid = c.id; go('analysis'); } }, 'Open')))));
  }
  async function viewSettings(main) {
    main.appendChild(head('Settings & status', 'Runtime configuration (secrets are never shown)'));
    const [hl, mt] = await Promise.all([api('/api/health'), api('/api/metrics')]);
    main.appendChild(h('div', { class: 'card' }, h('h2', {}, 'AI engine'), h('div', {}, 'LLM provider: ', h('strong', {}, hl.llm_provider)), h('p', { class: 'muted small' }, 'Set LLM_PROVIDER=openai with LLM_API_KEY, LLM_BASE_URL and LLM_MODEL in the environment to let an LLM refine plans and phrase answers. Numbers always come from SQL/pandas.')));
    main.appendChild(h('div', { class: 'card', style: 'margin-top:12px' }, h('h2', {}, 'Cache'), h('div', {}, `Items ${mt.cache.items} · hits ${mt.cache.hits} · misses ${mt.cache.misses} · hit rate ${(mt.cache.hit_rate * 100).toFixed(0)}%`)));
    const rows = Object.entries(mt.tools);
    main.appendChild(h('div', { class: 'card', style: 'margin-top:12px' }, h('h2', {}, 'Tool metrics (this server process)'), rows.length ? h('div', { class: 'tbl-wrap' }, h('table', {}, h('tr', {}, ['tool', 'calls', 'errors', 'avg ms'].map((x) => h('th', {}, x))), rows.map(([k, v]) => h('tr', {}, h('td', {}, k), h('td', { class: 'num' }, v.calls), h('td', { class: 'num' }, v.errors), h('td', { class: 'num' }, v.avg_ms))))) : h('span', { class: 'muted' }, 'No tool calls yet.')));
  }

  // ---------------------------------------------------------------- boot
  window.addEventListener('DOMContentLoaded', async () => {
    $('auth-form').addEventListener('submit', (e) => { e.preventDefault(); authSubmit('/api/auth/login'); });
    $('btn-register').addEventListener('click', () => authSubmit('/api/auth/register'));
    $('btn-logout').addEventListener('click', async () => { try { await api('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ } state.cid = null; showAuth(); });
    try { const me = await api('/api/auth/me'); if (me.user) { state.user = me.user; showApp(); } else showAuth(); } catch (e) { showAuth(); }
  });
  window.__app = { renderResult, state }; // exposed for tests
})();
