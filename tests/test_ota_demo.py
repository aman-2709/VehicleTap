"""The /ota/demo page (docs/OTA.md "Demo view"): the /api/ota answer laid out for an audience.

The same rules as /ota (tests/test_ota_api.py): read-only, no board data in the markup, text only. And one of its
own: every value on the page comes from the answer, so a field the answer does not carry is left out, never
filled in. The page logic is pure and run in node; the render runs against the same fake DOM as /ota's.
"""
import copy
import json
import os
import re
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


def test_the_os_packages_card_is_hidden_until_the_board_reports_its_packages(api):
    html = api[0].get("/ota/demo").get_data(as_text=True)
    card = html[html.index('id="demo-pkgs-card"'):].split(">")[0]
    assert "hidden" in card and "demo-wide" in html[html.rindex("<section", 0, html.index('id="demo-pkgs-card"')):]
    assert html.index("APP OTA Status") < html.index("OS Packages") < html.index(">eSync<")


def test_the_os_packages_card_starts_folded_with_its_title_and_counts_on_the_line_that_opens_it(api):
    html = api[0].get("/ota/demo").get_data(as_text=True)
    fold = html[html.index('<details class="demo-fold" id="demo-pkgs-fold"'):html.index("</details>")]
    assert fold.split(">")[0] == '<details class="demo-fold" id="demo-pkgs-fold"'          # no `open`: folded
    head = fold[fold.index("<summary>"):fold.index("</summary>")]
    for part in ("OS Packages", 'id="demo-pkgs-count"', 'id="demo-pkgs-note"', "Show packages", "Hide packages"):
        assert part in head, part
    assert 'id="demo-pkgs"' in fold[fold.index("</summary>"):]                    # the boxes are inside the fold


def test_only_the_presenter_opens_or_closes_the_fold():
    """The page redraws every 5 s; it must never touch the fold, or it would close it under the presenter."""
    src = read(DEMO_JS)
    assert "demo-pkgs-fold" not in src and ".open" not in src and "'open'" not in src


def test_the_shown_packages_take_four_columns_two_on_a_tablet_and_one_on_a_phone():
    css = read(STATIC, "ota_demo.css")
    assert ".demo-boxes.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }" in css
    assert "@media (max-width: 800px) { .demo-boxes.four { grid-template-columns: repeat(2, minmax(0, 1fr)); } }" in css
    narrow = css[css.index("@media (max-width: 420px)"):].split("\n")[0]
    assert ".demo-boxes.four" in narrow
    assert ".demo-card[hidden] { display: none; }" in css
    assert ".demo-fold:not([open]) .when-open, .demo-fold[open] .when-closed { display: none; }" in css
    assert ".demo-fold-hint[hidden] { display: none; }" in css


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
    assert out[0] == [{"label": "Kernel Version", "value": "6.12.0-281", "title": "6.12.0-281.el10iv.aarch64"},
                      {"label": "bootc Version", "value": "1.17.1", "title": ""},
                      {"label": "OSTree Version", "value": "2026.4", "title": ""},
                      {"label": "Podman Version", "value": "6.1.0", "title": ""}]
    assert [f["value"] for f in out[1]] == ["", "1.17.1", "", ""] and out[1][0]["title"] == ""
    assert [f["value"] for f in out[2]] == ["", "", "", ""]


# What the J7's images carry (tests/fixtures/ota/rpm_qa_*.txt, `uname -r`), and the forms around them.
SHORT_VERSIONS = [
    ("6.12.0-281.el10iv.aarch64", "6.12.0-281"),               # uname -r: the tag and the architecture go
    ("6.12.0-281.el10iv.x86_64", "6.12.0-281"),
    ("6.12.0-281.el10iv.aarch64+debug", "6.12.0-281+debug"),   # a kernel variant stays
    ("6.12.0-281.el10iv", "6.12.0-281"),                       # the kernel-automotive package
    ("3.5.9-1.el10", "3.5.9-1"),
    ("20260811-1.git4dcd294.el10", "20260811-1.git4dcd294"),   # crypto-policies: a git snapshot stays
    ("2026.2.92_v9.0.319-100.3.el10", "2026.2.92_v9.0.319-100.3"),
    ("10.44-1.el10.3", "10.44-1.3"),                           # pcre2 in 4.3.0: the tag inside the release
    ("10.44-1.4.el10", "10.44-1.4"),                           # and in 4.6.0
    ("3.el10_0.2", "3.2"), ("1.2-3.el10_1", "1.2-3"),          # a z-stream tag
    ("6.12.0-251.el10iv, 6.12.0-281.el10iv", "6.12.0-251, 6.12.0-281"),   # two installed: each
    ("1.17.1", "1.17.1"), ("9.9p1-34", "9.9p1-34"),            # nothing to take out
    ("5.6-el10", "5.6-el10"), ("1.0-2.elx", "1.0-2.elx"), ("2.el10-1", "2.el10-1"),   # not a tag where it sits
    ("", ""),
]


@needs_node
def test_a_version_on_the_demo_view_leaves_out_the_build_tag_and_the_architecture():
    out = demo_js("D[0].map(X.shortVersion)", [v for v, _ in SHORT_VERSIONS])
    assert out == [short for _, short in SHORT_VERSIONS]
    assert demo_js("[null, undefined, 3, {}].map(X.shortVersion)") == ["", "", "", ""]


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


