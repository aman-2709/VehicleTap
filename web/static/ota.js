// The /ota page: asks /api/ota, writes what it gets as text (docs/OTA.md).
//
// Read-only by construction -- one GET, no controls -- and every value is written with textContent,
// never as markup: the numbers and strings come from a file another process wrote
// (tests/test_ota_api.py pins both). The pure helpers are exported on window.Ota for the node tests, and with
// the few DOM builders and the poll loop for the demo view (ota_demo.js), which shows the same answer.
(function () {
  'use strict';

  const POLL_MS = 5000;
  const DASH = '—';

  // ── pure helpers ──

  const short = d => (typeof d === 'string' && d) ? d.replace(/^sha256:/, '').slice(0, 12) : '';

  function ago(s) {
    if (typeof s !== 'number' || !isFinite(s)) return '';
    s = Math.max(0, s);
    if (s < 120) return Math.floor(s) + ' s';
    if (s < 7200) return Math.floor(s / 60) + ' min';
    return Math.floor(s / 3600) + ' h';
  }

  // '2026-08-07T23:03:53.358Z' -> '2026-08-07 23:03:53 UTC'. The server only lets ISO times with a zone through.
  function when(t) {
    if (typeof t !== 'string' || !t) return '';
    return t.replace('T', ' ').replace(/\.\d+/, '').replace(/Z$/, ' UTC');
  }

  const SLOTS = ['booted', 'staged', 'rollback'];
  const FIELDS = [['Version', 'version', v => v], ['Digest', 'digest', short], ['Image created', 'built_at', when]];

  // Rows of the image comparison. A row `differs` when two or more of the images that exist disagree on it:
  // that is what the page highlights, so the eye lands on what an update or a rollback would change.
  function imageRows(os) {
    os = os || {};
    return FIELDS.map(([label, key, show]) => {
      const raw = SLOTS.map(slot => (os[slot] && os[slot][key]) || '');
      const present = raw.filter(v => v);
      const cells = {};
      SLOTS.forEach((slot, i) => { cells[slot] = raw[i] ? show(raw[i]) : ''; });
      return { label, cells, differs: new Set(present).size > 1 };
    });
  }

  // null when all is well; otherwise the banner to show.
  function banner(data) {
    if (!data || typeof data !== 'object') return { kind: 'invalid', text: 'No answer from the dashboard server.' };
    const msg = typeof data.message === 'string' ? data.message : '';
    switch (data.status) {
      case 'ok': return null;
      case 'waiting': return { kind: 'waiting', text: msg || 'Waiting for the host to write the status file.' };
      case 'invalid': return { kind: 'invalid', text: msg || 'The status file cannot be used.' };
      case 'stale':
        return { kind: 'stale', text: typeof data.age_s === 'number'
          ? 'The status file is ' + ago(data.age_s) + ' old. Showing the last state the host wrote.'
          : (msg || 'The status file is out of date.') + ' Showing the last state the host wrote.' };
      default: return { kind: 'invalid', text: 'Unexpected answer from the dashboard server.' };
    }
  }

  // The "next boot" warning, or '' when a reboot would start the image that is running. A version is the
  // clearest name for an image; one that has none is named by its digest, so the warning never goes missing.
  function nextBoot(nb, os) {
    const name = img => (img && (img.version || short(img.digest))) || '';
    const why = !nb ? '' : nb.source === 'rollback' ? 'queued rollback' : nb.source === 'staged' ? 'staged update' : '';
    const next = why ? name(nb) : '';
    if (!next) return '';
    const running = name(os && os.booted);
    if (!running) return 'A reboot would start ' + next + ' (' + why + ').';
    if (running === next) return 'A reboot would start another image with the same name, ' + next + ' (' + why + ').';
    return 'A reboot would start ' + next + ', not the running ' + running + ' (' + why + ').';
  }

  const serviceClass = s => s === 'active' ? 'ok' : s === 'failed' ? 'bad'
    : ['inactive', 'activating', 'deactivating', 'reloading', 'maintenance'].includes(s) ? 'warn' : 'dim';
  const selinuxClass = m => m === 'enforcing' ? 'ok' : m === 'permissive' ? 'warn' : m === 'disabled' ? 'bad' : 'dim';
  const ntpClass = v => v === true ? 'ok' : v === false ? 'bad' : 'dim';
  const diskClass = p => typeof p !== 'number' ? 'dim' : p >= 90 ? 'bad' : p >= 75 ? 'warn' : 'ok';

  const bootedVersion = os => (os && os.booted && os.booted.version) || '';
  const secondsBetween = (later, earlier) => {
    const a = Date.parse(later), b = Date.parse(earlier);
    return isFinite(a) && isFinite(b) ? Math.max(0, (a - b) / 1000) : null;
  };

  // The image's build stamp against the running version (the server derives `check`, web/ota.py build_check).
  function stampFact(os, check) {
    const b = (os && os.build) || {};
    switch (check && check.stamp) {
      case 'match': return { text: b.version + (b.built_at ? ', built ' + when(b.built_at) : ''), cls: 'ok' };
      case 'mismatch': return { text: 'says ' + b.version + ', but bootc runs ' + bootedVersion(os), cls: 'bad' };
      case 'missing': return { text: 'none on the board', cls: 'bad' };
      default: return { text: b.version || '', cls: 'dim' };
    }
  }

  const TOOLS = ['bootc', 'ostree', 'podman'];
  // Each tool with its version ('' for one that did not answer); [] when none answered.
  function toolVersions(tools) {
    tools = tools || {};
    return TOOLS.some(n => tools[n]) ? TOOLS.map(n => ({ name: n, version: tools[n] || '' })) : [];
  }

  // The kernel and tools against the build record; null when the exporter was given no record ('off').
  function recordFact(os, check) {
    const v = bootedVersion(os);
    switch (check && check.record) {
      case 'match': return { text: 'as built for ' + v, cls: 'ok' };
      case 'mismatch': return { cls: 'bad', text: 'not as built for ' + v + ': ' + (check.differs || [])
        .map(d => d.name + ' ' + (d.have || '?') + ' (built with ' + (d.want || '?') + ')').join(', ') };
      case 'none': return { text: 'no build record for ' + v, cls: 'warn' };
      case 'unknown': return { text: '', cls: 'dim' };
      default: return null;
    }
  }

  // '' while the OS data is current; otherwise how old it is. bootc is asked every os_interval_s and its last
  // good answer kept, so data two intervals old means bootc has stopped answering (busy, or failing).
  function osDataNote(data) {
    const os = (data && data.os) || {};
    if (!os.as_of) return os.booted ? '' : 'No answer from bootc yet.';
    const age = secondsBetween(data.generated_at, os.as_of);
    const every = typeof data.os_interval_s === 'number' ? data.os_interval_s : 15;
    if (age === null || age <= 2 * every) return '';
    return 'The image data is from ' + ago(age) + ' before this report: bootc has not answered since.';
  }

  function processFact(e) {
    e = e || {};
    const n = e.processes, out = e.outside_unit, unit = (e.unit || 'esync') + '.service';
    if (typeof n !== 'number') return { text: 'unknown', cls: 'dim' };
    if (n === 0) return { text: 'none running', cls: 'warn' };
    if (typeof out === 'number' && out > 0) {
      return { text: out + ' of ' + n + ' outside ' + unit + ' (started by hand?)', cls: 'bad' };
    }
    return { text: n + ', all in ' + unit, cls: 'ok' };
  }

  const agentName = t => (typeof t === 'string' && t.split('/').filter(Boolean).pop()) || '';
  function agentFact(a) {
    const n = a && a.running;
    if (typeof n !== 'number') return { text: 'unknown', cls: 'dim' };
    if (n === 1) return { text: 'running', cls: 'ok' };
    if (n === 0) return { text: 'not running', cls: 'bad' };
    return { text: n + ' running', cls: 'warn' };
  }

  // The client logs each campaign it is still working on at every session (well under a minute apart), so a
  // campaign it has not logged for CURRENT_S is one it has stopped working on, or the client has stopped.
  const CURRENT_S = 120;
  function campaignRows(list, generatedAt) {
    return (Array.isArray(list) ? list : []).map(c => {
      const age = secondsBetween(generatedAt, c.last_seen);
      return { package: c.package || '', version: c.version || '',
               campaign: typeof c.campaign === 'number' ? '#' + c.campaign : '',
               seen: age === null ? '' : ago(age) + ' ago', current: age !== null && age <= CURRENT_S,
               rollback: c.rollback_version || '', result: c.result || '' };
    });
  }

  // ── shared by both pages: they touch the document only when called ──

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  // A version number stands out from the words around it; one that did not answer is a plain '?'.
  function versionText(v) { return v ? el('span', 'ver', v) : document.createTextNode('?'); }

  function toolsValue(tools) {
    const list = toolVersions(tools);
    if (!list.length) return '';
    const v = el('span');
    list.forEach((t, i) => {
      v.appendChild(document.createTextNode((i ? ' · ' : '') + t.name + ' '));
      v.appendChild(versionText(t.version));
    });
    return v;
  }

  // Asks /api/ota now and every POLL_MS while the tab is visible; `render` gets each answer, null when there is none.
  function watch(render) {
    async function poll() {
      if (document.hidden) return;
      try {
        const r = await fetch('/api/ota', { cache: 'no-store' });
        render(r.ok ? await r.json() : null);
      } catch (e) {
        render(null);
      }
    }
    poll();
    setInterval(poll, POLL_MS);
  }

  window.Ota = { short, ago, when, imageRows, banner, nextBoot, serviceClass, selinuxClass, ntpClass, diskClass,
                 stampFact, toolVersions, recordFact, osDataNote, processFact, agentName, agentFact, campaignRows,
                 bootedVersion, el, versionText, toolsValue, watch };

  // ── the page ──

  if (typeof document === 'undefined' || !document.getElementById('ota-root')) return;

  const $ = id => document.getElementById(id);

  function table(head, rows) {
    const t = el('table', 'ota-table');
    const tr = el('tr');
    head.forEach(h => tr.appendChild(el('th', '', h)));
    t.appendChild(el('thead')).appendChild(tr);
    const body = el('tbody');
    rows.forEach(r => {
      const row = el('tr', r.cls || '');
      r.cells.forEach(c => row.appendChild(c instanceof Node ? c : el('td', '', c || DASH)));
      body.appendChild(row);
    });
    t.appendChild(body);
    return t;
  }

  // `value` is text, or a node built with versionText/toolsValue.
  function fact(label, value, cls) {
    const f = el('div', 'ota-fact');
    f.appendChild(el('span', 'k', label));
    const v = f.appendChild(el('span', 'v ' + (cls || '')));
    if (value instanceof Node) v.appendChild(value); else v.textContent = value || DASH;
    return f;
  }

  function fill(id, ...children) { $(id).replaceChildren(...children); }

  function renderOs(data) {
    const os = data.os, nb = data.next_boot;
    const warning = nextBoot(nb, os);
    const nextEl = $('ota-next');
    nextEl.textContent = warning;
    nextEl.className = 'ota-next' + (warning ? ' warn' : '');
    nextEl.hidden = !warning;
    const rows = imageRows(os).map(r => ({
      cls: r.differs ? 'diff' : '',
      cells: [el('th', '', r.label), r.cells.booted, r.cells.staged, r.cells.rollback],
    }));
    const queued = os && os.rollback_queued;
    fill('ota-images', table(['', 'Running', 'Staged', 'Rollback' + (queued ? ' (queued)' : '')], rows));
    const note = osDataNote(data);
    const asOf = $('ota-os-asof');
    asOf.textContent = note;
    asOf.hidden = !note;
    const stamp = stampFact(os, data.build_check);
    const record = recordFact(os, data.build_check);
    const kernel = os && os.kernel;
    fill('ota-os-facts', fact('Kernel', kernel ? versionText(kernel) : ''), fact('Running since', when(os && os.booted_at)),
      fact('Build stamp', stamp.text, stamp.cls), fact('Update tools', toolsValue(os && os.tools)),
      ...(record ? [fact('Build record', record.text, record.cls)] : []));
  }

  function renderEsync(e, generatedAt) {
    e = e || {};
    const procs = processFact(e);
    fill('ota-esync', fact('Processes', procs.text, procs.cls),
      ...(Array.isArray(e.agents) ? e.agents : []).map(a => {
        const f = agentFact(a);
        return fact((agentName(a.topic) || a.topic) + ' agent', f.text, f.cls);
      }));
    if (e.client_log !== 'ok') {
      fill('ota-campaigns', el('p', 'ota-none', 'No eSync client log was found, so its campaigns cannot be shown.'));
      return;
    }
    const rows = campaignRows(e.campaigns, generatedAt);
    if (!rows.length) { fill('ota-campaigns', el('p', 'ota-none', "No campaign in the client's current log.")); return; }
    fill('ota-campaigns', table(['Package', 'Target version', 'Campaign', 'Last seen'], rows.map(r => {
      const name = el('td');
      name.appendChild(el('span', 'ota-dot ' + (r.current ? 'ok' : 'dim')));
      name.appendChild(document.createTextNode(r.package));
      return { cls: r.current ? '' : 'old', cells: [name, r.version, r.campaign, r.seen] };
    })));
  }

  function renderComponents(list) {
    if (!list || !list.length) { fill('ota-components', el('p', 'ota-none', 'No components reported.')); return; }
    fill('ota-components', table(['Component', 'Version', 'Transaction', 'Installed'],
      list.map(c => ({ cells: [c.id, c.version || 'not reported', short(c.transaction_digest), when(c.installed_at)] }))));
  }

  function renderServices(list) {
    if (!list || !list.length) { fill('ota-services', el('p', 'ota-none', 'No services reported.')); return; }
    fill('ota-services', table(['Service', 'State', 'Since', 'Restarts'], list.map(s => {
      const name = el('td');
      name.appendChild(el('span', 'ota-dot ' + serviceClass(s.active_state)));
      name.appendChild(document.createTextNode(s.name));
      const state = s.active_state + (s.sub_state ? ' (' + s.sub_state + ')' : '');
      return { cells: [name, state, when(s.since), s.restarts === null || s.restarts === undefined ? '' : String(s.restarts)] };
    })));
  }

  function renderHealth(h) {
    h = h || {};
    const ntp = h.ntp_synced === true ? 'synchronized' : h.ntp_synced === false ? 'not synchronized' : 'unknown';
    const used = typeof h.var_used_pct === 'number' ? h.var_used_pct.toFixed(1) + ' % used' : '';
    const free = typeof h.var_free_bytes === 'number' ? (h.var_free_bytes / 1e9).toFixed(1) + ' GB free' : '';
    fill('ota-health',
      fact('SELinux', h.selinux, selinuxClass(h.selinux)),
      fact('Clock (NTP)', ntp, ntpClass(h.ntp_synced)),
      fact('/var', [used, free].filter(Boolean).join(' · '), diskClass(h.var_used_pct)));
  }

  function render(data) {
    const b = banner(data);
    const bar = $('ota-banner');
    bar.hidden = !b;
    bar.className = 'card ota-banner' + (b ? ' ' + b.kind : '');
    bar.textContent = b ? b.text : '';
    const show = !!data && (data.status === 'ok' || data.status === 'stale');
    $('ota-main').hidden = !show;
    $('ota-age').textContent = show && typeof data.age_s === 'number' ? 'written ' + ago(data.age_s) + ' ago' : '';
    if (!show) return;
    renderOs(data);
    renderEsync(data.esync, data.generated_at);
    renderComponents(data.components);
    renderServices(data.services);
    renderHealth(data.health);
  }

  watch(render);
})();
