"""The /ota/demo page (docs/OTA.md "Demo view"): the /api/ota answer laid out for an audience.

The same rules as /ota (tests/test_ota_api.py): read-only, no board data in the markup, text only. And one of its
own: every value on the page comes from the answer, so a field the answer does not carry is left out, never
filled in. The page logic is pure and run in node; the render runs against the same fake DOM as /ota's.
"""
import copy
import json
import os
import time

from test_ota_api import FAKE_DOM, OTA_JS, STATIC, TEMPLATES, api, fresh, needs_node, node, read  # noqa: F401  (api: fixture)

import ota  # noqa: E402  (web/ota.py)

DEMO_JS = os.path.join(STATIC, "ota_demo.js")


# ── the route ──

def test_the_demo_page_renders_with_no_file_at_all(api):
    r = api[0].get("/ota/demo")
    assert r.status_code == 200 and r.mimetype == "text/html"
    html = r.get_data(as_text=True)
    assert 'id="demo-root"' in html and 'id="demo-banner"' in html and 'id="demo-badge"' in html
    assert "OS OTA Status" in html and "APP OTA Status" in html and ">eSync<" in html
    assert "ota-foot" not in html and "never in it" not in html        # no footnote on a screen for an audience


def test_the_demo_page_carries_no_board_data_in_its_markup(api):
    client, write = api
    d = fresh()
    d["os"]["booted"]["version"] = "MARKER-<b>4.4.0</b>"
    d["os"]["distro"] = "MARKER-distro"
    write(d)
    assert "MARKER" not in client.get("/ota/demo").get_data(as_text=True)


def test_the_demo_page_is_read_only_local_and_has_the_security_headers(api):
    client, write = api
    write(fresh())
    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)("/ota/demo", json={}).status_code == 405
    assert client.get("/ota/demo", headers={"Host": "evil.example"}).status_code == 403
    r = client.get("/ota/demo", headers={"Host": "127.0.0.1:5000"})
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_the_demo_page_loads_its_scripts_in_order_and_nothing_from_outside(api):
    html = api[0].get("/ota/demo").get_data(as_text=True)
    assert html.index('src="/static/html.js"') < html.index('src="/static/ota.js"') < html.index('src="/static/ota_demo.js"')
    assert "http://" not in html and "https://" not in html


def test_the_two_pages_link_to_each_other(api):
    client, _ = api
    assert 'href="/ota"' in client.get("/ota/demo").get_data(as_text=True)
    assert 'href="/ota/demo"' in client.get("/ota").get_data(as_text=True)


def test_the_demo_page_does_not_name_the_vehicle(api):
    import reader
    assert reader.VEHICLE.TITLE not in api[0].get("/ota/demo").get_data(as_text=True)


def test_the_demo_page_works_in_demo_mode(api, monkeypatch):
    import app as webapp
    monkeypatch.setattr(webapp, "DEMO", os.path.join(os.path.dirname(STATIC), "..", "docs", "demo"))
    client, _ = api
    assert client.get("/ota/demo").status_code == 200
    body = client.get("/api/ota").get_json()
    assert body["os"]["distro"] and body["board"]["model"] and body["esync"]["os_package"] == "RHIVOS-OS-AUTOSD"


def test_ota_demo_js_writes_text_never_markup():
    src = read(DEMO_JS)
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src and "outerHTML" not in src
    assert "document.write" not in src and "eval(" not in src
    assert "textContent" in src


def test_the_three_tools_share_a_row_and_stack_on_a_small_phone():
    css = read(STATIC, "ota_demo.css")
    assert ".demo-boxes.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }" in css
    narrow = css[css.index("@media (max-width: 420px)"):].split("\n")[0]
    assert ".demo-boxes.three" in narrow and "minmax(0, 1fr)" in narrow


def test_the_cards_sit_side_by_side_and_stack_on_a_phone():
    css = read(STATIC, "ota_demo.css")
    assert "repeat(2, minmax(0, 1fr))" in css[css.index(".demo-main {"):].split("}")[0]
    narrow = css[css.index("@media (max-width: 800px)"):]
    assert ".demo-main { grid-template-columns: minmax(0, 1fr); }" in narrow.split("\n")[0]