def attempt_row(**over):
    """A package as X.packages builds it, with a campaign the client logged 5 s ago and eSync gave up on."""
    c = {"campaign": "#3901", "version": "3.7.0", "current": True, "seen": "5 s ago", "rollback": "3.4.0",
         "result": "ROLLBACK-COMPLETED"}
    c.update(over)
    return {"campaign": c}


def attempt(pkg, running="3.4.0", known=True):
    return demo_js("X.attempt(d0, %s, %s)" % (json.dumps(running), json.dumps(known)), pkg)


@needs_node
def test_the_latest_attempt_takes_its_campaign_and_versions_from_the_clients_log_and_the_running_version_from_the_card():
    out = attempt(attempt_row())
    assert out == {"title": "Latest OTA attempt · Campaign #3901", "attempted": "3.7.0", "rollback": "3.4.0",
                   "running": "3.4.0", "active": False,
                   "note": {"text": "Update to 3.7.0 did not pass its checks. The device rolled back to 3.4.0.", "cls": "warn"}}
    other = attempt(attempt_row(campaign="#77", version="9.9", rollback="8.8"), running="8.8")
    assert (other["title"], other["attempted"], other["rollback"], other["running"]) == (
        "Latest OTA attempt · Campaign #77", "9.9", "8.8", "8.8")                 # nothing is fixed in the page


@needs_node
def test_a_campaign_the_client_stopped_logging_says_when_it_was_last_seen_in_its_title():
    out = attempt(attempt_row(current=False, seen="27 h ago"))
    assert out["title"] == "Latest OTA attempt · Campaign #3901 · seen 27 h ago"
    assert out["note"]["cls"] == "warn"                          # a finished campaign keeps its result


@needs_node
def test_the_result_note_follows_the_result_eSync_logged():
    done = attempt(attempt_row(result="UPDATE-COMPLETED"), running="3.7.0")
    assert done["note"] == {"text": "Updated to 3.7.0.", "cls": "ok"}
    working = attempt(attempt_row(result="NOT-COMPLETED"), running="3.4.0")
    assert working["note"] == {"text": "Update to 3.7.0 is in progress.", "cls": "active"}
    gone = attempt(attempt_row(result="NOT-COMPLETED", current=False, seen="27 h ago"))
    assert gone["note"] == {"text": "No recent activity for campaign #3901.", "cls": "dim"}   # not "active" any more


@needs_node
def test_a_result_word_we_do_not_know_is_shown_as_eSync_wrote_it():
    out = attempt(attempt_row(result="SOME-NEW-STATE"))
    assert out["note"] == {"text": "eSync reports SOME-NEW-STATE.", "cls": "dim"}


@needs_node
def test_a_campaign_without_a_status_line_has_its_versions_and_no_verdict():
    out = attempt(attempt_row(result=""))
    assert out["note"] is None and out["attempted"] == "3.7.0" and out["rollback"] == "3.4.0"


@needs_node
def test_a_campaign_with_no_rollback_version_registered_still_has_a_block():
    done = attempt(attempt_row(rollback="", result="UPDATE-COMPLETED"), running="3.7.0")
    assert done["rollback"] == "" and done["note"] == {"text": "Updated to 3.7.0.", "cls": "ok"}
    failed = attempt(attempt_row(rollback="", result="ROLLBACK-COMPLETED"), running="3.4.0")
    assert failed["rollback"] == ""
    assert failed["note"] == {"text": "Update to 3.7.0 did not pass its checks. The device runs 3.4.0.", "cls": "warn"}
    assert "rolled back" not in failed["note"]["text"]           # no rollback was registered: none is claimed
    unknown = attempt(attempt_row(rollback="", result="ROLLBACK-COMPLETED"), running="")
    assert unknown["note"]["text"] == "Update to 3.7.0 did not pass its checks."


@needs_node
def test_when_the_device_runs_something_other_than_the_rollback_version_the_note_says_what_it_runs():
    """OS campaign 3958 on the J7: registered rollback 4.3.0, attempted 4.7.1, the device came back on 4.6.0."""
    out = attempt(attempt_row(campaign="#3958", version="4.7.1", rollback="4.3.0"), running="4.6.0")
    assert out["rollback"] == "4.3.0" and out["running"] == "4.6.0"
    assert out["note"] == {"text": "Update to 4.7.1 did not pass its checks. The device runs 4.6.0.", "cls": "warn"}
    nothing_read = attempt(attempt_row(), running="")            # the running version is unknown: only the target
    assert nothing_read["running"] == "" and nothing_read["note"]["text"].endswith("The device rolled back to 3.4.0.")


@needs_node
def test_a_campaign_with_no_version_or_number_is_still_worded_without_blanks():
    out = attempt(attempt_row(campaign="", version="", result="NOT-COMPLETED"))
    assert out["title"] == "Latest OTA attempt" and out["attempted"] == ""
    assert out["note"]["text"] == "The update is in progress."
    out = attempt(attempt_row(campaign="", version="", result="NOT-COMPLETED", current=False, seen=""))
    assert out["note"]["text"] == "No recent activity." and out["title"] == "Latest OTA attempt"


@needs_node
def test_there_is_no_block_without_the_clients_log_or_without_a_campaign():
    assert attempt(attempt_row(), known=False) is None           # the log was not read: say nothing
    assert attempt({"campaign": None}) is None
    assert demo_js("[X.attempt(null, '', true), X.attempt(undefined, '', true)]") == [None, None]


@needs_node
def test_campaigns_are_known_only_when_the_exporter_read_the_clients_log():
    out = demo_js("D.map(X.campaignsKnown)", {"esync": {"client_log": "ok"}}, {"esync": {"client_log": "unavailable"}},
                  {"esync": {}}, None)
    assert out == [True, False, False, False]


