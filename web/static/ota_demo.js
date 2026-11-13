// The /ota/demo page: the same /api/ota answer as /ota, laid out for an audience (docs/OTA.md "Demo view").
//
// Read-only and text-only like ota.js, whose helpers it uses (window.Ota; ota.js loads first). Every value on
// the page comes from the answer: a field the answer does not carry is left out, never filled in. The pure
// helpers are exported on window.OtaDemo for the node tests (tests/test_ota_demo.py).
(function () {
  'use strict';

  const O = window.Ota;
  const DASH = '—';

  // ── pure helpers ──

  // The badge: whether what the page shows is live, and if not, how old it is.
  function freshness(data) {
    const ago = data && typeof data.age_s === 'number' ? O.ago(data.age_s) + ' ago' : '';
    switch (data && data.status) {
      case 'ok': return { cls: 'live', text: ago ? 'Live · ' + ago : 'Live' };
      case 'stale': return { cls: 'stale', text: ago ? 'Stale · last data ' + ago : 'Stale' };
      default: return { cls: 'none', text: 'No data' };
    }
  }

  // 'Automotive Stream Distribution 10 · Texas Instruments J784S4 EVM', or as much of it as the board named.
  function subtitle(data) {
    return [data && data.os && data.os.distro, data && data.board && data.board.model].filter(Boolean).join(' · ');
  }

  // The OS card's version boxes, each in its own box: the running kernel, then the update tools.
  function versionFacts(os) {
    const tools = (os && os.tools) || {};
    return [{ label: 'Kernel Version', value: (os && os.kernel) || '' },
            { label: 'bootc Version', value: tools.bootc || '' },
            { label: 'OSTree Version', value: tools.ostree || '' },
            { label: 'Podman Version', value: tools.podman || '' }];
  }

  // The OS's package and the applications': each with eSync's recorded version and its most recent campaign.
  // A package with a campaign but no record yet (a first install) is listed too. With no OS package named,
  // every package is an application and the OS card shows bootc's view alone.
  function packages(data) {
    const osName = (data && data.esync && data.esync.os_package) || '';
    const comps = Array.isArray(data && data.components) ? data.components : [];
    const camps = O.campaignRows(data && data.esync && data.esync.campaigns, data && data.generated_at);
    const names = [];
    comps.forEach(c => { if (!names.includes(c.id)) names.push(c.id); });
    camps.forEach(c => { if (!names.includes(c.package)) names.push(c.package); });
    const pkg = name => ({ name, record: (comps.find(c => c.id === name) || {}).version || '',
                           campaign: camps.find(c => c.package === name) || null });   // the list is newest first
    return { os: osName ? pkg(osName) : null, apps: names.filter(n => n !== osName).map(pkg) };
  }

  function campaignNote(c) {
    if (!c) return '';
    const what = c.campaign + ' → ' + (c.version || '?');
    return c.current ? 'Campaign ' + what + ' active' : 'Last campaign ' + what + (c.seen ? ', seen ' + c.seen : '');
  }

  // Whether the answer can say what eSync is working on: only when the exporter read the client's log.
  const campaignsKnown = data => !!(data && data.esync && data.esync.client_log === 'ok');

  // What eSync is doing for a package: a campaign the client is working on now, or none (with the last one);
  // null when the client's log was not read, so the page says nothing rather than "no campaign".
  function campaignStatus(pkg, known) {
    if (!known) return null;
    const c = pkg && pkg.campaign;
    if (c && c.current) return { text: campaignNote(c), cls: 'active', note: '' };
    return { text: 'No active campaign', cls: 'idle', note: campaignNote(c) };
  }

  // eSync's result for a campaign, in the words the client logs after "considered already updated:".
  const RESULTS = { 'UPDATE-COMPLETED': { text: 'Updated', cls: 'ok' },
                    'ROLLBACK-COMPLETED': { text: 'Rolled back', cls: 'warn' },
                    'NOT-COMPLETED': { text: 'In progress', cls: 'active' } };

  // A package's last campaign as three boxes: the version it deployed, the version eSync rolls back to if that fails,
  // and eSync's result. null when the client's log was not read or holds no status line for the campaign. A campaign
  // the client stopped logging keeps its boxes, with when it was last seen under the result.
  function outcome(pkg, known) {
    const c = pkg && pkg.campaign;
    if (!known || !c || !c.result) return null;
    const r = RESULTS[c.result] || { text: c.result, cls: 'dim' };
    return { deployed: c.version || '', rollback: c.rollback || '',
             result: { text: r.text, cls: r.cls, note: c.current || !c.seen ? '' : 'seen ' + c.seen } };
  }

  // The OS's update status. What the next boot starts outranks a campaign: a queued rollback, then a staged update.
  // Those come from bootc and need no eSync; a campaign needs the OS package named and the client's log read.
  function osStatus(data, pkg) {
    const nb = data && data.next_boot;
    const next = nb && (nb.version || O.short(nb.digest));
    const note = campaignsKnown(data) ? campaignNote(pkg && pkg.campaign) : '';
    if (nb && nb.source === 'rollback') return { text: 'Rollback queued: a reboot starts ' + next, cls: 'warn', note };
    if (nb && nb.source === 'staged') return { text: 'Update staged: a reboot starts ' + next, cls: 'active', note };
    return pkg ? campaignStatus(pkg, campaignsKnown(data)) : null;
  }

  // eSync's record of the OS package against the image bootc says is running; null when there is nothing to compare.
  // They differ after the platform rolled an update back and the agent has not caught up; this says so and no more.
  function recordStatus(os, pkg) {
    const running = O.bootedVersion(os), rec = pkg && pkg.record;
    if (!rec || !running) return null;
    return rec === running ? { text: rec, cls: 'ok' } : { text: rec + ' (the board runs ' + running + ')', cls: 'warn' };
  }

  // Is the running image the build its version names (web/ota.py build_check)? The worst finding first.
  function buildStatus(os, check) {
    const v = O.bootedVersion(os), stamped = (os && os.build && os.build.version) || '';
    switch (check && check.stamp) {
      case 'mismatch': return { text: 'stamped ' + stamped + ', but bootc runs ' + v, cls: 'bad' };
      case 'missing': return { text: 'no build stamp on the image', cls: 'bad' };
    }
    const rec = O.recordFact(os, check);
    if (rec && rec.cls === 'bad') return rec;
    if (check && check.record === 'match') return { text: 'as built for ' + v, cls: 'ok' };
    if (check && check.stamp === 'match') return { text: 'stamped ' + v, cls: 'ok' };
    return null;
  }

  function serviceText(s) {
    if (s.active_state === 'active') return s.sub_state || 'active';
    if (s.active_state === 'inactive') return s.sub_state === 'not-found' ? 'not found' : 'stopped';
    return s.active_state || 'unknown';
  }

  // The application's services: every service the host reports except eSync's own unit.
  function appServices(data) {
    const unit = (data && data.esync && data.esync.unit) || 'esync';
    return (Array.isArray(data && data.services) ? data.services : [])
      .filter(s => s.name !== unit && s.name !== unit + '.service')
      .map(s => ({ name: s.name, text: serviceText(s), cls: O.serviceClass(s.active_state) }));
  }

  // The eSync line under the cards: each agent the host was asked about, else how many of eSync's processes run.
  function esyncFacts(data) {
    const e = (data && data.esync) || {};
    if (Array.isArray(e.agents) && e.agents.length) {
      return e.agents.map(a => Object.assign({ name: (O.agentName(a.topic) || a.topic) + ' agent' }, O.agentFact(a)));
    }
    return [Object.assign({ name: 'eSync processes' }, O.processFact(e))];
  }

  // The other two image slots beside the running one (the headline): what an update staged, what a rollback would start.
  function slots(os) {
    os = os || {};
    const name = img => (img && (img.version || O.short(img.digest))) || '';
    return [{ label: 'Staged', value: name(os.staged) },
            { label: os.rollback_queued ? 'Rollback (queued)' : 'Rollback', value: name(os.rollback) }];
  }

  window.OtaDemo = { freshness, subtitle, versionFacts, packages, campaignsKnown, campaignNote, campaignStatus, outcome,
                     osStatus, recordStatus, buildStatus,
                     serviceText, appServices, esyncFacts, slots };

  // ── the page ──

  if (typeof document === 'undefined' || !document.getElementById('demo-root')) return;

  const $ = id => document.getElementById(id);
  const el = O.el;

  function fill(id, ...children) { $(id).replaceChildren(...children); }

  // One fact in its own box: a small label over the value, the value coloured by `cls`, an optional smaller line
  // under it. A `wide` box takes the whole row of its grid.
  function box(label, value, cls, note, wide) {
    const b = el('div', 'demo-box' + (wide ? ' wide' : ''));
    b.appendChild(el('span', 'k', label));
    const v = b.appendChild(el('span', 'v ' + (cls || '')));
    if (value instanceof Node) v.appendChild(value); else v.textContent = value || DASH;
    if (note) b.appendChild(el('span', 'note', note));
    return b;
  }

  function grid(...boxes) {
    return gridOf('demo-boxes', boxes);
  }

  function gridOf(cls, boxes) {
    const g = el('div', cls);
    boxes.forEach(b => g.appendChild(b));
    return g;
  }

  // The headline version: big, in the accent colour; a dash when there is none.
  function headline(label, version, extra) {
    const h = el('div', 'demo-head');
    h.appendChild(el('span', 'k', label));
    h.appendChild(version ? el('span', 'demo-ver', version) : el('span', 'demo-ver none', DASH));
    if (extra) h.appendChild(el('span', 'demo-digest', extra));
    return h;
  }

  // The build check and eSync's record share a row; one alone takes the whole row.
  function checks(build, record) {
    const pair = [build && [ 'Build check', build ], record && [ 'eSync record', record ]].filter(Boolean);
    return pair.map(([label, f]) => box(label, f.text, f.cls, '', pair.length === 1));
  }

  function renderOs(data, pkg) {
    const os = data.os || {};
    const strip = el('div', 'demo-slots');
    slots(os).forEach(s => {
      const box = strip.appendChild(el('div', 'demo-slot' + (s.value ? ' on' : '')));
      box.appendChild(el('span', 'k', s.label));
      box.appendChild(el('span', 'v', s.value || DASH));
    });
    const status = osStatus(data, pkg);
    const build = buildStatus(os, data.build_check);
    const record = recordStatus(os, pkg);
    const note = O.osDataNote(data);
    const version = O.bootedVersion(os), digest = O.short(os.booted && os.booted.digest);
    const versionBox = (f, wide) => box(f.label, f.value ? O.versionText(f.value) : '', '', '', wide);
    const [kernel, ...tools] = versionFacts(os);
    fill('demo-os',
      ...(pkg ? [el('div', 'demo-pkg', pkg.name)] : []),
      headline('Running', version || digest, version ? digest : ''),        // an image with no version is named by its digest
      strip,
      grid(versionBox(kernel, true)),                                       // across the card: a version must not wrap
      gridOf('demo-boxes three', tools.map(f => versionBox(f))),
      grid(...(status ? [box('Update', status.text, status.cls, status.note, true)] : [])),
      ...outcomeBoxes(outcome(pkg, campaignsKnown(data))),
      grid(...checks(build, record)),
      ...(note ? [el('p', 'demo-asof', note)] : []));
  }

  function renderApps(data, apps) {
    const parts = [];
    if (!apps.length) parts.push(el('p', 'demo-none', "No application package in eSync's record."));
    const known = campaignsKnown(data);
    apps.forEach(pkg => {
      const status = campaignStatus(pkg, known);
      parts.push(el('div', 'demo-pkg', pkg.name), headline('Installed', pkg.record),
                 ...(status ? [grid(box('Update', status.text, status.cls, status.note, true))] : []),
                 ...outcomeBoxes(outcome(pkg, known)));
    });
    const services = appServices(data);
    if (services.length) parts.push(el('div', 'demo-sub', 'Services'), grid(...services.map(serviceBox)));
    fill('demo-app', ...parts);
  }

  // Something that runs or not (a service, an agent): its name over its state, with the state's dot.
  function stateBox(name, text, cls) {
    const state = el('span');
    state.appendChild(el('span', 'ota-dot ' + cls));
    state.appendChild(document.createTextNode(text));
    return box(name, state, cls);
  }

  const serviceBox = s => stateBox(s.name, s.text, s.cls);

  // The outcome boxes of a package's last campaign, or nothing.
  function outcomeBoxes(o) {
    if (!o) return [];
    return [gridOf('demo-boxes three', [
      box('Deployed version', o.deployed ? O.versionText(o.deployed) : ''),
      o.rollback ? box('Rollback version', O.versionText(o.rollback)) : box('Rollback version', 'none registered', 'dim'),
      box('Result', o.result.text, o.result.cls, o.result.note)])];
  }

  function renderEsync(data) {
    fill('demo-esync', grid(...esyncFacts(data).map(f => stateBox(f.name, f.text, f.cls))));
  }

  function render(data) {
    const badge = freshness(data);
    $('demo-badge').textContent = badge.text;
    $('demo-badge').className = 'demo-badge ' + badge.cls;
    const show = !!data && (data.status === 'ok' || data.status === 'stale');
    const b = show ? null : O.banner(data);
    $('demo-banner').hidden = !b;
    $('demo-banner').textContent = b ? b.text : '';
    $('demo-main').hidden = !show;
    $('demo-main').className = 'demo-main' + (data && data.status === 'stale' ? ' stale' : '');
    if (!show) return;
    $('demo-sub').textContent = subtitle(data);
    const p = packages(data);
    renderOs(data, p.os);
    renderApps(data, p.apps);
    renderEsync(data);
  }

  O.watch(render);
})();