# ── the pure half, in node ──

def demo_js(expr, *data):
    """`expr` evaluated in node with window.Ota and window.OtaDemo loaded; `data` arrives as d0, d1, ..."""
    return node("""
      globalThis.window = globalThis;
      require(process.argv[1]); require(process.argv[2]);
      const D = JSON.parse(process.argv[3]);
      const [d0, d1, d2] = D;
      const X = window.OtaDemo;
      console.log(JSON.stringify((() => """ + expr + """)()));
    """, OTA_JS, DEMO_JS, json.dumps(list(data)))


def good_view(**over):
    return ota.view(fresh(**over), now=time.time())


GEN = "2026-11-12T01:36:40Z"


def campaign(package, version, number, seconds_ago):
    t = ota.parse_time(GEN) - seconds_ago
    return {"package": package, "version": version, "campaign": number, "last_seen": ota.format_time(t)}


@needs_node
def test_the_badge_says_live_stale_or_no_data():
    out = demo_js("D.map(X.freshness)", {"status": "ok", "age_s": 3.2}, {"status": "stale", "age_s": 725},
                  {"status": "stale", "age_s": None}, {"status": "waiting"}, {"status": "invalid"}, None)
    assert out == [{"cls": "live", "text": "Live · 3 s ago"}, {"cls": "stale", "text": "Stale · last data 12 min ago"},
                   {"cls": "stale", "text": "Stale"}, {"cls": "none", "text": "No data"},
                   {"cls": "none", "text": "No data"}, {"cls": "none", "text": "No data"}]


@needs_node
def test_the_header_is_what_the_board_named_and_nothing_else():
    out = demo_js("D.map(X.subtitle)",
                  {"os": {"distro": "AutoSD 10"}, "board": {"model": "TI EVM"}}, {"os": {"distro": "AutoSD 10"}},
                  {"board": {"model": "TI EVM"}}, {"os": {}, "board": {"model": ""}}, None)
    assert out == ["AutoSD 10 · TI EVM", "AutoSD 10", "TI EVM", "", ""]


@needs_node
def test_the_os_card_has_a_box_for_the_kernel_and_each_tool():
    out = demo_js("D.map(X.versionFacts)",
                  {"kernel": "6.12.0-281.el10iv.aarch64", "tools": {"bootc": "1.17.1", "ostree": "2026.4", "podman": "6.1.0"}},
                  {"kernel": "", "tools": {"bootc": "1.17.1", "ostree": "", "podman": ""}}, None)
    assert out[0] == [{"label": "Kernel Version", "value": "6.12.0-281.el10iv.aarch64"},
                      {"label": "bootc Version", "value": "1.17.1"}, {"label": "OSTree Version", "value": "2026.4"},
                      {"label": "Podman Version", "value": "6.1.0"}]
    assert [f["value"] for f in out[1]] == ["", "1.17.1", "", ""]
    assert [f["value"] for f in out[2]] == ["", "", "", ""]


@needs_node
def test_packages_split_into_the_os_and_the_applications_with_their_newest_campaign():
    data = {"generated_at": GEN, "esync": {"os_package": "OS-PKG", "campaigns": [
                campaign("APP-B", "2.0", 7, 10), campaign("OS-PKG", "4.7.0", 3904, 3600), campaign("APP-A", "3.7.0", 3901, 30),
                campaign("APP-A", "3.6.0", 3800, 7200)]},
            "components": [{"id": "APP-A", "version": "3.4.0"}, {"id": "OS-PKG", "version": "4.7.0"}]}
    out = demo_js("X.packages(d0)", data)
    assert out["os"]["name"] == "OS-PKG" and out["os"]["record"] == "4.7.0"
    assert out["os"]["campaign"]["campaign"] == "#3904" and out["os"]["campaign"]["current"] is False
    assert [a["name"] for a in out["apps"]] == ["APP-A", "APP-B"]           # recorded first, then a first install
    assert out["apps"][0]["record"] == "3.4.0" and out["apps"][0]["campaign"]["campaign"] == "#3901"   # the newest
    assert out["apps"][0]["campaign"]["current"] is True
    assert out["apps"][1]["record"] == "" and out["apps"][1]["campaign"]["version"] == "2.0"


