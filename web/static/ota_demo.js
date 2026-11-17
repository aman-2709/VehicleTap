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

  // Whether the board has written a status the page has not shown: the live badge beats once for each. A file read
  // again, a stale one and no answer are not a write.
  function heartbeat(lastWrite, data) {
    return !!(data && data.status === 'ok' && data.generated_at && data.generated_at !== lastWrite);
  }

  // The badge: whether what the page shows is live, and if not, how old it is.
  function freshness(data) {
    const ago = data && typeof data.age_s === 'number' ? O.ago(data.age_s) + ' ago' : '';
    switch (data && data.status) {
      case 'ok': return { cls: 'live', text: ago ? 'Live · ' + ago : 'Live' };
      case 'stale': return { cls: 'stale', text: ago ? 'Stale · last data ' + ago : 'Stale' };
      default: return { cls: 'none', text: 'No data' };
    }
  }

  // The OS's name and the board's model ('Automotive Stream Distribution 10 · <the model in the board's device
  // tree>'), or as much of it as the board named.
  function subtitle(data) {
    return [data && data.os && data.os.distro, data && data.board && data.board.model].filter(Boolean).join(' · ');
  }

  // A version as the demo view shows it: the upstream version and the build number, without the build's
  // distribution tag (.el10, .el10iv, .el10_0, wherever it sits in the release) or the architecture the kernel
  // names itself with (`uname -r`). Every package of an image carries one of a few tags, so they do not say what
  // changed: 6.12.0-281.el10iv.aarch64 shows as 6.12.0-281. Only those two are taken out; everything else stays,
  // a kernel variant too (6.12.0-281.el10iv.aarch64+debug shows as 6.12.0-281+debug). Two installed versions
  // ('a, b') are shortened each. The page keeps the full string for the box's hover, and the answer keeps it.
  const ARCH = /\.(?:aarch64|x86_64|i686|ppc64le|s390x|riscv64|noarch)(?=\+|$)/;
  const DIST = /\.el\d+(?:_\d+)*[a-z]*(?=[.+]|$)/g;
  function shortVersion(v) {
    if (typeof v !== 'string') return '';
    return v.split(', ').map(one => one.replace(ARCH, '').replace(DIST, '')).join(', ');
  }

  // The OS card's version boxes, each in its own box: the running kernel (shortened, its full release on hover),
  // then the update tools.
  function versionFacts(os) {
    const tools = (os && os.tools) || {};
    const kernel = (os && os.kernel) || '';
    return [{ label: 'Kernel Version', value: shortVersion(kernel), title: kernel },
            { label: 'bootc Version', value: tools.bootc || '', title: '' },
            { label: 'OSTree Version', value: tools.ostree || '', title: '' },
            { label: 'Podman Version', value: tools.podman || '', title: '' }];
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

  // Whether the answer can say what eSync is working on: only when the exporter read the client's log.
  const campaignsKnown = data => !!(data && data.esync && data.esync.client_log === 'ok');

  // What eSync's result for a campaign means for the device, in a sentence. eSync's words are the client's, logged
  // after "considered already updated:". `running` is the version the card knows to be running (bootc's for the OS,
  // eSync's record for an application), '' when it does not know. What the device does is claimed only when `running`
  // shows it: eSync's record can say UPDATE-COMPLETED or ROLLBACK-COMPLETED for a version the board does not run (after a
  // platform revert), and the line then says what eSync recorded and what runs. `stale`: the answer is old, so nothing
  // is said to be happening now. A word we do not know is shown as is.
  function verdict(c, running, stale) {
    const to = c.version ? 'Update to ' + c.version : 'The update';
    switch (c.result) {
      case '': return null;                                           // the client has logged no status line yet
      case 'UPDATE-COMPLETED':
        if (!c.version) return { text: 'The update completed.', cls: 'ok' };
        if (running && running !== c.version) {
          return { text: 'eSync recorded the update to ' + c.version + ', but the device runs ' + running + '.', cls: 'warn' };
        }
        return { text: 'Updated to ' + c.version + '.', cls: 'ok' };
      case 'ROLLBACK-COMPLETED': {
        const failed = to + ' did not pass its checks.';
        let tail = '';
        if (!running) tail = c.rollback ? ' eSync rolled back to ' + c.rollback + '.' : '';    // eSync's record, not the device's
        else if (running === c.rollback) tail = ' The device rolled back to ' + c.rollback + '.';
        else if (running === c.version) tail = ' The device still runs ' + running + '.';
        else tail = ' The device runs ' + running + '.';
        return { text: failed + tail, cls: 'warn' };
      }
      case 'NOT-COMPLETED':                                           // only while the client still works on it
        if (!c.current) return { text: 'No recent activity' + (c.campaign ? ' for campaign ' + c.campaign : '') + '.', cls: 'dim' };
        return stale ? { text: to + ' was in progress at the last report.', cls: 'dim' }
                     : { text: to + ' is in progress.', cls: 'active' };
      default: return { text: 'eSync reports ' + c.result + '.', cls: 'dim' };
    }
  }

  // A package's latest campaign: what was attempted, what eSync rolls back to if that fails, what runs now, and
  // what eSync made of it. Every value is the board's: the campaign and the attempted version from the client's
  // log, the rollback version as eSync registered it, `running` from the card. A campaign the client stopped
  // logging says when it was last seen. null when the client's log was not read or holds no campaign for the
  // package, so the page says nothing rather than "no campaign". `active` is whether something is happening: the
  // campaign is not completed and the client is still working on it. A finished campaign the client keeps
  // confirming is not. `stale` (the answer is old): nothing is active or said to be in progress now.
  function attempt(pkg, running, known, stale) {
    const c = pkg && pkg.campaign;
    if (!known || !c) return null;
    const title = ['Latest OTA attempt' + (c.campaign ? ' · Campaign ' + c.campaign : ''),
                   !c.current && c.seen ? 'seen ' + c.seen : ''].filter(Boolean).join(' · ');
    return { title, attempted: c.version || '', rollback: c.rollback || '', running: running || '',
             active: c.result === 'NOT-COMPLETED' && !!c.current && !stale,
             note: verdict(c, running || '', !!stale) };
  }

  // The OS's update status from bootc alone: what the next boot starts, when that is a queued rollback or a staged
  // update. Needs no eSync; null when the next boot is the running image.
  function osStatus(data) {
    const nb = data && data.next_boot;
    const next = nb && (nb.version || O.short(nb.digest));
    if (nb && nb.source === 'rollback') return { text: 'Rollback queued: a reboot starts ' + next, cls: 'warn' };
    if (nb && nb.source === 'staged') return { text: 'Update staged: a reboot starts ' + next, cls: 'active' };
    return null;
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

  // The OS image's packages (the exporter's --show-rpm), against the rollback image: the one-line summary the card
  // shows folded, then each package the board was asked to show with its version and what it was. null when the
  // board did not read its packages.
  function packageView(os) {
    const p = os && os.packages;
    if (!p) return null;
    const running = O.bootedVersion(os);
    const before = (os.rollback && (os.rollback.version || O.short(os.rollback.digest))) || '';
    const total = typeof p.total === 'number' ? p.total + ' packages' + (running ? ' in ' + running : '') : '';
    let summary;
    if (p.compared) {
      // Downgrades (the demo's reset to an older image) are named only when there are some, so the update's line
      // reads as it always did; a file from an exporter that did not count them has none.
      const counts = [['updated', p.updated], ['downgraded', p.downgraded], ['added', p.added], ['removed', p.removed]]
        .filter(([word, n]) => typeof n === 'number' && (word !== 'downgraded' || n > 0));
      const changed = counts.reduce((sum, [, n]) => sum + n, 0);
      const since = before ? 'since ' + before + ' (the rollback image)' : 'since the rollback image';
      summary = { text: changed ? counts.map(([word, n]) => n + ' ' + word).join(' · ') : 'No package changed',
                  cls: changed ? 'active' : 'dim', note: [since, total].filter(Boolean).join(' · ') };
    } else {
      summary = { text: total, cls: '',
                  note: os.rollback ? "The rollback image's packages could not be read" : 'No rollback image to compare with' };
    }
    return { summary, rows: (Array.isArray(p.shown) ? p.shown : []).map(r => shownPackage(r, before)) };
  }

  // One shown package: its version now (`accent` when it changed), under it what it was, both shortened
  // (shortVersion), and on hover the versions as rpm writes them. A change only in the tag (the same version
  // rebuilt under another one) keeps the tags: they are what changed.
  function shownPackage(r, before) {
    const moved = r.change === 'updated' || r.change === 'downgraded';
    const tagOnly = moved && shortVersion(r.version) === shortVersion(r.before);
    const v = s => (tagOnly ? s || '' : shortVersion(s));
    const was = r.before ? 'was ' + r.before : '';
    const row = (value, accent, cls, note, title) => ({ name: r.name, value, accent, cls, note, title });
    switch (r.change) {
      case 'updated': return row(v(r.version), true, '', 'was ' + v(r.before), [r.version, was].filter(Boolean).join(', '));
      case 'downgraded': return row(v(r.version), true, '', 'downgraded from ' + v(r.before),
                                    [r.version, was].filter(Boolean).join(', '));
      case 'added': return row(v(r.version), true, '', before ? 'new since ' + before : 'new', r.version || '');
      case 'removed': return row('removed', false, 'warn', 'was ' + v(r.before), was);
      case 'same': return row(v(r.version), false, 'dim', 'unchanged', r.version || '');
    }
    return r.version ? row(v(r.version), false, '', '', r.version) : row('not installed', false, 'dim', '', '');
  }

  window.OtaDemo = { heartbeat, freshness, subtitle, shortVersion, versionFacts, packages, campaignsKnown,
                     attempt, osStatus, recordStatus, buildStatus,
                     serviceText, appServices, esyncFacts, packageView, shownPackage };

  // ── the page ──

  if (typeof document === 'undefined' || !document.getElementById('demo-root')) return;

  const $ = id => document.getElementById(id);
  const el = O.el;

  // The badge's beat: the board's last write the page showed, and which of two identical animations the last beat
  // used (a new class name restarts a CSS animation; the same one does not).
  let lastWrite = '', beatFlip = false, beatClass = '';

  // What the cards said at the last render, by key, and at this one. A fact whose text differs from the last render's
  // (or is new) flashes once; nothing flashes before the page has shown a card.
  let shown = null, nowShown = {};
  function mark(node, key, text) {
    nowShown[key] = text;
    if (shown && shown[key] !== text) node.className += ' changed';
    return node;
  }

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

  // A box whose value the page shortened carries the full string as its hover text.
  function titled(b, title) {
    if (title) b.title = title;
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
    const status = osStatus(data);
    const build = buildStatus(os, data.build_check);
    const record = recordStatus(os, pkg);
    const note = O.osDataNote(data);
    const version = O.bootedVersion(os), digest = O.short(os.booted && os.booted.digest);
    const versionBox = (f, wide) => titled(box(f.label, f.value ? O.versionText(f.value) : '', '', '', wide), f.title);
    const [kernel, ...tools] = versionFacts(os);
    fill('demo-os',
      ...(pkg ? [el('div', 'demo-pkg', pkg.name)] : []),
      mark(headline('Running', version || digest, version ? digest : ''), 'os/version', version || digest),   // no version: its digest
      grid(versionBox(kernel, true)),                                       // across the card: a version must not wrap
      gridOf('demo-boxes three', tools.map(f => versionBox(f))),
      grid(...(status ? [mark(box('Update', status.text, status.cls, '', true), 'os/update', status.text)] : [])),
      ...attemptBlock(attempt(pkg, version, campaignsKnown(data), data.status === 'stale'), 'os'),
      grid(...checks(build, record)),
      ...(note ? [el('p', 'demo-asof', note)] : []));
  }

  function renderApps(data, apps) {
    const parts = [];
    if (!apps.length) parts.push(el('p', 'demo-none', "No application package in eSync's record."));
    const known = campaignsKnown(data);
    apps.forEach(pkg => {
      parts.push(el('div', 'demo-pkg', pkg.name), mark(headline('Installed', pkg.record), pkg.name + '/version', pkg.record),
                 ...attemptBlock(attempt(pkg, pkg.record, known, data.status === 'stale'), pkg.name));
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

  // A package's latest campaign (attempt): its title, the three versions side by side, and the one-line result.
  // `scope` names the card, so a fact that changes is told from the same fact of another card. While the campaign is
  // active the title and the line pulse (ota_demo.css); a fact that changed since the last render flashes.
  function attemptBlock(a, scope) {
    if (!a) return [];
    const ver = v => (v ? O.versionText(v) : '');
    const rollback = a.rollback ? ver(a.rollback) : 'none registered';
    const parts = [el('div', 'demo-sub demo-attempt-title' + (a.active ? ' active' : ''), a.title),
                   gridOf('demo-boxes three', [
                     mark(box('Attempted', ver(a.attempted)), scope + '/attempted', a.attempted),
                     mark(a.rollback ? box('Rolls back to', rollback) : box('Rolls back to', rollback, 'dim'), scope + '/rollback', a.rollback),
                     mark(box('Running now', ver(a.running)), scope + '/running', a.running)])];
    if (a.note) parts.push(mark(el('p', 'demo-attempt-note ' + a.note.cls, a.note.text), scope + '/note', a.note.cls + a.note.text));
    return parts;
  }

  function renderEsync(data) {
    fill('demo-esync', grid(...esyncFacts(data).map(f => stateBox(f.name, f.text, f.cls))));
  }

  // The OS packages card, hidden when the board did not read its packages. The summary goes on its folded line and
  // the package boxes inside the fold; whether the fold is open is the presenter's, never set here.
  function renderPackages(data) {
    const v = packageView(data.os);
    $('demo-pkgs-card').hidden = !v;
    if (!v) {
      fill('demo-pkgs');
      return;
    }
    const s = v.summary;
    $('demo-pkgs-count').textContent = s.text || DASH;
    $('demo-pkgs-count').className = 'demo-fold-count ' + s.cls;
    $('demo-pkgs-note').textContent = s.note;
    $('demo-pkgs-hint').hidden = !v.rows.length;                         // nothing to open
    fill('demo-pkgs', ...(v.rows.length ? [gridOf('demo-boxes four', v.rows.map(r =>
      titled(box(r.name, r.accent ? O.versionText(r.value) : r.value, r.cls, r.note), r.title)))] : []));
  }

  function render(data) {
    const badge = freshness(data);
    if (heartbeat(lastWrite, data)) {
      beatFlip = !beatFlip;
      beatClass = beatFlip ? 'beat-a' : 'beat-b';
      lastWrite = data.generated_at;
    }
    $('demo-badge').textContent = badge.text;
    $('demo-badge').className = 'demo-badge ' + badge.cls + (badge.cls === 'live' ? ' ' + beatClass : '');
    const show = !!data && (data.status === 'ok' || data.status === 'stale');
    const b = show ? null : O.banner(data);
    $('demo-banner').hidden = !b;
    $('demo-banner').textContent = b ? b.text : '';
    $('demo-main').hidden = !show;
    $('demo-main').className = 'demo-main' + (data && data.status === 'stale' ? ' stale' : '');
    if (!show) return;
    nowShown = {};
    $('demo-sub').textContent = subtitle(data);
    const p = packages(data);
    renderOs(data, p.os);
    renderApps(data, p.apps);
    renderPackages(data);
    renderEsync(data);
    shown = nowShown;
  }

  O.watch(render);
})();