@needs_node
def test_the_board_after_act_1_and_a_failed_os_update_shows_both_attempts():
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
    assert out["osTitles"] == ["Latest OTA attempt · Campaign #3904"]
    assert [(osr[k]["v"], osr[k]["cls"]) for k in ("Attempted", "Rolls back to", "Running now")] == [
        ("4.7.0", ""), ("none registered", "dim"), ("4.4.0", "")]
    assert out["osNotes"] == [{"text": "Update to 4.7.0 is in progress.", "cls": "active"}]
    assert out["appTitles"] == ["Latest OTA attempt · Campaign #3901"]
    assert [(appr[k]["v"], appr[k]["cls"]) for k in ("Attempted", "Rolls back to", "Running now")] == [
        ("3.7.0", ""), ("3.4.0", ""), ("3.4.0", "")]
    assert out["appNotes"] == [{"text": "Update to 3.7.0 did not pass its checks. The device rolled back to 3.4.0.",
                                "cls": "warn"}]
    for r in (osr, appr):                                         # the boxes the block replaces
        assert not {"Update", "Deployed version", "Rollback version", "Result"} & set(r)


@needs_node
def test_a_finished_campaign_the_client_keeps_confirming_is_not_called_active():
    """Campaign #3958 → 4.7.1 was rolled back, yet the client logs it every 30 s until it is stopped on the portal."""
    d = good_view()
    d["os"]["booted"]["version"] = "4.6.0"
    d["next_boot"] = {"source": "booted", "version": "4.6.0", "digest": ""}
    d["esync"]["campaigns"] = [{"package": "RHIVOS-OS-AUTOSD", "version": "4.7.1", "campaign": 3958,
                                "last_seen": d["generated_at"], "rollback_version": "4.3.0",
                                "result": "ROLLBACK-COMPLETED"}]
    out = page(d)
    assert "active" not in out["os"].lower()
    assert out["osNotes"] == [{"text": "Update to 4.7.1 did not pass its checks. The device runs 4.6.0.", "cls": "warn"}]


@needs_node
def test_a_campaign_that_completed_with_no_rollback_registered_reads_updated():
    d = good_view()
    d["esync"]["campaigns"] = [{"package": "RHIVOS-APP-CANDUMP", "version": "3.7.0", "campaign": 3956,
                                "last_seen": d["generated_at"], "rollback_version": "", "result": "UPDATE-COMPLETED"}]
    d["components"][0]["version"] = "3.7.0"
    out = page(d)
    appr = by_label(out["appRows"])
    assert (appr["Rolls back to"]["v"], appr["Rolls back to"]["cls"]) == ("none registered", "dim")
    assert out["appNotes"] == [{"text": "Updated to 3.7.0.", "cls": "ok"}]


@needs_node
def test_a_campaign_with_no_status_line_yet_shows_its_boxes_and_no_note():
    d = good_view()
    d["esync"]["campaigns"] = [{"package": "RHIVOS-APP-CANDUMP", "version": "3.7.0", "campaign": 3901,
                                "last_seen": d["generated_at"], "rollback_version": "", "result": ""}]
    out = page(d)
    assert out["appTitles"] == ["Latest OTA attempt · Campaign #3901"] and out["appNotes"] == []
    assert by_label(out["appRows"])["Attempted"]["v"] == "3.7.0"


@needs_node
def test_with_no_os_package_named_the_os_card_is_bootc_alone():
    d = good_view()
    d["esync"]["os_package"] = ""
    out = page(d)
    assert out["osTitles"] == [] and out["osNotes"] == []
    assert not {"Attempted", "Rolls back to", "Running now"} & set(by_label(out["osRows"]))


@needs_node
def test_the_os_card_runs_what_bootc_booted_and_an_app_card_what_esyncs_record_says():
    d = good_view()
    d["os"]["booted"]["version"] = "4.6.0"
    d["components"][0]["version"] = "3.1.0"
    out = page(d)
    assert by_label(out["osRows"])["Running now"]["v"] == "4.6.0"
    assert by_label(out["appRows"])["Running now"]["v"] == "3.1.0"


@needs_node
def test_a_package_that_never_had_a_campaign_gets_no_block():
    d = good_view()
    d["esync"]["campaigns"] = [c for c in d["esync"]["campaigns"] if c["package"] != "RHIVOS-APP-CANDUMP"]
    out = page(d)
    assert out["appTitles"] == [] and "Attempted" not in by_label(out["appRows"])
    assert out["osTitles"] == ["Latest OTA attempt · Campaign #1003"]


@needs_node
def test_what_the_next_boot_starts_is_said_from_bootc_alone_on_the_os_card():
    out = demo_js("D.map(X.osStatus)",
                  {"next_boot": {"source": "rollback", "version": "4.5.0"}},
                  {"next_boot": {"source": "staged", "version": "4.7.0"}},
                  {"next_boot": {"source": "staged", "version": "", "digest": "sha256:" + "ab" * 32}})
    assert out[0] == {"text": "Rollback queued: a reboot starts 4.5.0", "cls": "warn"}
    assert out[1] == {"text": "Update staged: a reboot starts 4.7.0", "cls": "active"}
    assert out[2]["text"] == "Update staged: a reboot starts " + "ab" * 6       # no version: named by its digest
    none = demo_js("[X.osStatus({next_boot: {source: 'booted', version: '4.6.0'}}), X.osStatus({}), X.osStatus(null)]")
    assert none == [None, None, None]


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