@needs_node
def test_with_no_os_package_named_every_package_is_an_application():
    out = demo_js("X.packages(d0)", {"components": [{"id": "A", "version": "1"}, {"id": "B", "version": "2"}],
                                     "esync": {"os_package": ""}})
    assert out["os"] is None and [a["name"] for a in out["apps"]] == ["A", "B"]
    assert demo_js("X.packages(d0)", None) == {"os": None, "apps": []}


@needs_node
def test_a_campaign_is_active_only_while_the_client_logs_it():
    rows = [{"campaign": "#3901", "version": "3.7.0", "current": True, "seen": "30 s ago"},
            {"campaign": "#3901", "version": "3.7.0", "current": False, "seen": "26 h ago"},
            {"campaign": "#5", "version": "", "current": False, "seen": ""}]
    out = demo_js("[...D.map(c => X.campaignStatus({campaign: c}, true)), X.campaignStatus({campaign: null}, true),"
                  " X.campaignStatus(null, true), X.campaignStatus({campaign: D[0]}, false)]", *rows)
    assert out[0] == {"text": "Campaign #3901 → 3.7.0 active", "cls": "active", "note": ""}
    assert out[1] == {"text": "No active campaign", "cls": "idle", "note": "Last campaign #3901 → 3.7.0, seen 26 h ago"}
    assert out[2] == {"text": "No active campaign", "cls": "idle", "note": "Last campaign #5 → ?"}
    assert out[3] == out[4] == {"text": "No active campaign", "cls": "idle", "note": ""}
    assert out[5] is None                                   # the client's log was not read: say nothing


@needs_node
def test_campaigns_are_known_only_when_the_exporter_read_the_clients_log():
    out = demo_js("D.map(X.campaignsKnown)", {"esync": {"client_log": "ok"}}, {"esync": {"client_log": "unavailable"}},
                  {"esync": {}}, None)
    assert out == [True, False, False, False]


@needs_node
def test_a_campaigns_outcome_is_the_version_it_deployed_the_rollback_version_and_esyncs_result():
    def camp(**over):
        c = {"campaign": "#3901", "version": "3.7.0", "current": True, "seen": "5 s ago", "rollback": "3.4.0",
             "result": "ROLLBACK-COMPLETED"}
        c.update(over)
        return {"campaign": c}
    out = demo_js("D.map(p => X.outcome(p, true))", camp(), camp(result="UPDATE-COMPLETED"),
                  camp(result="NOT-COMPLETED", rollback=""), camp(result="SOME-NEW-STATE"),
                  camp(current=False, seen="27 h ago"), camp(result=""), {"campaign": None}, None)
    assert out[0] == {"deployed": "3.7.0", "rollback": "3.4.0", "result": {"text": "Rolled back", "cls": "warn", "note": ""}}
    assert out[1]["result"] == {"text": "Updated", "cls": "ok", "note": ""}
    assert out[2] == {"deployed": "3.7.0", "rollback": "", "result": {"text": "In progress", "cls": "active", "note": ""}}
    assert out[3]["result"] == {"text": "SOME-NEW-STATE", "cls": "dim", "note": ""}        # a word we do not know, as is
    assert out[4]["result"]["note"] == "seen 27 h ago"                         # no longer logged: says how old
    assert out[5] is None and out[6] is None and out[7] is None                # no status line: no boxes
    assert demo_js("X.outcome(d0, false)", camp()) is None                     # the client's log was not read


@needs_node
def test_the_board_after_act_1_and_a_failed_os_update_shows_both_outcomes():
    """The J7 as it was left on 2026-11-12: app 3.7.0 rolled back to 3.4.0, and OS 4.7.0 (registered no rollback
    version) still not completed while the board runs 4.6.0."""
    d = good_view()
    d["next_boot"] = {"source": "booted", "version": "4.4.0", "digest": ""}
    d["esync"]["campaigns"] = [
        {"package": "RHIVOS-APP-CANDUMP", "version": "3.7.0", "campaign": 3901, "last_seen": d["generated_at"],
         "rollback_version": "3.4.0", "result": "ROLLBACK-COMPLETED"},
        {"package": "RHIVOS-OS-AUTOSD", "version": "4.7.0", "campaign": 3904, "last_seen": d["generated_at"],
         "rollback_version": "", "result": "NOT-COMPLETED"}]
    out = page(d)
    osr, appr = by_label(out["osRows"]), by_label(out["appRows"])
    assert (osr["Deployed version"]["v"], osr["Rollback version"]["v"], osr["Rollback version"]["cls"]) == (
        "4.7.0", "none registered", "dim")
    assert osr["Result"]["v"] == "In progress"
    assert (appr["Deployed version"]["v"], appr["Rollback version"]["v"], appr["Result"]["v"]) == (
        "3.7.0", "3.4.0", "Rolled back")


@needs_node
def test_what_the_next_boot_starts_outranks_a_campaign_on_the_os_card():
    active = {"campaign": {"campaign": "#9", "version": "4.7.0", "current": True, "seen": "5 s ago"}}
    log = {"client_log": "ok"}
    out = demo_js("[X.osStatus(d0, d2), X.osStatus(d1, d2), X.osStatus({next_boot: {source: 'booted', version: '4.6.0'}, esync: d0.esync}, d2),"
                  " X.osStatus({next_boot: {source: 'staged', version: '', digest: 'sha256:' + 'ab'.repeat(32)}}, null),"
                  " X.osStatus({next_boot: {source: 'booted', version: '4.6.0'}, esync: d0.esync}, null),"
                  " X.osStatus({next_boot: {source: 'booted', version: '4.6.0'}}, d2),"
                  " X.osStatus({next_boot: {source: 'rollback', version: '4.5.0'}}, d2)]",
                  {"next_boot": {"source": "rollback", "version": "4.5.0"}, "esync": log},
                  {"next_boot": {"source": "staged", "version": "4.7.0"}, "esync": log}, active)
    assert out[0] == {"text": "Rollback queued: a reboot starts 4.5.0", "cls": "warn", "note": "Campaign #9 → 4.7.0 active"}
    assert out[1] == {"text": "Update staged: a reboot starts 4.7.0", "cls": "active", "note": "Campaign #9 → 4.7.0 active"}
    assert out[2] == {"text": "Campaign #9 → 4.7.0 active", "cls": "active", "note": ""}
    assert out[3]["text"] == "Update staged: a reboot starts " + "ab" * 6       # no version: named by its digest
    assert out[4] is None                                   # no OS package named: no campaign to speak of
    assert out[5] is None                                   # no client log read: likewise
    assert out[6] == {"text": "Rollback queued: a reboot starts 4.5.0", "cls": "warn", "note": ""}   # bootc alone


@needs_node
def test_esyncs_record_is_compared_with_the_running_image_and_left_out_when_either_is_missing():
    os_ = {"booted": {"version": "4.6.0"}}
    out = demo_js("[X.recordStatus(d0, {record: '4.6.0'}), X.recordStatus(d0, {record: '4.7.0'}),"
                  " X.recordStatus(d0, {record: ''}), X.recordStatus(d0, null), X.recordStatus({booted: null}, {record: '4.7.0'})]", os_)
    assert out == [{"text": "4.6.0", "cls": "ok"}, {"text": "4.7.0 (the board runs 4.6.0)", "cls": "warn"}, None, None, None]


@needs_node
def test_the_build_check_names_the_worst_finding():
    os_ = {"booted": {"version": "4.4.0"}, "build": {"version": "4.3.0"}}
    checks = [{"stamp": "mismatch", "record": "match"}, {"stamp": "missing", "record": "off"},
              {"stamp": "match", "record": "mismatch", "differs": [{"name": "podman", "have": "6.1.0", "want": "6.0.2"}]},
              {"stamp": "match", "record": "match"}, {"stamp": "match", "record": "off"},
              {"stamp": "match", "record": "none"}, {"stamp": "unknown", "record": "unknown"}, None]
    out = demo_js("D.map(c => X.buildStatus(d0, c))".replace("d0", json.dumps(os_)), *checks)
    assert out[0] == {"text": "stamped 4.3.0, but bootc runs 4.4.0", "cls": "bad"}
    assert out[1] == {"text": "no build stamp on the image", "cls": "bad"}
    assert out[2] == {"text": "not as built for 4.4.0: podman 6.1.0 (built with 6.0.2)", "cls": "bad"}
    assert out[3] == {"text": "as built for 4.4.0", "cls": "ok"}
    assert out[4] == out[5] == {"text": "stamped 4.4.0", "cls": "ok"}
    assert out[6] is None and out[7] is None