def act2_packages(**over):
    """The J7 after Act 2 (4.3.0 to 4.6.0), as the exporter reports it with three --show-rpm names."""
    p = {"total": 255, "compared": True, "updated": 119, "added": 1, "removed": 0,
         "shown": [{"name": "openssl", "version": "3.5.9-1.el10", "before": "3.5.7-2.el10", "change": "updated"},
                   {"name": "zstd", "version": "1.5.5-9.el10", "before": "", "change": "added"},
                   {"name": "bash", "version": "5.2.26-6.el10", "before": "5.2.26-6.el10", "change": "same"}]}
    p.update(over)
    return p


def act2_os(**packages_over):
    return {"booted": {"version": "4.6.0"}, "rollback": {"version": "4.3.0"}, "packages": act2_packages(**packages_over)}


@needs_node
def test_the_packages_summary_counts_the_changes_since_the_rollback_image():
    out = demo_js("X.packageView(d0)", act2_os())
    assert out["summary"] == {"text": "119 updated · 1 added · 0 removed", "cls": "active",
                              "note": "since 4.3.0 (the rollback image) · 255 packages in 4.6.0"}
    assert [r["name"] for r in out["rows"]] == ["openssl", "zstd", "bash"]


@needs_node
def test_after_the_reset_back_to_4_3_0_the_summary_counts_the_downgrades():
    """The reset campaign: 4.3.0 runs and 4.6.0 is the rollback image."""
    os_ = {"booted": {"version": "4.3.0"}, "rollback": {"version": "4.6.0"},
           "packages": act2_packages(total=254, updated=0, downgraded=119, added=0, removed=1)}
    out = demo_js("X.packageView(d0)", os_)
    assert out["summary"] == {"text": "0 updated · 119 downgraded · 0 added · 1 removed", "cls": "active",
                              "note": "since 4.6.0 (the rollback image) · 254 packages in 4.3.0"}


@needs_node
def test_no_downgrade_leaves_the_summary_as_it_was():
    out = demo_js("D.map(X.packageView)", act2_os(downgraded=0), act2_os(downgraded=None))
    assert [o["summary"]["text"] for o in out] == ["119 updated · 1 added · 0 removed"] * 2
    out = demo_js("X.packageView(d0)", act2_os(updated=0, downgraded=0, added=0, removed=0))
    assert out["summary"]["text"] == "No package changed"


@needs_node
def test_an_update_that_changed_no_package_says_so():
    """Act 3's 4.7.1 is 4.6.0 with one boot check more: the same packages."""
    out = demo_js("X.packageView(d0)", act2_os(updated=0, added=0, removed=0))
    assert out["summary"]["text"] == "No package changed" and out["summary"]["cls"] == "dim"


@needs_node
def test_without_a_comparison_the_summary_is_the_count_and_why():
    with_rollback = act2_os(compared=False, updated=None, added=None, removed=None)
    without = dict(with_rollback, rollback=None)
    out = demo_js("D.map(X.packageView)", with_rollback, without)
    assert out[0]["summary"] == {"text": "255 packages in 4.6.0", "cls": "",
                                 "note": "The rollback image's packages could not be read"}
    assert out[1]["summary"]["note"] == "No rollback image to compare with"


@needs_node
def test_a_count_the_answer_does_not_carry_is_left_out_of_the_summary():
    out = demo_js("X.packageView(d0)", act2_os(added=None, total=None))
    assert out["summary"]["text"] == "119 updated · 0 removed"
    assert out["summary"]["note"] == "since 4.3.0 (the rollback image)"


@needs_node
def test_no_packages_in_the_answer_is_no_view():
    assert demo_js("D.map(X.packageView)", {"booted": {"version": "4.6.0"}, "packages": None}, {}, None) == [None] * 3


@needs_node
def test_each_shown_package_shows_its_version_and_what_it_was():
    rows = [{"name": "a", "version": "2", "before": "1", "change": "updated"},
            {"name": "b", "version": "1", "before": "", "change": "added"},
            {"name": "c", "version": "", "before": "3", "change": "removed"},
            {"name": "d", "version": "4", "before": "4", "change": "same"},
            {"name": "e", "version": "5", "before": "", "change": ""},
            {"name": "f", "version": "", "before": "", "change": ""}]
    out = demo_js("D[0].map(r => X.shownPackage(r, '4.3.0'))", rows)
    assert out == [{"name": "a", "value": "2", "accent": True, "cls": "", "note": "was 1", "title": "2, was 1"},
                   {"name": "b", "value": "1", "accent": True, "cls": "", "note": "new since 4.3.0", "title": "1"},
                   {"name": "c", "value": "removed", "accent": False, "cls": "warn", "note": "was 3", "title": "was 3"},
                   {"name": "d", "value": "4", "accent": False, "cls": "dim", "note": "unchanged", "title": "4"},
                   {"name": "e", "value": "5", "accent": False, "cls": "", "note": "", "title": "5"},
                   {"name": "f", "value": "not installed", "accent": False, "cls": "dim", "note": "", "title": ""}]
    assert demo_js("X.shownPackage(d0, '')", rows[1])["note"] == "new"