@needs_node
def test_the_applications_services_are_every_service_but_esyncs_own():
    data = {"esync": {"unit": "esync"}, "services": [
        {"name": "esync", "active_state": "active", "sub_state": "running"},
        {"name": "candump", "active_state": "failed", "sub_state": "failed"},
        {"name": "cangen", "active_state": "active", "sub_state": "running"},
        {"name": "other", "active_state": "inactive", "sub_state": "dead"},
        {"name": "gone", "active_state": "inactive", "sub_state": "not-found"},
        {"name": "odd", "active_state": "unknown", "sub_state": ""}]}
    out = demo_js("X.appServices(d0)", data)
    assert out == [{"name": "candump", "text": "failed", "cls": "bad"}, {"name": "cangen", "text": "running", "cls": "ok"},
                   {"name": "other", "text": "stopped", "cls": "warn"}, {"name": "gone", "text": "not found", "cls": "warn"},
                   {"name": "odd", "text": "unknown", "cls": "dim"}]
    data["esync"]["unit"] = "esync-sdk"
    assert [s["name"] for s in demo_js("X.appServices(d0)", data)][0] == "esync"


@needs_node
def test_the_esync_line_lists_the_agents_else_the_processes():
    out = demo_js("D.map(X.esyncFacts)",
                  {"esync": {"agents": [{"topic": "/SDK/RHIVOS_APP", "running": 1}, {"topic": "/SDK/RHIVOS_OS", "running": 0}]}},
                  {"esync": {"agents": [], "processes": 7, "outside_unit": 0, "unit": "esync"}}, None)
    assert out[0] == [{"name": "RHIVOS_APP agent", "text": "running", "cls": "ok"},
                      {"name": "RHIVOS_OS agent", "text": "not running", "cls": "bad"}]
    assert out[1] == [{"name": "eSync processes", "text": "7, all in esync.service", "cls": "ok"}]
    assert out[2] == [{"name": "eSync processes", "text": "unknown", "cls": "dim"}]


@needs_node
def test_the_slots_beside_the_running_image_are_staged_and_rollback():
    out = demo_js("D.map(X.slots)", {"staged": {"version": "4.7.0"}, "rollback": {"version": "4.5.0"}, "rollback_queued": True},
                  {"staged": None, "rollback": None}, None)
    assert out[0] == [{"label": "Staged", "value": "4.7.0"}, {"label": "Rollback (queued)", "value": "4.5.0"}]
    assert out[1] == out[2] == [{"label": "Staged", "value": ""}, {"label": "Rollback", "value": ""}]


# ── the page, rendered against the fake DOM ──

DEMO_DOM = FAKE_DOM.replace("require(process.argv[1]);", "require(process.argv[1]); require(process.argv[3]);")
assert DEMO_DOM != FAKE_DOM


def page(answer):
    return node(DEMO_DOM + """
      settle().then(() => {
        const g = id => document.getElementById(id);
        const flat = n => n.kids ? n.own + n.kids.map(flat).join('') : n.textContent;
        const boxes = n => (/\\bdemo-box\\b/.test(n.className || '') ? [n] : []).concat(...(n.kids || []).map(boxes));
        const rows = id => boxes(g(id)).map(b => ({
          k: flat(b.kids[0]), v: flat(b.kids[1]), cls: b.kids[1].className.replace(/^v\\s*/, '').trim(),
          note: b.kids[2] ? flat(b.kids[2]) : '', wide: / wide/.test(b.className) }));
        console.log(JSON.stringify({
          badge: flat(g('demo-badge')), badgeClass: g('demo-badge').className, sub: flat(g('demo-sub')),
          bannerHidden: g('demo-banner').hidden, banner: flat(g('demo-banner')),
          mainHidden: g('demo-main').hidden, mainClass: g('demo-main').className,
          os: flat(g('demo-os')), app: flat(g('demo-app')), esync: flat(g('demo-esync')),
          osRows: rows('demo-os'), appRows: rows('demo-app'), esyncRows: rows('demo-esync')
        }));
      });
    """, OTA_JS, json.dumps(answer), DEMO_JS)