@needs_node
def test_a_shown_package_leaves_out_the_build_tag_and_keeps_the_rpm_strings_for_the_hover():
    rows = [{"name": "kernel-automotive", "version": "6.12.0-281.el10iv", "before": "6.12.0-251.el10iv", "change": "updated"},
            {"name": "can-utils", "version": "2025.01-3.el10iv", "before": "2023.03-3.el10", "change": "updated"},
            {"name": "zstd", "version": "1.5.5-9.el10", "before": "", "change": "added"},
            {"name": "gone", "version": "", "before": "2.1-4.el10", "change": "removed"},
            {"name": "bash", "version": "5.2.26-6.el10", "before": "5.2.26-6.el10", "change": "same"},
            {"name": "other", "version": "1.0-1.el10", "before": "", "change": ""}]
    out = demo_js("D[0].map(r => X.shownPackage(r, '4.3.0'))", rows)
    assert [(r["value"], r["note"], r["title"]) for r in out] == [
        ("6.12.0-281", "was 6.12.0-251", "6.12.0-281.el10iv, was 6.12.0-251.el10iv"),
        ("2025.01-3", "was 2023.03-3", "2025.01-3.el10iv, was 2023.03-3.el10"),
        ("1.5.5-9", "new since 4.3.0", "1.5.5-9.el10"),
        ("removed", "was 2.1-4", "was 2.1-4.el10"),
        ("5.2.26-6", "unchanged", "5.2.26-6.el10"),
        ("1.0-1", "", "1.0-1.el10")]


@needs_node
def test_a_downgraded_package_says_so_with_the_newer_version_it_came_from():
    rows = [{"name": "kernel-automotive", "version": "6.12.0-251.el10iv", "before": "6.12.0-281.el10iv",
             "change": "downgraded"},
            {"name": "x", "version": "1.0-1.el10", "before": "1.0-1.el10iv", "change": "downgraded"}]   # the tag alone
    out = demo_js("D[0].map(r => X.shownPackage(r, '4.6.0'))", rows)
    assert out == [{"name": "kernel-automotive", "value": "6.12.0-251", "accent": True, "cls": "",
                    "note": "downgraded from 6.12.0-281", "title": "6.12.0-251.el10iv, was 6.12.0-281.el10iv"},
                   {"name": "x", "value": "1.0-1.el10", "accent": True, "cls": "",
                    "note": "downgraded from 1.0-1.el10iv", "title": "1.0-1.el10, was 1.0-1.el10iv"}]


@needs_node
def test_an_update_that_changed_only_the_build_tag_keeps_the_tags_in_the_box():
    """The same version rebuilt under another tag: shortened, the box would say "updated" between two equal
    versions."""
    rows = [{"name": "can-utils", "version": "2025.01-3.el10iv", "before": "2025.01-3.el10", "change": "updated"},
            {"name": "tzdata", "version": "2026a-1.el10_1", "before": "2026a-1.el10_0", "change": "updated"}]
    out = demo_js("D[0].map(r => X.shownPackage(r, '4.3.0'))", rows)
    assert [(r["value"], r["note"], r["accent"]) for r in out] == [
        ("2025.01-3.el10iv", "was 2025.01-3.el10", True), ("2026a-1.el10_1", "was 2026a-1.el10_0", True)]


# ── the page, rendered against the fake DOM ──

DEMO_DOM = FAKE_DOM.replace("require(process.argv[1]);", "require(process.argv[1]); require(process.argv[3]);")
assert DEMO_DOM != FAKE_DOM


# What the page holds after a render, for the tests to read.
DUMP = """
  const g = id => document.getElementById(id);
  const flat = n => n.kids ? n.own + n.kids.map(flat).join('') : n.textContent;
  const withClass = (re, n) => (re.test(n.className || '') ? [n] : []).concat(...(n.kids || []).map(k => withClass(re, k)));
  const boxes = n => withClass(/\\bdemo-box\\b/, n);
  const rows = id => boxes(g(id)).map(b => ({
    k: flat(b.kids[0]), v: flat(b.kids[1]), cls: b.kids[1].className.replace(/^v\\s*/, '').trim(),
    note: b.kids[2] ? flat(b.kids[2]) : '', wide: / wide/.test(b.className), title: b.title || '' }));
  const changedBoxes = id => boxes(g(id)).filter(b => /\\bchanged\\b/.test(b.className)).map(b => flat(b.kids[0]));
  const heads = id => withClass(/\\bdemo-head\\b/, g(id)).map(n => ({ text: flat(n), changed: /\\bchanged\\b/.test(n.className) }));
  const titles = id => withClass(/\\bdemo-attempt-title\\b/, g(id)).map(flat);
  const titleActive = id => withClass(/\\bdemo-attempt-title\\b/, g(id)).map(n => /\\bactive\\b/.test(n.className));
  const notes = id => withClass(/\\bdemo-attempt-note\\b/, g(id)).map(n => ({
    text: flat(n), cls: n.className.replace(/^demo-attempt-note\\s*/, '').trim() }));
  const dump = () => ({
    osTitles: titles('demo-os'), appTitles: titles('demo-app'), osNotes: notes('demo-os'), appNotes: notes('demo-app'),
    osTitleActive: titleActive('demo-os'), appTitleActive: titleActive('demo-app'),
    osChanged: changedBoxes('demo-os'), appChanged: changedBoxes('demo-app'), osHeads: heads('demo-os'), appHeads: heads('demo-app'),
    badge: flat(g('demo-badge')), badgeClass: g('demo-badge').className, sub: flat(g('demo-sub')),
    bannerHidden: g('demo-banner').hidden, banner: flat(g('demo-banner')),
    mainHidden: g('demo-main').hidden, mainClass: g('demo-main').className,
    os: flat(g('demo-os')), app: flat(g('demo-app')), esync: flat(g('demo-esync')),
    osRows: rows('demo-os'), appRows: rows('demo-app'), esyncRows: rows('demo-esync'),
    pkgsHidden: g('demo-pkgs-card').hidden, pkgs: flat(g('demo-pkgs')), pkgRows: rows('demo-pkgs'),
    pkgsCount: flat(g('demo-pkgs-count')), pkgsCountClass: g('demo-pkgs-count').className,
    pkgsNote: flat(g('demo-pkgs-note')), pkgsHintHidden: g('demo-pkgs-hint').hidden
  });
"""