def by_label(rows):
    return {r["k"]: r for r in rows}


@needs_node
def test_the_demo_page_shows_the_os_and_the_application_for_a_good_answer():
    out = page(good_view())
    assert out["badge"].startswith("Live") and out["badgeClass"] == "demo-badge live"
    assert out["sub"] == "Automotive Stream Distribution 10 · Texas Instruments J784S4 EVM"
    assert out["mainHidden"] is False and out["mainClass"] == "demo-main" and out["bannerHidden"] is True
    assert "RHIVOS-OS-AUTOSD" in out["os"] and "4.4.0" in out["os"] and "Rollback (queued)4.5.0" in out["os"]
    osr = by_label(out["osRows"])
    assert osr["Update"]["v"] == "Rollback queued: a reboot starts 4.5.0" and osr["Update"]["cls"] == "warn"
    assert osr["Build check"] == {"k": "Build check", "v": "as built for 4.4.0", "cls": "ok", "note": "", "wide": True}
    assert osr["Update"]["wide"] is True and osr["Update"]["note"] == "Campaign #1003 → 4.5.0 active"
    assert [(r["k"], r["v"]) for r in out["osRows"]][:4] == [                    # under Staged and Rollback, each in its box
        ("Kernel Version", "6.12.0-251.el10iv.aarch64"), ("bootc Version", "1.16.6"), ("OSTree Version", "2026.1"),
        ("Podman Version", "6.0.2")]
    assert [r["k"] for r in out["osRows"]][4:] == ["Update", "Deployed version", "Rollback version", "Result", "Build check"]
    assert [(osr[k]["v"], osr[k]["cls"]) for k in ("Deployed version", "Rollback version", "Result")] == [
        ("4.5.0", ""), ("4.4.0", ""), ("In progress", "active")]
    assert osr["Kernel Version"]["wide"] is True and not osr["bootc Version"]["wide"]   # a kernel version must not wrap
    assert out["os"].index("Rollback (queued)") < out["os"].index("Kernel Version")
    assert "eSync record" not in osr                       # the fixture's OS record has no version: nothing to compare
    assert "RHIVOS-APP-CANDUMP" in out["app"] and "3.4.0" in out["app"] and "RHIVOS-OS-AUTOSD" not in out["app"]
    services = {r["k"]: (r["v"], r["cls"]) for r in out["appRows"] if r["k"] in ("candump", "cangen", "esync")}
    assert services == {"candump": ("running", "ok"), "cangen": ("running", "ok")}
    assert [r["k"] for r in out["appRows"]] == ["Update", "Deployed version", "Rollback version", "Result",
                                                "candump", "cangen"]                                # each in its own box
    appr = by_label(out["appRows"])
    assert [(appr[k]["v"], appr[k]["cls"]) for k in ("Deployed version", "Rollback version", "Result")] == [
        ("3.7.0", ""), ("3.4.0", ""), ("Rolled back", "warn")]
    assert [(r["k"], r["v"], r["cls"]) for r in out["esyncRows"]] == [                 # each agent in its own box
        ("RHIVOS_APP agent", "running", "ok"), ("RHIVOS_OS agent", "running", "ok")]


@needs_node
def test_the_demo_page_shows_a_stale_record_and_a_broken_app_service_as_they_are():
    """The two stories the demo tells on the J7: eSync's record lagging an OS rollback, and a broken candump
    update that cangen, from the same transaction, survives."""
    d = good_view()
    d["components"][1]["version"] = "4.7.0"                     # eSync's OS record after the platform rolled back
    for s in d["services"]:
        if s["name"] == "candump":
            s.update(active_state="failed", sub_state="failed")
    out = page(d)
    osr = by_label(out["osRows"])
    assert osr["eSync record"]["v"] == "4.7.0 (the board runs 4.4.0)" and osr["eSync record"]["cls"] == "warn"
    assert osr["eSync record"]["wide"] is False and osr["Build check"]["wide"] is False   # the two share a row
    services = {r["k"]: (r["v"], r["cls"]) for r in out["appRows"] if r["k"] in ("candump", "cangen")}
    assert services == {"candump": ("failed", "bad"), "cangen": ("running", "ok")}


@needs_node
def test_an_active_campaign_is_shown_on_its_packages_card():
    d = good_view()
    d["next_boot"] = {"source": "booted", "version": "4.4.0", "digest": ""}
    d["esync"]["campaigns"] = [{"package": "RHIVOS-APP-CANDUMP", "version": "3.7.0", "campaign": 3901,
                                "last_seen": d["generated_at"]}]
    out = page(d)
    assert by_label(out["appRows"])["Update"]["v"] == "Campaign #3901 → 3.7.0 active"
    assert by_label(out["appRows"])["Update"]["cls"] == "active"
    assert by_label(out["osRows"])["Update"]["v"] == "No active campaign"


@needs_node
def test_a_stale_answer_is_still_shown_dimmed_with_its_age_on_the_badge():
    d = ota.view(fresh(), now=time.time() + 3600)
    assert d["status"] == "stale"
    out = page(d)
    assert out["badgeClass"] == "demo-badge stale" and out["badge"].startswith("Stale · last data")
    assert out["mainHidden"] is False and out["mainClass"] == "demo-main stale"


@needs_node
def test_no_answer_shows_the_reason_and_no_cards():
    out = page({"status": "waiting", "age_s": None, "message": "No status file yet."})
    assert out["mainHidden"] is True and out["bannerHidden"] is False and out["banner"] == "No status file yet."
    assert out["badge"] == "No data" and out["badgeClass"] == "demo-badge none"


@needs_node
def test_a_field_the_answer_does_not_carry_is_left_out_not_filled_in():
    d = ota.view({"schema": 1, "generated_at": ota.format_time(time.time()),
                  "os": {"booted": {"version": "4.6.0"}}}, now=time.time())
    out = page(d)
    assert out["sub"] == ""
    osr = by_label(out["osRows"])
    assert set(osr) == {"Kernel Version", "bootc Version", "OSTree Version", "Podman Version", "Build check"}
    assert all(osr[k]["v"] == "—" for k in ("Kernel Version", "bootc Version", "OSTree Version", "Podman Version"))
    assert osr["Build check"]["v"] == "no build stamp on the image"        # a running version with no stamp is a finding
    assert "No application package in eSync's record." in out["app"]
    assert [(r["k"], r["v"]) for r in out["esyncRows"]] == [("eSync processes", "unknown")]


@needs_node
def test_without_the_clients_log_no_card_claims_there_is_no_campaign():
    d = good_view()
    d["esync"]["client_log"] = "unavailable"
    d["esync"]["campaigns"] = []
    d["next_boot"] = {"source": "booted", "version": "4.4.0", "digest": ""}
    out = page(d)
    assert "Update" not in by_label(out["osRows"]) and "Update" not in by_label(out["appRows"])
    assert "Result" not in by_label(out["osRows"]) and "Result" not in by_label(out["appRows"])
    assert "No active campaign" not in out["os"] + out["app"]


@needs_node
def test_the_demo_page_writes_hostile_strings_as_text():
    hostile = '<img src=x onerror="alert(1)">'
    d = good_view()
    d["os"]["distro"] = hostile
    d["board"]["model"] = hostile
    d["os"]["booted"]["version"] = hostile
    d["components"][0]["id"] = hostile
    d["services"][1]["name"] = hostile
    out = page(copy.deepcopy(d))                                # the fake DOM throws if innerHTML is ever assigned
    assert hostile in out["sub"] and hostile in out["os"] and hostile in out["app"]