def page(answer):
    return node(DEMO_DOM + DUMP + """
      settle().then(() => console.log(JSON.stringify(dump())));
    """, OTA_JS, json.dumps(answer), DEMO_JS)


# The same page asked again, as it is every few seconds: `answers` are what /api/ota says at each poll, and the
# result is the page's dump after each one. The fake DOM's timer keeps the poll function (the demo's: it registers
# last) so the test can call it, and `__step` says which answer the fake fetch returns.
SEQ_DOM = (DEMO_DOM
           .replace("globalThis.setInterval = () => 0;", "globalThis.setInterval = f => { globalThis.__poll = f; return 0; };")
           .replace("const answer = JSON.parse(process.argv[2]);",
                    "const SEQ = JSON.parse(process.argv[2]); const answer = SEQ[0];")
           .replace("json: async () => answer", "json: async () => SEQ[globalThis.__step || 0]"))
assert SEQ_DOM.count("__poll") == 1 and "__step" in SEQ_DOM and "const SEQ" in SEQ_DOM


def page_seq(*answers):
    return node(SEQ_DOM + DUMP + """
      settle().then(async () => {
        const steps = [dump()];
        for (let i = 1; i < SEQ.length; i++) { globalThis.__step = i; await globalThis.__poll(); await settle(); steps.push(dump()); }
        console.log(JSON.stringify(steps));
      });
    """, OTA_JS, json.dumps(list(answers)), DEMO_JS)


def later(d, seconds=5, **over):
    """The same answer, written `seconds` later by the exporter (a new status file), with `over` changed."""
    out = copy.deepcopy(d)
    out["generated_at"] = ota.format_time(ota.parse_time(d["generated_at"]) + seconds)
    out.update(over)
    return out


def by_label(rows):
    return {r["k"]: r for r in rows}


@needs_node
def test_the_demo_page_shows_the_os_and_the_application_for_a_good_answer():
    out = page(good_view())
    assert out["badge"].startswith("Live") and out["badgeClass"] == "demo-badge live beat-a"   # the first status beats
    assert out["sub"] == "Automotive Stream Distribution 10 · Texas Instruments J784S4 EVM"
    assert out["mainHidden"] is False and out["mainClass"] == "demo-main" and out["bannerHidden"] is True
    assert "RHIVOS-OS-AUTOSD" in out["os"] and "4.4.0" in out["os"]
    # No Staged and Rollback boxes: the Update box says what the next boot starts when that matters.
    assert "Staged" not in out["os"] and "Rollback (queued)" not in out["os"]
    osr = by_label(out["osRows"])
    assert osr["Update"]["v"] == "Rollback queued: a reboot starts 4.5.0" and osr["Update"]["cls"] == "warn"
    assert osr["Build check"] == {"k": "Build check", "v": "as built for 4.4.0", "cls": "ok", "note": "", "wide": True,
                                  "title": ""}
    assert osr["Update"]["wide"] is True and osr["Update"]["note"] == ""         # bootc's word alone: no campaign text
    assert [(r["k"], r["v"]) for r in out["osRows"]][:4] == [                    # right under the running version
        ("Kernel Version", "6.12.0-251"), ("bootc Version", "1.16.6"), ("OSTree Version", "2026.1"),
        ("Podman Version", "6.0.2")]
    assert osr["Kernel Version"]["title"] == "6.12.0-251.el10iv.aarch64"           # the full release on hover
    assert osr["bootc Version"]["title"] == ""
    assert [r["k"] for r in out["osRows"]][4:] == ["Update", "Attempted", "Rolls back to", "Running now", "Build check"]
    assert out["osTitles"] == ["Latest OTA attempt · Campaign #1003"]
    assert [(osr[k]["v"], osr[k]["cls"]) for k in ("Attempted", "Rolls back to", "Running now")] == [
        ("4.5.0", ""), ("4.4.0", ""), ("4.4.0", "")]
    assert out["osNotes"] == [{"text": "Update to 4.5.0 is in progress.", "cls": "active"}]
    assert osr["Kernel Version"]["wide"] is True and not osr["bootc Version"]["wide"]   # a kernel version must not wrap
    assert out["os"].index("Running") < out["os"].index("Kernel Version")
    assert "eSync record" not in osr                       # the fixture's OS record has no version: nothing to compare
    assert "RHIVOS-APP-CANDUMP" in out["app"] and "3.4.0" in out["app"] and "RHIVOS-OS-AUTOSD" not in out["app"]
    services = {r["k"]: (r["v"], r["cls"]) for r in out["appRows"] if r["k"] in ("candump", "cangen", "esync")}
    assert services == {"candump": ("running", "ok"), "cangen": ("running", "ok")}
    assert [r["k"] for r in out["appRows"]] == ["Attempted", "Rolls back to", "Running now", "candump", "cangen"]                                # each in its own box
    appr = by_label(out["appRows"])
    assert [(appr[k]["v"], appr[k]["cls"]) for k in ("Attempted", "Rolls back to", "Running now")] == [
        ("3.7.0", ""), ("3.4.0", ""), ("3.4.0", "")]
    assert out["appNotes"] == [{"text": "Update to 3.7.0 did not pass its checks. The device rolled back to 3.4.0.",
                                "cls": "warn"}]
    assert [(r["k"], r["v"], r["cls"]) for r in out["esyncRows"]] == [                 # each agent in its own box
        ("RHIVOS_APP agent", "running", "ok"), ("RHIVOS_OS agent", "running", "ok")]


@needs_node
def test_a_staged_update_is_said_in_the_update_box_with_no_slot_boxes():
    d = good_view()
    d["os"]["rollback_queued"] = False
    d["next_boot"] = {"source": "staged", "version": "4.7.0", "digest": ""}
    out = page(d)
    osr = by_label(out["osRows"])
    assert osr["Update"]["v"] == "Update staged: a reboot starts 4.7.0" and osr["Update"]["cls"] == "active"
    assert "Staged" not in out["os"] and not any(r["k"].startswith(("Staged", "Rollback (")) for r in out["osRows"])


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
def test_a_campaign_in_progress_is_the_only_one_shown_as_active():
    d = good_view()
    d["next_boot"] = {"source": "booted", "version": "4.4.0", "digest": ""}
    d["esync"]["campaigns"] = [{"package": "RHIVOS-APP-CANDUMP", "version": "3.7.0", "campaign": 3901,
                                "last_seen": d["generated_at"], "result": "NOT-COMPLETED"}]
    out = page(d)
    assert out["appNotes"] == [{"text": "Update to 3.7.0 is in progress.", "cls": "active"}]
    assert out["osNotes"] == [] and "Update" not in by_label(out["osRows"])      # no campaign for the OS: no block


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
    assert out["osTitles"] == out["appTitles"] == [] and out["osNotes"] == out["appNotes"] == []
    assert "Attempted" not in by_label(out["osRows"]) and "Attempted" not in by_label(out["appRows"])
    assert "No active campaign" not in out["os"] + out["app"]


@needs_node
def test_the_os_packages_card_shows_the_act_2_update():
    d = good_view()
    d["os"]["booted"]["version"], d["os"]["rollback"]["version"] = "4.6.0", "4.3.0"
    d["os"]["packages"] = act2_packages()
    out = page(d)
    assert out["pkgsHidden"] is False and out["pkgsHintHidden"] is False
    assert (out["pkgsCount"], out["pkgsCountClass"]) == ("119 updated · 1 added · 0 removed", "demo-fold-count active")
    assert out["pkgsNote"] == "since 4.3.0 (the rollback image) · 255 packages in 4.6.0"
    assert [(r["k"], r["v"], r["cls"], r["note"]) for r in out["pkgRows"]] == [          # inside the fold
        ("openssl", "3.5.9-1", "", "was 3.5.7-2"), ("zstd", "1.5.5-9", "", "new since 4.3.0"),
        ("bash", "5.2.26-6", "dim", "unchanged")]
    assert [r["title"] for r in out["pkgRows"]] == [                                  # as rpm writes them, on hover
        "3.5.9-1.el10, was 3.5.7-2.el10", "1.5.5-9.el10", "5.2.26-6.el10"]


@needs_node
def test_with_no_package_to_show_the_card_is_the_counts_alone_with_nothing_to_open():
    d = good_view()
    d["os"]["packages"] = act2_packages(shown=[])
    out = page(d)
    assert out["pkgsHidden"] is False and out["pkgsCount"] == "119 updated · 1 added · 0 removed"
    assert out["pkgsHintHidden"] is True and out["pkgRows"] == []


@needs_node
def test_the_os_packages_card_stays_hidden_when_the_board_did_not_read_its_packages():
    out = page(good_view())
    assert out["pkgsHidden"] is True and out["pkgs"] == "" and out["pkgRows"] == []


@needs_node
def test_the_demo_page_writes_hostile_strings_as_text():
    hostile = '<img src=x onerror="alert(1)">'
    d = good_view()
    d["os"]["distro"] = hostile
    d["board"]["model"] = hostile
    d["os"]["booted"]["version"] = hostile
    d["components"][0]["id"] = hostile
    d["services"][1]["name"] = hostile
    d["os"]["packages"] = act2_packages()
    d["os"]["packages"]["shown"][0]["name"] = hostile           # the reader would drop it; the page must not care
    d["os"]["packages"]["shown"][0]["before"] = hostile
    out = page(copy.deepcopy(d))                                # the fake DOM throws if innerHTML is ever assigned
    assert hostile in out["sub"] and hostile in out["os"] and hostile in out["app"] and hostile in out["pkgs"]


# ── something is happening: the heartbeat, the pulse and the flash ──

@needs_node
def test_a_campaign_is_active_only_while_it_is_not_completed_and_the_client_still_logs_it():
    out = demo_js("D.map(p => X.attempt(p, '', true).active)",
                  attempt_row(result="NOT-COMPLETED"),
                  attempt_row(result="NOT-COMPLETED", current=False, seen="27 h ago"),
                  attempt_row(result="UPDATE-COMPLETED"), attempt_row(result="ROLLBACK-COMPLETED"),
                  attempt_row(result="SOME-NEW-STATE"), attempt_row(result=""))
    assert out == [True, False, False, False, False, False]


@needs_node
def test_the_heartbeat_beats_once_for_each_status_the_board_writes():
    w1, w2 = GEN, "2026-11-12T01:36:45Z"
    out = demo_js("[X.heartbeat('', d0), X.heartbeat(d0.generated_at, d0), X.heartbeat(d0.generated_at, d1),"
                  " X.heartbeat('', d2), X.heartbeat('', null), X.heartbeat('', {status: 'ok'})]",
                  {"status": "ok", "generated_at": w1}, {"status": "ok", "generated_at": w2},
                  {"status": "stale", "generated_at": w1})
    assert out == [True, False, True, False, False, False]      # new write: beat; same file again, stale, none: no beat


@needs_node
def test_the_live_badge_beats_for_each_new_status_and_stops_when_the_board_goes_quiet():
    a = good_view()
    b = later(a)
    quiet = later(b, 0, status="stale", age_s=70)               # the board stopped writing: the file stays b's
    back = later(b, 90)
    steps = page_seq(a, b, copy.deepcopy(b), quiet, back)
    classes = [st["badgeClass"] for st in steps]
    assert classes[0] in ("demo-badge live beat-a", "demo-badge live beat-b")
    assert classes[1].startswith("demo-badge live beat-") and classes[1] != classes[0]    # a new class restarts the beat
    assert classes[2] == classes[1]                              # the same status read again: no second beat
    assert classes[3] == "demo-badge stale"                      # quiet: no beat, no live dot
    assert classes[4].startswith("demo-badge live beat-")        # and it beats again when the board writes again


@needs_node
def test_nothing_beats_without_an_answer():
    steps = page_seq({"status": "waiting", "message": "no file yet"})
    assert steps[0]["badgeClass"] == "demo-badge none"


@needs_node
def test_only_a_campaign_in_progress_pulses_and_a_finished_one_the_client_still_confirms_does_not():
    out = page(good_view())             # OS campaign 1003 NOT-COMPLETED and logged now; app campaign 1001 ROLLBACK-COMPLETED
    assert out["osTitleActive"] == [True] and out["appTitleActive"] == [False]
    assert out["osNotes"][0]["cls"] == "active" and out["appNotes"][0]["cls"] == "warn"
    old = good_view()
    old["esync"]["campaigns"][0]["last_seen"] = ota.format_time(ota.parse_time(old["generated_at"]) - 3600)
    assert page(old)["osTitleActive"] == [False]                 # not logged for an hour: no longer working on it


@needs_node
def test_a_staged_update_pulses_in_its_update_box():
    d = good_view()
    d["next_boot"] = {"source": "staged", "version": "4.7.0", "digest": ""}
    assert by_label(page(d)["osRows"])["Update"]["cls"] == "active"      # the pulse is on every box classed active


@needs_node
def test_a_fact_that_changes_between_two_answers_flashes_and_nothing_flashes_on_load_or_when_nothing_changed():
    a = good_view()
    same = later(a)
    done = later(a, 10)
    done["esync"]["campaigns"][0]["result"] = "ROLLBACK-COMPLETED"
    moved = later(done, 10)
    moved["os"]["booted"]["version"] = "4.5.0"
    steps = page_seq(a, same, done, moved)
    for st in steps[:2]:                                         # the first render and an unchanged one
        assert st["osChanged"] == st["appChanged"] == [] and not any(h["changed"] for h in st["osHeads"] + st["appHeads"])
        assert all("changed" not in n["cls"] for n in st["osNotes"] + st["appNotes"])
    assert "changed" in steps[2]["osNotes"][0]["cls"]            # eSync's result moved: the line flashes
    assert steps[2]["osChanged"] == [] and steps[2]["appChanged"] == []    # the versions did not
    assert steps[3]["osChanged"] == ["Running now"]              # bootc booted another image
    assert [h["changed"] for h in steps[3]["osHeads"]] == [True] and not any(h["changed"] for h in steps[3]["appHeads"])
    assert steps[3]["appChanged"] == []                          # the application card did not change


@needs_node
def test_a_result_line_that_appears_flashes():
    a = good_view()
    a["esync"]["campaigns"][1]["result"] = ""                    # the app's campaign: no status line yet
    b = later(a)
    b["esync"]["campaigns"][1]["result"] = "UPDATE-COMPLETED"
    steps = page_seq(a, b)
    assert steps[0]["appNotes"] == [] and "changed" in steps[1]["appNotes"][0]["cls"]


@needs_node
def test_a_card_that_first_appears_after_no_data_does_not_flash():
    steps = page_seq({"status": "waiting", "message": "no file yet"}, good_view())
    assert steps[1]["osChanged"] == steps[1]["appChanged"] == [] and not any(h["changed"] for h in steps[1]["osHeads"])


def test_the_motion_is_css_only_stops_for_reduced_motion_and_loops_in_step_with_the_poll():
    css = read(STATIC, "ota_demo.css")
    for name in ("demo-beat-a", "demo-beat-b", "demo-pulse", "demo-breathe", "demo-flash"):
        assert "@keyframes " + name in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "animation: none" in css[css.index("@media (prefers-reduced-motion: reduce)"):]
    # The page redraws a card at every poll, which restarts a CSS animation: a loop whose period divides the poll
    # interval starts over exactly where it ends, so the redraw is not seen.
    poll_s = int(re.search(r"POLL_MS = (\d+)", read(STATIC, "ota.js")).group(1)) / 1000
    for name in ("demo-pulse", "demo-breathe"):
        period = float(re.search(r"animation: " + name + r" ([0-9.]+)s", css).group(1))
        assert abs(poll_s / period - round(poll_s / period)) < 1e-9, name
