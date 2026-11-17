"""tools/ota_export.py: the host-side half of the OTA status file (docs/OTA.md).

It runs on the board, outside the container, as root, and it is the only thing that sees the host. So the
claims worth testing are the ones about what it reads and what it lets out: it only reads (an allowlist of the
commands it may run), it builds the document from named fields (a registry, a checksum or a key in its inputs
cannot reach the output), and what it writes is exactly what the dashboard's reader accepts.

The inputs are real captures from the J7 with the registry, digests and checksums replaced
(tests/fixtures/ota/).
"""
import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from conftest import ROOT, fixture

sys.path.insert(0, os.path.join(ROOT, "tools"))

import ota_export as ex  # noqa: E402
import ota  # noqa: E402  (web/ota.py: the reader the output must satisfy)

BOOTC = fixture("ota/bootc_status.json")
ESYNC = fixture("ota/esync_components.json")
HOST = fixture("ota/host_misc.json")
NOW = HOST["time"]
MONO = HOST["monotonic"]
BOOT = HOST["boottime"]


def vfs():
    return ex.Vfs(**HOST["statvfs"])


def sources(**over):
    s = {"bootc": BOOTC, "esync": ESYNC, "kernel": HOST["kernel"],
         "units": {n: HOST["systemctl_show"][n] for n in ("esync", "candump", "cangen")},
         "selinux": HOST["selinux_enforce"], "ntp": HOST["ntp"], "vfs": vfs()}
    s.update(over)
    return s


def build(**over):
    return ex.build(sources(**over), now=NOW, mono=MONO, boot=BOOT)


def fake_io(**over):
    """A host that has nothing, unless told: every reader the collector uses, as a namespace."""
    io = dict(run=lambda c, timeout=10: None, read_file=lambda p: None, statvfs=lambda p: vfs(),
              kernel_release=lambda: HOST["kernel"], read_state_dir=lambda p: {}, processes=lambda prefix: [],
              log_tail=lambda p: None)
    io.update(over)
    return SimpleNamespace(**io)


class FakeClock:
    """A monotonic clock that moves only when something sleeps or `advance()`s it."""

    def __init__(self, t=1000.0):
        self.t, self.naps = t, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.naps.append(s)
        self.t += s

    def advance(self, s):
        self.t += s


# ── the document: every section, from real captures ──

def test_the_document_has_the_schema_and_the_time_it_was_made():
    d = build()
    assert d["schema"] == 1 and d["generated_at"] == ex.iso(NOW) == "2026-10-28T03:58:12Z"


def test_the_os_section_names_three_images_and_the_queued_rollback():
    o = build()["os"]
    assert o["booted"] == {"version": "4.4.0", "digest": o["booted"]["digest"], "built_at": "2026-08-07T23:03:53Z"}
    assert o["rollback"]["version"] == "4.5.0" and o["rollback"]["built_at"] == "2026-08-07T23:03:54Z"
    assert o["staged"] is None and o["rollback_queued"] is True
    assert o["kernel"] == "6.12.0-251.el10iv.aarch64"
    assert o["booted_at"] == "2026-10-27T23:46:53Z"            # now minus the time since boot


def test_nanosecond_times_become_whole_seconds():
    assert ex.iso_seconds("2026-08-07T23:03:53.358191364Z") == "2026-08-07T23:03:53Z"
    assert ex.iso_seconds("2026-08-07T23:03:53Z") == "2026-08-07T23:03:53Z"
    assert ex.iso_seconds("2026-08-07T16:03:53.5-07:00") == "2026-08-07T16:03:53-07:00"
    for bad in ("", None, 5, "later", "2026-08-07", "2026-08-07T23:03:53"):
        assert ex.iso_seconds(bad) == ""


def test_a_staged_update_is_an_image_like_the_others():
    b = json.loads(json.dumps(BOOTC))
    b["status"]["staged"] = json.loads(json.dumps(b["status"]["rollback"]))
    b["status"]["staged"]["image"]["version"] = "9.9.9"
    b["status"]["rollbackQueued"] = False
    o = build(bootc=b)["os"]
    assert o["staged"]["version"] == "9.9.9" and o["rollback_queued"] is False


@pytest.mark.parametrize("queued", ["true", 1, None, "yes", [True]])
def test_a_queued_rollback_needs_a_real_true(queued):
    b = json.loads(json.dumps(BOOTC))
    b["status"]["rollbackQueued"] = queued
    assert build(bootc=b)["os"]["rollback_queued"] is False


def test_components_come_from_the_esync_state_by_name():
    c = build()["components"]
    assert [x["id"] for x in c] == ["RHIVOS-APP-CANDUMP", "RHIVOS-OS-AUTOSD"]
    assert c[0] == {"id": "RHIVOS-APP-CANDUMP", "version": "3.4.0", "installed_at": "2026-07-24T20:16:34Z",
                    "transaction_digest": ESYNC["RHIVOS-APP-CANDUMP"]["transactionDigest"]}
    assert c[1]["version"] == ""


def test_services_report_state_and_when_they_started_on_the_wall_clock():
    s = {x["name"]: x for x in build()["services"]}
    assert list(s) == ["esync", "candump", "cangen"]
    e = s["esync"]
    assert (e["active_state"], e["sub_state"], e["restarts"]) == ("active", "running", 0)
    # started 52.715896 s after the monotonic clock began; now is MONO s after it began
    assert e["since"] == ex.iso(NOW - MONO + 52.715896) == "2026-10-27T23:47:46Z"
    assert s["candump"]["since"] == "2026-10-27T23:47:32Z"


def test_a_unit_that_does_not_exist_is_inactive_and_says_so():
    s = ex.service("nosuchunit", HOST["systemctl_show"]["nosuchunit"], now=NOW, mono=MONO)
    assert (s["active_state"], s["sub_state"], s["since"]) == ("inactive", "not-found", "")


def test_a_unit_that_never_started_has_no_start_time():
    text = "LoadState=loaded\nActiveState=inactive\nSubState=dead\nActiveEnterTimestampMonotonic=0\nNRestarts=0\n"
    assert ex.service("x", text, now=NOW, mono=MONO)["since"] == ""


@pytest.mark.parametrize("text", [None, "", "garbage", "ActiveState", "=\n=\n", "ActiveState=<script>\nNRestarts=many\n",
                                  "ActiveState=active\nSubState=<b>x</b>\nActiveEnterTimestampMonotonic=-5\n"])
def test_systemctl_output_that_is_garbage_is_unknown_not_a_crash(text):
    s = ex.service("x", text, now=NOW, mono=MONO)
    assert s["name"] == "x" and s["sub_state"] == "" and s["since"] == "" and s["restarts"] is None
    assert s["active_state"] == ("active" if text and text.startswith("ActiveState=active") else "unknown")


def test_health_from_the_kernel_the_clock_service_and_the_filesystem():
    h = build()["health"]
    assert h["selinux"] == "enforcing" and h["ntp_synced"] is True
    assert h["var_free_bytes"] == 5424121 * 4096 == 22217199616
    assert h["var_used_pct"] == 24.9                             # used / (used + available), as df shows it


@pytest.mark.parametrize("text,want", [("1\n", "enforcing"), ("0\n", "permissive"), ("1", "enforcing"),
                                       (None, "disabled"), ("", "unknown"), ("2", "unknown"), ("enforcing", "unknown")])
def test_selinux_mode_from_the_selinuxfs_flag(text, want):
    assert ex.selinux_mode(text) == want


@pytest.mark.parametrize("text,want", [("NTPSynchronized=yes\n", True), ("NTPSynchronized=no\n", False),
                                       ("NTPSynchronized=maybe", None), ("", None), (None, None), ("yes", None)])
def test_ntp_sync_from_timedatectl(text, want):
    assert ex.ntp_synced(text) is want


def test_a_full_filesystem_reads_as_one_hundred_percent_not_a_division_error():
    full = ex.Vfs(f_frsize=4096, f_blocks=100, f_bfree=0, f_bavail=0)
    assert ex.health("1", "NTPSynchronized=yes", full)["var_used_pct"] == 100.0
    empty = ex.Vfs(f_frsize=4096, f_blocks=0, f_bfree=0, f_bavail=0)
    h = ex.health("1", "NTPSynchronized=yes", empty)
    assert h["var_used_pct"] is None and h["var_free_bytes"] == 0


# ── sections that cannot be collected come out empty, never as an error ──

@pytest.mark.parametrize("junk", [None, 0, "x", [], [1], {"status": None}, {"status": []}, {"status": {"booted": "x"}}])
def test_bootc_output_that_is_not_what_it_should_be_gives_an_empty_os_section(junk):
    o = build(bootc=junk)["os"]
    assert o["booted"] is None and o["staged"] is None and o["rollback"] is None
    assert o["rollback_queued"] is False and o["kernel"] == HOST["kernel"]


@pytest.mark.parametrize("junk", [None, [], "x", 5, {"A": "x"}, {"A": None}, {"A": []}, {"": {"version": "1"}}])
def test_an_esync_state_that_is_not_what_it_should_be_gives_no_components(junk):
    assert build(esync=junk)["components"] == []


def test_a_missing_input_gives_a_section_that_says_nothing_and_the_rest_still_comes():
    d = ex.build({}, now=NOW, mono=MONO, boot=BOOT)
    assert d["os"]["booted"] is None and d["components"] == [] and d["services"] == []
    assert d["health"]["selinux"] == "disabled" and d["health"]["ntp_synced"] is None
    assert d["health"]["var_used_pct"] is None and d["health"]["var_free_bytes"] is None
    assert ota.clean(d)["schema"] == 1                           # still a file the dashboard accepts


# ── the board's own record: build stamp, update tools, eSync ──
# Real formats from the J7 (2026-11-12), values replaced: tests/fixtures/ota/{build_info.txt, os_builds.txt,
# tool_versions.json, esyncclient_tail.txt, esync_state_d/}; os_release.txt and the model are the board's own.

FIX = os.path.join(ROOT, "tests", "fixtures", "ota")
STATE_D = os.path.join(FIX, "esync_state_d")
TOOL_OUT = fixture("ota/tool_versions.json")
LOG_NOW = 1794447400.0                                       # 2026-11-12T01:36:40Z, just after the log's last line


def fixture_text(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def fixture_bytes(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


HOME = "/root/esync"
IN_UNIT = "0::/system.slice/esync.service\n"
PROCS = [
    ([HOME + "/bin/esyncclient", "-c", HOME + "/etc/esync.cfg"], IN_UNIT),
    ([HOME + "/bin/workloadagent", "-k", HOME + "/data/InVehicle-Certs/pki/certs/rhivos_app", "-t", "/SDK/RHIVOS_APP",
      "-b", HOME + "/data"], IN_UNIT),
    # the same agent as eSync's launcher may pass it: each option and its value as one argument
    ([HOME + "/bin/workloadagent", "-k " + HOME + "/data/InVehicle-Certs/pki/certs/rhivos_os", "-t /SDK/RHIVOS_OS",
      "-b " + HOME + "/data"], "0::/system.slice/esync.service/payload\n"),
    ([HOME + "/bin/loggit", "-l", HOME + "/data/log/workloadagent-app.log"], IN_UNIT),
]
AGENTS = ("/SDK/RHIVOS_APP", "/SDK/RHIVOS_OS")
MODEL = "Texas Instruments J784S4 EVM\0"                      # /proc/device-tree/model ends in a NUL
# The real package lists of OS 4.6.0 and 4.3.0 (the J7's `rpm -qa`, 2026-11-11), in RPM_FORMAT. They are the
# lists of the Act 2 update; the bootc fixture's images carry other labels, which the packages do not depend on.
RPMS_NOW = fixture_text("rpm_qa_4.6.0.txt")
RPMS_BEFORE = fixture_text("rpm_qa_4.3.0.txt")
SHOWN = ("openssl", "zstd", "kernel-automotive")


def full_sources(**over):
    s = sources(esync=None, esync_d=ex.read_state_dir(STATE_D), build_info=fixture_text("build_info.txt"),
                tools=TOOL_OUT, records=fixture_text("os_builds.txt"), processes=PROCS,
                client_log=fixture_bytes("esyncclient_tail.txt"), os_release=fixture_text("os_release.txt"),
                board_model=MODEL, interval=5, os_interval=15,
                esync_config={"home": HOME, "unit": "esync", "topics": AGENTS, "os_package": "RHIVOS-OS-AUTOSD"},
                rpms=ex.rpm_list(RPMS_NOW), rpms_before=ex.rpm_list(RPMS_BEFORE), rpm_shown=SHOWN)
    s.update(over)
    return s


def build_full(**over):
    return ex.build(full_sources(**over), now=LOG_NOW, mono=MONO, boot=BOOT)


def test_the_build_stamp_is_the_images_version_and_build_time():
    assert ex.build_info(fixture_text("build_info.txt")) == {"version": "4.6.0", "built_at": "2026-11-11T21:38:59Z"}
    assert build_full()["os"]["build"] == {"version": "4.6.0", "built_at": "2026-11-11T21:38:59Z"}


@pytest.mark.parametrize("raw,want", [
    (None, None), ("", None), ("garbage\n", None), ('IMAGE_NAME="base"\n', None),
    ('IMAGE_VERSION="4.3.0"\n', {"version": "4.3.0", "built_at": ""}),
    ('TIMESTAMP="2026-08-01 20:17:50 UTC"\n', {"version": "", "built_at": "2026-08-01T20:17:50Z"}),
    ('IMAGE_VERSION=4.3.0\nTIMESTAMP="yesterday"\n', None),                 # unquoted, and not a time
    ('IMAGE_VERSION="4.3.0"\nTIMESTAMP="2026-13-01 20:17:50 UTC"\n', {"version": "4.3.0", "built_at": ""}),
    ('  IMAGE_VERSION="4.3.0"  \r\n', {"version": "4.3.0", "built_at": ""}),
])
def test_a_build_stamp_is_read_from_its_two_named_lines_only(raw, want):
    assert ex.build_info(raw) == want


def test_the_build_stamps_other_lines_never_get_out():
    text = json.dumps(build_full()["os"])
    for word in ("synthetic-build-id", "autosd10", "ebbr", "UUID", "DISTRO"):
        assert word not in text, word


def test_the_os_is_named_by_its_os_release_and_the_board_by_its_device_tree():
    d = build_full()
    assert d["os"]["distro"] == "Automotive Stream Distribution 10"
    assert d["board"] == {"model": "Texas Instruments J784S4 EVM"}
    assert d["esync"]["os_package"] == "RHIVOS-OS-AUTOSD"


@pytest.mark.parametrize("raw,want", [
    ('PRETTY_NAME="A B 10"\nNAME="A"\nVERSION_ID="9"\n', "A B 10"),         # the pretty name wins
    ('NAME="Fedora Linux"\nVERSION_ID=42\n', "Fedora Linux 42"),             # else the name and version; bare is fine
    ("PRETTY_NAME='Single Quoted 1'\n", "Single Quoted 1"),
    ("NAME=Bare\n", "Bare"),
    ('PRETTY_NAME="has \\"escape\\""\nNAME="Plain"\n', "Plain"),             # a line that needs the shell is skipped
    ('PRETTY_NAME="$(id)"\n', ""),
    ("ID=autosd\nHOME_URL=x\n", ""),                                         # names nothing
    ("", ""),
    (None, ""),
])
def test_the_os_name_is_the_pretty_name_else_name_and_version(raw, want):
    assert ex.distro(raw) == want


def test_the_rest_of_os_release_never_gets_out():
    text = json.dumps(build_full())
    for word in ("sig.centos.org", "cpe:/o", "platform:el10", "fedora-logo", "REDHAT_SUPPORT", "rhel fedora"):
        assert word not in text, word


@pytest.mark.parametrize("raw,want", [
    ("Texas Instruments J784S4 EVM\0", "Texas Instruments J784S4 EVM"),
    ("Raspberry Pi 5 Model B Rev 1.0\0", "Raspberry Pi 5 Model B Rev 1.0"),
    ("No NUL at all", "No NUL at all"),
    ("Model\0trailing junk after the NUL", "Model"),
    ("Bad\x01Model\0", ""),                                                   # not printable: nothing
    ("x" * 500 + "\0", "x" * 120),
    ("", ""),
    (None, ""),                                                                  # a PC has no device tree
])
def test_the_board_model_is_the_device_trees_text_up_to_its_nul(raw, want):
    assert ex.board_model(raw) == want


def test_os_release_is_read_from_etc_and_else_from_usr_lib():
    def io_with(files):
        return fake_io(read_file=lambda p: files.get(p))
    only_usr = {"/usr/lib/os-release": 'PRETTY_NAME="From Usr"\n'}
    both = dict(only_usr, **{"/etc/os-release": 'PRETTY_NAME="From Etc"\n'})
    for files, want in ((both, "From Etc"), (only_usr, "From Usr"), ({}, "")):
        src = ex.collect(ex.parse_args(["--out", "/x/o.json"]), io_with(files))
        assert ex.distro(src["os_release"]) == want


def test_the_os_package_is_an_option_and_empty_when_not_given():
    assert ex.parse_args(["--out", "/x/o.json"]).os_package is None
    src = ex.collect(ex.parse_args(["--out", "/x/o.json", "--os-package", "RHIVOS-OS-AUTOSD"]), fake_io())
    assert ex.build(src, now=NOW, mono=MONO, boot=BOOT)["esync"]["os_package"] == "RHIVOS-OS-AUTOSD"
    src = ex.collect(ex.parse_args(["--out", "/x/o.json"]), fake_io())
    assert ex.build(src, now=NOW, mono=MONO, boot=BOOT)["esync"]["os_package"] == ""


@pytest.mark.parametrize("name", ["-x", "a b", "../x", "a/b", "", "x" * 65, "$(id)", "A;B"])
def test_an_os_package_that_is_not_a_package_name_is_refused(name):
    with pytest.raises(SystemExit):
        ex.parse_args(["--out", "/x/o.json", "--os-package", name])


def test_the_update_tools_report_their_versions():
    assert ex.tools(TOOL_OUT) == {"bootc": "1.17.1", "ostree": "2026.4", "podman": "6.1.0"}
    assert "0000000000" not in json.dumps(build_full()), "ostree's git commit is not a version"


@pytest.mark.parametrize("name,raw,want", [
    ("bootc", "bootc 1.16.6\n", "1.16.6"), ("bootc", "bootc\n", ""), ("bootc", "something 1.0", ""),
    ("bootc", None, ""), ("bootc", "bootc <script>", ""),
    ("podman", "podman version 6.0.2\n", "6.0.2"), ("podman", "podman 6.0.2", ""), ("podman", "", ""),
    ("ostree", "libostree:\n Version: '2026.1'\n", "2026.1"), ("ostree", "Version: 2026.1", ""),
    ("ostree", " Version: '<b>'\n", ""), ("ostree", None, ""),
])
def test_a_tool_version_is_the_version_word_or_nothing(name, raw, want):
    assert ex.tool_version(name, raw) == want


def test_the_build_record_lists_each_version_with_what_it_was_built_with():
    r = ex.build_records(fixture_text("os_builds.txt"))
    assert [x["version"] for x in r] == ["4.3.0", "4.6.0", "4.7.1"]              # the comment and blank line skipped
    assert r[1] == {"version": "4.6.0", "kernel": "6.12.0-281.el10iv.aarch64", "bootc": "1.17.1",
                    "ostree": "2026.4", "podman": "6.1.0"}


@pytest.mark.parametrize("raw", [None, "", "# only a comment\n", "4.6.0 k 1 2\n", "4.6.0 k 1 2 3 4\n",
                                 "4.6.0 <k> 1 2 3\n", "4.6.0 k 1 2 $(id)\n"])
def test_a_build_record_line_that_is_not_five_versions_is_skipped(raw):
    assert ex.build_records(raw) == []


def test_build_records_are_capped():
    raw = "".join(f"{i}.0.0 k b o p\n" for i in range(ex.LIST_MAX + 10))
    assert len(ex.build_records(raw)) == ex.LIST_MAX


def test_components_come_from_the_per_package_files():
    c = build_full()["components"]
    assert c == [{"id": "RHIVOS-APP-CANDUMP", "version": "3.4.0", "installed_at": "2026-11-06T01:57:15Z",
                  "transaction_digest": "sha256:" + "a1" * 32},
                 {"id": "RHIVOS-OS-AUTOSD", "version": "4.7.0", "installed_at": "2026-11-11T23:45:22Z",
                  "transaction_digest": "sha256:" + "b2" * 32}]
    assert "activeSet" not in json.dumps(c) and "synthetic-active-set" not in json.dumps(c)


def test_a_packages_own_file_wins_over_the_shared_state_and_the_shared_state_fills_the_rest():
    """The agent reads state.d/<package>.json first and falls back to state.json (workload_rhivos_common.c)."""
    shared = {"RHIVOS-APP-CANDUMP": {"version": "3.1.0"}, "OLD-ONLY": {"version": "1.0"}}
    c = {x["id"]: x["version"] for x in build_full(esync=shared)["components"]}
    assert c == {"OLD-ONLY": "1.0", "RHIVOS-APP-CANDUMP": "3.4.0", "RHIVOS-OS-AUTOSD": "4.7.0"}
    assert ex.components(shared, None) == ex.components(shared, {}) == ex.components(shared)


def test_reading_state_d_takes_only_regular_json_files_and_skips_a_broken_one(tmp_path, capsys):
    (tmp_path / "GOOD.json").write_text('{"version": "1"}')
    (tmp_path / "HALF.json").write_text('{"version": ')               # the agent mid-write
    (tmp_path / "BIN.json").write_bytes(b"\xff\xfe")
    (tmp_path / ".HIDDEN.json").write_text('{"version": "2"}')
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "DIR.json").mkdir()
    (tmp_path / "BIG.json").write_text('{"v": "' + "x" * ex.STATE_FILE_MAX + '"}')
    outside = tmp_path.parent / "outside.json"
    outside.write_text('{"version": "9"}')
    (tmp_path / "LINK.json").symlink_to(outside)
    assert ex.read_state_dir(str(tmp_path)) == {"GOOD": {"version": "1"}}
    err = capsys.readouterr().err
    assert "HALF.json" in err and "BIN.json" in err and "BIG.json" in err


def test_a_state_d_that_does_not_exist_is_no_components_not_an_error(tmp_path):
    assert ex.read_state_dir(str(tmp_path / "nope")) == {}


def test_state_d_is_capped(tmp_path):
    for i in range(ex.LIST_MAX + 5):
        (tmp_path / f"P{i:03d}.json").write_text('{"version": "1"}')
    assert len(ex.read_state_dir(str(tmp_path))) == ex.LIST_MAX


def test_the_campaigns_are_the_ones_in_the_client_log_most_recent_first():
    e = build_full()["esync"]
    assert e["client_log"] == "ok"
    assert e["campaigns"] == [
        {"package": "RHIVOS-APP-CANDUMP", "version": "3.7.0", "campaign": 1001, "last_seen": "2026-11-12T01:36:25Z",
         "rollback_version": "3.4.0", "result": "ROLLBACK-COMPLETED"},          # the later of its two status lines
        {"package": "RHIVOS-OS-AUTOSD", "version": "4.7.0", "campaign": 1004, "last_seen": "2026-11-12T01:35:59Z",
         "rollback_version": "", "result": "NOT-COMPLETED"}]                    # "registered rollback to <null>"


def test_nothing_else_in_the_client_log_gets_out():
    text = json.dumps(build_full())
    for secret in ("SYNTHETIC-DEVICE-ID", "dmc.example", "apikey-0123", "@2001", "2001", "needs to be", "update_info",
                   "Package is of", "forced", "pending update", "had QP", "TV:", "considered", "<null>"):
        assert secret not in text, secret


STATUS = (b"11-12:01:%s.100 pui_init:update_info.cpp:313 [main/1] <RHIVOS-X:2.0#7@8> Package is of version 1.0, "
          b"needs to be of version 2.0, registered rollback to %s (forced:FALSE), pending update: FALSE, had QP: TRUE, "
          b"TV: FALSE, considered already updated: %s (%s)\n")


def status_line(at, rollback, result):
    return STATUS % (at, rollback, result, result)


def test_a_campaigns_rollback_version_and_result_come_from_its_latest_status_line():
    log = status_line(b"30:00", b"1.0", b"NOT-COMPLETED") + status_line(b"31:00", b"1.0", b"ROLLBACK-COMPLETED")
    assert ex.campaigns(log, LOG_NOW) == [{"package": "RHIVOS-X", "version": "2.0", "campaign": 7,
                                           "last_seen": "2026-11-12T01:31:00Z", "rollback_version": "1.0",
                                           "result": "ROLLBACK-COMPLETED"}]
    later_first = status_line(b"31:00", b"1.0", b"UPDATE-COMPLETED") + status_line(b"30:00", b"1.0", b"NOT-COMPLETED")
    assert ex.campaigns(later_first, LOG_NOW)[0]["result"] == "UPDATE-COMPLETED"       # by time, not by place


@pytest.mark.parametrize("line", [
    status_line(b"30:00", b"1.0 && id", b"NOT-COMPLETED"),                  # a rollback that is not a version
    status_line(b"30:00", b"1.0", b"not-completed"),                        # a result that is not eSync's word
    b"11-12:01:30:00.100 x <RHIVOS-X:2.0#7@8> Package is of version 1.0, registered rollback to 1.0\n",  # cut short
])
def test_a_status_line_that_is_not_the_clients_leaves_the_campaign_without_one(line):
    assert ex.campaigns(line, LOG_NOW) == [{"package": "RHIVOS-X", "version": "2.0", "campaign": 7,
                                            "last_seen": "2026-11-12T01:30:00Z", "rollback_version": "", "result": ""}]


def campaign_line(at, package, version, number):
    return b"11-12:01:%s.100 pui_init:update_info.cpp:313 [main/1] <%s:%s#%d@8> x\n" % (at, package, version, number)


@pytest.mark.parametrize("old, new", [(b"3.7.0", b"3.8.0"), (b"3.9.0", b"3.10.0"), (b"3.10.0", b"3.9.0")])
@pytest.mark.parametrize("old_first", [True, False])
def test_of_two_campaigns_of_a_package_logged_in_the_same_second_the_newer_number_comes_first(old, new, old_first):
    """The client logs every campaign it still works on at each session, so an old campaign it keeps confirming
    and a new one carry the same second. The page takes the first campaign of a package as its latest."""
    a = campaign_line(b"30:00", b"RHIVOS-X", old, 1001)
    b = campaign_line(b"30:00", b"RHIVOS-X", new, 1002)
    got = ex.campaigns(a + b if old_first else b + a, LOG_NOW)
    assert [c["campaign"] for c in got] == [1002, 1001]           # by number, not by the version as a string


def test_a_later_second_still_comes_before_a_higher_campaign_number():
    log = campaign_line(b"31:00", b"RHIVOS-X", b"3.7.0", 1001) + campaign_line(b"30:00", b"RHIVOS-X", b"3.8.0", 1002)
    assert [c["campaign"] for c in ex.campaigns(log, LOG_NOW)] == [1001, 1002]     # the time is the first key


def test_campaigns_of_different_packages_in_one_second_keep_a_fixed_order():
    log = campaign_line(b"30:00", b"RHIVOS-OS", b"4.7.0", 1004) + campaign_line(b"30:00", b"RHIVOS-APP", b"3.7.0", 1001)
    assert [c["package"] for c in ex.campaigns(log, LOG_NOW)] == ["RHIVOS-APP", "RHIVOS-OS"]


def test_a_status_line_cut_inside_or_right_after_its_result_does_not_replace_the_previous_result():
    """A tail that ends mid-write: the newest status line stops inside eSync's word. A fragment such as NOT-CO
    must not become the campaign's result; the campaign keeps its previous complete line."""
    word = b"ROLLBACK-COMPLETED"
    done = status_line(b"30:00", b"1.0", b"NOT-COMPLETED")
    full = status_line(b"31:00", b"1.0", word)
    start = full.index(b"considered already updated: ") + len(b"considered already updated: ")
    for cut in range(start, start + len(word) + 1):               # none of the word ... the whole word, nothing after
        got = ex.campaigns(done + full[:cut], LOG_NOW)
        assert got[0]["result"] == "NOT-COMPLETED", full[:cut][-12:]
    whole = ex.campaigns(done + full[:start + len(word) + 1], LOG_NOW)      # the word and the space after it
    assert whole[0]["result"] == "ROLLBACK-COMPLETED"


def test_a_status_line_without_the_parenthesis_after_the_result_is_still_read():
    line = status_line(b"30:00", b"1.0", b"UPDATE-COMPLETED").replace(b" (UPDATE-COMPLETED)", b"")
    assert ex.campaigns(line, LOG_NOW)[0]["result"] == "UPDATE-COMPLETED"


def test_a_campaign_line_without_a_time_at_its_start_is_not_read():
    log = (b"mpaign <RHIVOS-X:1.0#7@8> cut off\n"                       # the first line of a tail is often cut
           b"  <RHIVOS-X:1.0#7@8> continuation\n"
           b"11-12:01:00:00.000 f:x.cpp:1 [m] <RHIVOS-Y:2.0#9@10> ok\n")
    assert [c["package"] for c in ex.campaigns(log, LOG_NOW)] == ["RHIVOS-Y"]


@pytest.mark.parametrize("log", [None, b"", b"\x00\xff" * 100, "11-12:01:00:00.000 [m] <RHIVOS-Y:2.0#9@10>",
                                 b"11-12:01:00:00.000 [m] <rhivos-y:2.0#9@10>", b"13-40:01:00:00.000 [m] <RHIVOS-Y:2.0#9@10>",
                                 b"11-12:01:00:00.000 [m] <RHIVOS-Y:2.0#nine@10>"])
def test_a_client_log_with_no_readable_campaign_has_none(log):
    assert ex.campaigns(log, LOG_NOW) == []


def test_a_log_time_has_no_year_so_it_is_this_years_unless_that_is_in_the_future():
    jan_2 = 1798848000.0                                          # 2027-01-02T00:00:00Z
    assert ex.iso(ex.log_time(12, 31, 23, 59, 0, jan_2)) == "2026-12-31T23:59:00Z"
    assert ex.iso(ex.log_time(1, 1, 23, 0, 0, jan_2)) == "2027-01-01T23:00:00Z"
    assert ex.iso(ex.log_time(1, 2, 23, 0, 0, jan_2)) == "2027-01-02T23:00:00Z"    # less than a day ahead: clock skew
    assert ex.log_time(2, 29, 0, 0, 0, jan_2) is None                               # 2027 and 2026 have no 29 February
    assert ex.log_time(2, 29, 0, 0, 0, 1709251200.0) is not None                    # 2024-03-01: a leap year


def test_the_esync_processes_are_counted_and_all_of_them_are_in_the_unit():
    e = build_full()["esync"]
    assert (e["unit"], e["processes"], e["outside_unit"]) == ("esync", 4, 0)       # a sub-cgroup of the unit is in it
    assert e["agents"] == [{"topic": "/SDK/RHIVOS_APP", "running": 1}, {"topic": "/SDK/RHIVOS_OS", "running": 1}]


@pytest.mark.parametrize("cgroup", ["0::/user.slice/user-0.slice/session-3.scope\n", "0::/system.slice/esyncd.service\n",
                                    "0::/system.slice/esync.service-x\n", "1:name=systemd:/system.slice/esync.service\n",
                                    "", None])
def test_a_process_outside_the_units_cgroup_is_counted_as_outside(cgroup):
    procs = PROCS[:1] + [(PROCS[1][0], cgroup)]
    assert ex.esync_section(procs, None, LOG_NOW)["outside_unit"] == 1


def test_an_agent_that_is_expected_and_not_running_is_listed_with_zero_and_a_duplicate_with_two():
    procs = [PROCS[1], PROCS[1]]
    e = ex.esync_section(procs, None, LOG_NOW, home=HOME, topics=AGENTS)
    assert e["agents"] == [{"topic": "/SDK/RHIVOS_APP", "running": 2}, {"topic": "/SDK/RHIVOS_OS", "running": 0}]


def test_an_agent_with_a_topic_nobody_asked_for_is_still_listed():
    e = ex.esync_section(PROCS, None, LOG_NOW, home=HOME, topics=("/SDK/RHIVOS_APP",))
    assert [a["topic"] for a in e["agents"]] == ["/SDK/RHIVOS_APP", "/SDK/RHIVOS_OS"]


@pytest.mark.parametrize("argv,want", [
    (["/x/workloadagent", "-t", "/SDK/A"], "/SDK/A"),
    (["/x/workloadagent", "-t /SDK/A"], "/SDK/A"),
    (["/x/workloadagent", "-t/SDK/A"], "/SDK/A"),
    (["/x/workloadagent", "-k", "/a/b", "-t", "/SDK/B_2", "-t", "/SDK/C"], "/SDK/B_2"),
    (["/x/workloadagent", "-t"], ""),
    (["/x/workloadagent", "-t", "../../etc/passwd"], ""),
    (["/x/workloadagent", "-t", "/SDK/A B"], ""),
    (["/x/workloadagent", "-t", "/" + "a" * 50], ""),
    (["/x/workloadagent", "--trace", "/SDK/A"], ""),
    (["/x/workloadagent"], ""),
    (["-t", "/SDK/A"], ""),                                           # argv[0] is the program, never an option
])
def test_an_agents_topic_is_its_t_option_in_either_form_and_nothing_else(argv, want):
    assert ex.agent_topic(argv) == want


def test_only_the_agent_program_counts_as_an_agent():
    lookalike = (["/root/esync/bin/workloadagent-old", "-t", "/SDK/RHIVOS_APP"], IN_UNIT)
    e = ex.esync_section([lookalike], None, LOG_NOW, home=HOME, topics=AGENTS)
    assert all(a["running"] == 0 for a in e["agents"]) and e["processes"] == 1


def test_no_esync_on_the_board_is_said_plainly():
    e = ex.esync_section([], None, LOG_NOW, home=HOME, topics=AGENTS)
    assert e == {"unit": "esync", "os_package": "", "processes": 0, "outside_unit": 0, "client_log": "unavailable", "campaigns": [],
                 "agents": [{"topic": "/SDK/RHIVOS_APP", "running": 0}, {"topic": "/SDK/RHIVOS_OS", "running": 0}]}


def test_a_proc_that_could_not_be_read_is_unknown_not_zero():
    e = ex.esync_section(None, None, LOG_NOW, topics=AGENTS)
    assert e["processes"] is None and e["outside_unit"] is None and {a["running"] for a in e["agents"]} == {None}


def test_a_process_command_line_never_gets_out():
    text = json.dumps(build_full())
    for secret in ("InVehicle-Certs", "rhivos_app", "loggit", "esync.cfg", "-k", HOME + "/data"):
        assert secret not in text, secret


# ── the host readers, against a fake /proc and real files ──

def fake_proc(root, pid, cmdline, cgroup=IN_UNIT):
    d = root / str(pid)
    d.mkdir()
    if cmdline is not None:
        (d / "cmdline").write_bytes(cmdline)
    if cgroup is not None:
        (d / "cgroup").write_text(cgroup)


def test_processes_reads_only_the_programs_under_the_prefix(tmp_path):
    fake_proc(tmp_path, 10, b"/root/esync/bin/esyncclient\0-c\0/root/esync/etc/x\0")
    fake_proc(tmp_path, 11, b"/root/esync/bin/workloadagent\0-t\0/SDK/A\0", "0::/user.slice/x.scope\n")
    fake_proc(tmp_path, 12, b"/root/esync-bak/bin/esyncclient\0")             # not under the prefix
    fake_proc(tmp_path, 13, b"/usr/bin/python3\0/root/esync/bin/esyncclient\0")
    fake_proc(tmp_path, 14, b"")                                              # a kernel thread
    fake_proc(tmp_path, 15, b"/root/esync/bin/gone\0", cgroup=None)           # exited between the two reads
    fake_proc(tmp_path, 16, None)                                             # exited before
    (tmp_path / "self").mkdir()
    (tmp_path / "sys").mkdir()
    got = sorted(ex.processes("/root/esync/bin/", proc=str(tmp_path)))
    assert got == [(["/root/esync/bin/esyncclient", "-c", "/root/esync/etc/x"], IN_UNIT),
                   (["/root/esync/bin/workloadagent", "-t", "/SDK/A"], "0::/user.slice/x.scope\n")]


def test_processes_on_this_machine_finds_this_process():
    """The real /proc, read the real way: this test's own process has a command line and a cgroup."""
    with open("/proc/self/cmdline", "rb") as f:
        me = f.read().split(b"\0")
    found = ex.processes(me[0].decode())
    assert [a.decode() for a in me if a] in [argv for argv, _ in found]
    assert all(cg.startswith("0::") for _, cg in found)


def test_the_log_tail_is_the_end_of_the_file(tmp_path):
    p = tmp_path / "esyncclient.log"
    p.write_bytes(b"a" * 100 + b"\nlast line\n")
    assert ex.log_tail(str(p), limit=10) == b"last line\n"
    assert ex.log_tail(str(p)) == p.read_bytes()
    assert ex.log_tail(str(tmp_path / "rotated-away")) is None


# ── how often: each source at its own pace ──

def counting_io(bootc=lambda: json.dumps(BOOTC), tool=lambda name: TOOL_OUT[name]):
    calls = []

    def run(cmd, timeout=10):
        calls.append(tuple(cmd))
        if cmd[:2] == ["bootc", "status"]:
            return bootc()
        if tuple(cmd) in {tuple(v) for v in ex.TOOL_COMMANDS.values()}:
            return tool(cmd[0])
        return HOST["ntp"] if cmd[0] == "timedatectl" else HOST["systemctl_show"]["esync"]

    return calls, fake_io(run=run, read_file=lambda p: fixture_text("build_info.txt") if p == "/etc/build-info" else None)


def args(*extra):
    return ex.parse_args(["--out", "/x/o.json", "--service", "esync", "--interval", "5", *extra])


def passes(collector, n, step=5.0, t0=LOG_NOW, m0=500.0):
    return [collector.collect(t0 + i * step, m0 + i * step) for i in range(n)]


def test_bootc_is_asked_at_the_os_interval_and_the_cheap_sources_on_every_pass():
    calls, io = counting_io()
    srcs = passes(ex.Collector(args(), io), 7)                    # 0, 5, ... 30 s
    assert calls.count(("bootc", "status", "--format", "json")) == 3          # at 0, 15 and 30 s
    assert sum(c[0] == "systemctl" for c in calls) == 7
    assert [s["bootc_at"] for s in srcs] == [LOG_NOW] * 3 + [LOG_NOW + 15] * 3 + [LOG_NOW + 30]
    assert all(s["interval"] == 5 and s["os_interval"] == 15 for s in srcs)


def test_the_build_stamp_and_the_tool_versions_are_read_once():
    calls, io = counting_io()
    passes(ex.Collector(args(), io), 12)
    for cmd in ex.TOOL_COMMANDS.values():
        assert calls.count(tuple(cmd)) == 1, cmd


def test_a_tool_that_did_not_answer_is_asked_again_at_the_os_interval_until_it_does():
    answers = iter([None, None, "podman version 6.1.0\n"])
    calls, io = counting_io(tool=lambda name: next(answers) if name == "podman" else TOOL_OUT[name])
    srcs = passes(ex.Collector(args(), io), 10)                  # 0 .. 45 s: OS passes at 0, 15, 30, 45
    assert calls.count(("podman", "-v")) == 3
    assert [ex.tools(s["tools"])["podman"] for s in srcs] == [""] * 6 + ["6.1.0"] * 4


def test_a_failed_bootc_call_keeps_the_last_good_answer_with_the_time_it_was_given():
    answers = iter([json.dumps(BOOTC), None, "not json", json.dumps({"status": None})])
    calls, io = counting_io(bootc=lambda: next(answers))
    c = ex.Collector(args(), io)
    srcs = passes(c, 10)                                          # OS passes at 0 (good), 15, 30, 45 (all bad)
    assert all(s["bootc"] == BOOTC for s in srcs) and {s["bootc_at"] for s in srcs} == {LOG_NOW}
    d = ex.build(srcs[-1], now=LOG_NOW + 45, mono=MONO, boot=BOOT)
    assert d["os"]["booted"]["version"] == "4.4.0" and d["os"]["as_of"] == ex.iso(LOG_NOW)


def test_bootc_never_answering_leaves_the_os_section_empty_and_says_so():
    calls, io = counting_io(bootc=lambda: None)
    src = passes(ex.Collector(args(), io), 1)[0]
    d = ex.build(src, now=LOG_NOW, mono=MONO, boot=BOOT)
    assert d["os"]["booted"] is None and d["os"]["as_of"] == ""


def test_a_one_shot_document_is_as_of_now_and_declares_no_interval():
    d = build()
    assert d["os"]["as_of"] == d["generated_at"] and d["interval_s"] is None and d["os_interval_s"] is None
    assert build(bootc=None)["os"]["as_of"] == "" and build(bootc="junk")["os"]["as_of"] == ""


def test_the_loop_keeps_a_fixed_rate_whatever_a_pass_costs(tmp_path, monkeypatch):
    fakes(monkeypatch)
    clock = FakeClock()

    class Slow:
        costs = iter([2.0, 0.5, 7.0, 1.0])                         # the third pass overruns the 5 s interval

        def document(self):
            clock.advance(next(self.costs))
            return {"schema": 1}

    def nap(s):
        clock.sleep(s)
        if len(clock.naps) == 4:
            raise KeyboardInterrupt

    assert ex.main(["--out", str(tmp_path / "o.json"), "--interval", "5"], sleep=nap, clock=clock,
                   collector=Slow()) == 0
    assert clock.naps == [3.0, 4.5, 0, 4.0]                       # an overrun starts the next pass at once, no burst


@pytest.mark.parametrize("flag,value", [("--agent", "RHIVOS_APP"), ("--agent", "/SDK/A B"), ("--agent", "-t"),
                                        ("--esync-home", "root/esync"), ("--esync-home", "/root/../etc"),
                                        ("--esync-home", "/"), ("--esync-unit", "-x"), ("--esync-unit", "a/b"),
                                        ("--os-interval", "0"), ("--os-interval", "x")])
def test_bad_esync_and_cadence_options_are_refused(flag, value):
    with pytest.raises(SystemExit):
        ex.parse_args(["--out", "/x/o.json", flag, value])


def test_the_esync_options_have_board_defaults_and_take_a_trailing_slash():
    a = ex.parse_args(["--out", "/x/o.json"])
    assert (a.esync_home, a.esync_unit, a.agent, a.os_interval) == ("/root/esync", "esync", [], 15)
    assert ex.parse_args(["--out", "/x/o.json", "--esync-home", "/opt/esync/"]).esync_home == "/opt/esync"


# ── the OS image's packages (--show-rpm) ──

ROLLBACK_DB = ("/sysroot/ostree/deploy/default/deploy/" + BOOTC["status"]["rollback"]["ostree"]["checksum"]
               + ".1160/usr/lib/sysimage/rpm")
RPM_NOW_CMD = ("rpm", "-qa", "--qf", ex.RPM_FORMAT)
RPM_BEFORE_CMD = ("rpm", "--dbpath", ROLLBACK_DB, "-qa", "--qf", ex.RPM_FORMAT)


def test_a_package_list_is_each_name_with_rpms_epoch_and_version_and_no_signing_keys():
    now = ex.rpm_list(RPMS_NOW)
    assert len(now) == 255 and "gpg-pubkey" not in now and "gpg-pubkey" in RPMS_NOW
    assert now["openssl"] == "1:3.5.9-1.el10" and now["kernel-automotive"] == "0:6.12.0-281.el10iv"


@pytest.mark.parametrize("line", ["openssl 1:3.5.9-1.el10", "openssl\t", "\t0:3.5.9", "open ssl\t0:3.5.9", "-x\t0:1",
                                  "openssl\t0:3.5.9 1", "openssl\t0:<b>", "a/b\t0:1", "openssl\t0:3.5.9\textra",
                                  "openssl\t3.5.9-1.el10", "openssl\tx:3.5.9", "openssl\t1:", "openssl\t:3.5.9",
                                  "openssl\t12345678901:3.5.9", "openssl\t1:1:3.5.9"])
def test_a_line_that_is_not_a_name_a_tab_and_an_epoch_version_is_skipped(line):
    assert ex.rpm_list(line + "\nzstd\t0:1.5.5-9.el10\n") == {"zstd": "0:1.5.5-9.el10"}


def test_a_package_list_that_is_not_text_is_none_and_an_empty_one_is_empty():
    assert ex.rpm_list(None) is None and ex.rpm_list(b"zstd\t0:1") is None and ex.rpm_list("") == {}


def test_a_package_installed_in_two_versions_keeps_both():
    raw = "kernel-automotive\t0:6.12.0-281.el10iv\nkernel-automotive\t0:6.12.0-251.el10iv\n"
    assert ex.rpm_list(raw) == {"kernel-automotive": "0:6.12.0-251.el10iv, 0:6.12.0-281.el10iv"}


# rpm's own order (rpmvercmp, rpm's tests/rpmvercmp.at): each pair as (a, b, what rpm says of a against b).
VERCMP = [
    ("1.0", "1.0", 0), ("1.0", "2.0", -1), ("2.0", "1.0", 1), ("2.0.1", "2.0.1", 0), ("2.0", "2.0.1", -1),
    ("2.0.1", "2.0", 1), ("2.0.1a", "2.0.1a", 0), ("2.0.1a", "2.0.1", 1), ("2.0.1", "2.0.1a", -1),
    ("5.5p1", "5.5p1", 0), ("5.5p1", "5.5p2", -1), ("5.5p2", "5.5p1", 1), ("5.5p10", "5.5p10", 0),
    ("5.5p1", "5.5p10", -1), ("5.5p10", "5.5p1", 1), ("10xyz", "10.1xyz", -1), ("10.1xyz", "10xyz", 1),
    ("xyz10", "xyz10", 0), ("xyz10", "xyz10.1", -1), ("xyz10.1", "xyz10", 1), ("xyz.4", "xyz.4", 0),
    ("xyz.4", "8", -1), ("8", "xyz.4", 1), ("xyz.4", "2", -1), ("2", "xyz.4", 1), ("5.5p2", "5.6p1", -1),
    ("5.6p1", "5.5p2", 1), ("5.6p1", "6.5p1", -1), ("6.5p1", "5.6p1", 1), ("6.0.rc1", "6.0", 1),
    ("6.0", "6.0.rc1", -1), ("10b2", "10a1", 1), ("10a2", "10b2", -1), ("1.0aa", "1.0aa", 0), ("1.0a", "1.0aa", -1),
    ("1.0aa", "1.0a", 1), ("10.0001", "10.0001", 0), ("10.0001", "10.1", 0), ("10.1", "10.0001", 0),
    ("10.0001", "10.0039", -1), ("10.0039", "10.0001", 1), ("4.999.9", "5.0", -1), ("5.0", "4.999.9", 1),
    ("20101121", "20101121", 0), ("20101121", "20101122", -1), ("20101122", "20101121", 1), ("2_0", "2_0", 0),
    ("2.0", "2_0", 0), ("2_0", "2.0", 0), ("a", "a", 0), ("a+", "a+", 0), ("a+", "a_", 0), ("a_", "a+", 0),
    ("+a", "+a", 0), ("+a", "_a", 0), ("_a", "+a", 0), ("+_", "+_", 0), ("_+", "+_", 0), ("_+", "_+", 0),
    ("+", "_", 0), ("_", "+", 0), ("1.0~rc1", "1.0~rc1", 0), ("1.0~rc1", "1.0", -1), ("1.0", "1.0~rc1", 1),
    ("1.0~rc1", "1.0~rc2", -1), ("1.0~rc2", "1.0~rc1", 1), ("1.0~rc1~git123", "1.0~rc1~git123", 0),
    ("1.0~rc1~git123", "1.0~rc1", -1), ("1.0~rc1", "1.0~rc1~git123", 1), ("1.0^", "1.0^", 0), ("1.0^", "1.0", 1),
    ("1.0", "1.0^", -1), ("1.0^git1", "1.0^git1", 0), ("1.0^git1", "1.0", 1), ("1.0", "1.0^git1", -1),
    ("1.0^git1", "1.0^git2", -1), ("1.0^git2", "1.0^git1", 1), ("1.0^git1", "1.01", -1), ("1.01", "1.0^git1", 1),
    ("1.0^20160101", "1.0^20160101", 0), ("1.0^20160101", "1.0.1", -1), ("1.0.1", "1.0^20160101", 1),
    ("1.0^20160101^git1", "1.0^20160101^git1", 0), ("1.0^20160102", "1.0^20160101^git1", 1),
    ("1.0^20160101^git1", "1.0^20160102", -1), ("1.0~rc1^git1", "1.0~rc1^git1", 0), ("1.0~rc1^git1", "1.0~rc1", 1),
    ("1.0~rc1", "1.0~rc1^git1", -1), ("1.0^git1~pre", "1.0^git1~pre", 0), ("1.0^git1", "1.0^git1~pre", 1),
    ("1.0^git1~pre", "1.0^git1", -1),
    # the forms in the J7's images
    ("1.el10.3", "1.4.el10", -1), ("2025.01", "2023.03", 1), ("1.git4dcd294.el10", "1.git988eb15.el10", -1),
    ("2026.2.92_v9.0.319", "2025.2.80_v9.0.305", 1), ("9.9p1", "9.9p1", 0), ("3.el10iv", "3.el10", 1),
]


@pytest.mark.parametrize("a, b, want", VERCMP)
def test_versions_are_ordered_as_rpm_orders_them(a, b, want):
    assert ex.rpmvercmp(a, b) == want


@pytest.mark.parametrize("a, b, want", [
    ("0:1.0-1", "0:1.0-1", 0), ("1:1.0-1", "0:2.0-1", 1),            # the epoch first, whatever the version says
    ("0:2.0-1", "1:1.0-1", -1), ("0:1.0-2", "0:1.0.1-1", -1),        # then the version, before the release
    ("0:1.0-2", "0:1.0-10", -1), ("3:1.24.0-3.el10", "2:1.23.0-1.el10", 1),
    ("1.0-1", "0:1.0-1", 0), ("1.0", "0:1.0-1", -1),                 # no epoch is 0; no release sorts first
])
def test_an_epoch_version_release_is_ordered_epoch_then_version_then_release(a, b, want):
    assert ex.evr_compare(a, b) == want


def test_the_act_2_update_changed_119_packages_and_added_one():
    p = ex.packages_section(ex.rpm_list(RPMS_NOW), ex.rpm_list(RPMS_BEFORE), SHOWN)
    assert (p["total"], p["compared"], p["updated"], p["downgraded"], p["added"], p["removed"]) == (
        255, True, 119, 0, 1, 0)
    assert p["shown"] == [                                    # versions as `rpm -q` writes them, without the epoch
        {"name": "openssl", "version": "3.5.9-1.el10", "before": "3.5.7-2.el10", "change": "updated"},
        {"name": "zstd", "version": "1.5.5-9.el10", "before": "", "change": "added"},
        {"name": "kernel-automotive", "version": "6.12.0-281.el10iv", "before": "6.12.0-251.el10iv", "change": "updated"}]


def test_the_reset_from_4_6_0_back_to_4_3_0_downgraded_them():
    """The demo's reset campaign: 4.3.0 runs and 4.6.0 is the rollback image."""
    p = ex.packages_section(ex.rpm_list(RPMS_BEFORE), ex.rpm_list(RPMS_NOW), SHOWN)
    assert (p["total"], p["updated"], p["downgraded"], p["added"], p["removed"]) == (254, 0, 119, 0, 1)
    assert [(s["name"], s["version"], s["before"], s["change"]) for s in p["shown"]] == [
        ("openssl", "3.5.7-2.el10", "3.5.9-1.el10", "downgraded"), ("zstd", "", "1.5.5-9.el10", "removed"),
        ("kernel-automotive", "6.12.0-251.el10iv", "6.12.0-281.el10iv", "downgraded")]


def test_each_shown_package_says_what_happened_to_it():
    now = {"a": "0:1-1", "b": "0:2-1", "d": "0:4-1", "f": "0:1-1"}
    before = {"a": "0:1-1", "b": "0:1-1", "c": "0:3-1", "f": "0:2-1"}
    p = ex.packages_section(now, before, ("a", "b", "c", "d", "e", "f"))
    assert [(s["name"], s["change"]) for s in p["shown"]] == [("a", "same"), ("b", "updated"), ("c", "removed"),
                                                              ("d", "added"), ("e", ""), ("f", "downgraded")]
    assert p["shown"][2] == {"name": "c", "version": "", "before": "3-1", "change": "removed"}
    assert (p["total"], p["updated"], p["downgraded"], p["added"], p["removed"]) == (4, 1, 1, 1, 1)


def test_a_change_only_in_the_epoch_keeps_the_epochs_so_the_two_versions_differ_on_screen():
    p = ex.packages_section({"a": "1:2.0-1", "b": "0:1.0-1"}, {"a": "1:2.0-1", "b": "2:1.0-1"}, ("a", "b"))
    assert [(s["version"], s["before"], s["change"]) for s in p["shown"]] == [
        ("2.0-1", "2.0-1", "same"), ("1.0-1", "2:1.0-1", "downgraded")]       # an epoch of 0 is not written


def test_a_version_rpm_orders_the_same_but_written_otherwise_counts_as_updated():
    p = ex.packages_section({"a": "0:1.01-1"}, {"a": "0:1.1-1"}, ("a",))
    assert (p["updated"], p["downgraded"], p["shown"][0]["change"]) == (1, 0, "updated")


def test_a_package_in_two_versions_is_compared_by_its_newest():
    p = ex.packages_section({"k": "0:6.12.0-251.el10iv, 0:6.12.0-281.el10iv"}, {"k": "0:6.12.0-260.el10iv"}, ("k",))
    assert p["shown"] == [{"name": "k", "version": "6.12.0-251.el10iv, 6.12.0-281.el10iv",
                           "before": "6.12.0-260.el10iv", "change": "updated"}]
    p = ex.packages_section({"k": "0:6.12.0-251.el10iv"}, {"k": "0:6.12.0-240.el10iv, 0:6.12.0-281.el10iv"}, ("k",))
    assert p["shown"][0]["change"] == "downgraded"


def test_without_the_rollback_images_list_nothing_is_compared():
    p = ex.packages_section(ex.rpm_list(RPMS_NOW), None, ("openssl",))
    assert (p["total"], p["compared"], p["updated"], p["downgraded"], p["added"], p["removed"]) == (
        255, False, None, None, None, None)
    assert p["shown"] == [{"name": "openssl", "version": "3.5.9-1.el10", "before": "", "change": ""}]
    assert ex.packages_section(None, ex.rpm_list(RPMS_BEFORE), ("openssl",)) is None
    assert build()["os"]["packages"] is None                               # a run without --show-rpm


def test_the_rollback_images_database_is_found_from_what_bootc_names_its_deployment_by():
    assert ex.rollback_rpmdb(BOOTC) == ROLLBACK_DB
    assert ex.rollback_rpmdb({"status": {"rollback": None}}) is None and ex.rollback_rpmdb(None) is None


@pytest.mark.parametrize("over", [{"stateroot": "../etc"}, {"stateroot": "a/b"}, {"stateroot": ""}, {"stateroot": 1},
                                  {"checksum": "AB" * 32}, {"checksum": "ab" * 31}, {"checksum": "../" + "a" * 61},
                                  {"deploySerial": "1"}, {"deploySerial": -1}, {"deploySerial": True},
                                  {"deploySerial": 10 ** 7}, {"checksum": None}])
def test_a_deployment_path_needs_each_part_in_the_one_form_it_has(over):
    tree = dict(BOOTC["status"]["rollback"]["ostree"], **over)
    assert ex.deployment_rpmdb({"ostree": tree}) is None
    assert ex.deployment_rpmdb({"ostree": "x"}) is None and ex.deployment_rpmdb({}) is None


def rpm_io(answers=None):
    """A host whose rpm answers from `answers` ({command: output}); every other command as counting_io's."""
    answers = {RPM_NOW_CMD: RPMS_NOW, RPM_BEFORE_CMD: RPMS_BEFORE} if answers is None else answers
    calls, io = counting_io()
    others = io.run

    def run(cmd, timeout=10):
        if cmd[0] == "rpm":
            calls.append(tuple(cmd))
            return answers.get(tuple(cmd))
        return others(cmd, timeout)

    io.run = run
    return calls, io


def test_a_one_shot_run_reads_both_package_lists_with_rpm():
    calls, io = rpm_io()
    d = ex.build(ex.collect(ex.parse_args(["--out", "/x/o.json", "--show-rpm", "openssl"]), io),
                 now=NOW, mono=MONO, boot=BOOT)
    assert [c for c in calls if c[0] == "rpm"] == [RPM_NOW_CMD, RPM_BEFORE_CMD]
    assert d["os"]["packages"]["updated"] == 119 and d["os"]["packages"]["shown"][0]["change"] == "updated"
    calls, io = rpm_io()
    ex.collect(ex.parse_args(["--out", "/x/o.json"]), io)
    assert not [c for c in calls if c[0] == "rpm"]                         # no --show-rpm: rpm is never run


def test_the_running_images_list_is_read_once_and_the_rollback_images_when_it_changes():
    calls, io = rpm_io()
    c = ex.Collector(args("--show-rpm", "openssl"), io)
    srcs = passes(c, 7)                                                    # OS passes at 0, 15, 30 s
    assert calls.count(RPM_NOW_CMD) == 1 and calls.count(RPM_BEFORE_CMD) == 1
    assert all(s["rpms_before"]["openssl"] == "1:3.5.7-2.el10" for s in srcs)
    other = json.loads(json.dumps(BOOTC))
    other["status"]["rollback"]["ostree"]["deploySerial"] = 1161          # an undeploy and a new rollback image
    c.bootc = other
    c.collect_packages()
    assert calls[-1] == ("rpm", "--dbpath", ROLLBACK_DB.replace(".1160/", ".1161/"), "-qa", "--qf", ex.RPM_FORMAT)
    assert c.rpms_before is None                                           # that database answered nothing
    c.bootc = {"status": {"booted": BOOTC["status"]["booted"], "rollback": None}}
    c.collect_packages()
    assert c.rpms_before is None and c.rpms_before_db is None and calls.count(RPM_NOW_CMD) == 1


def test_a_package_list_that_could_not_be_read_is_read_again_at_the_os_interval():
    calls, io = rpm_io(answers={})                                         # rpm answers nothing
    src = passes(ex.Collector(args("--show-rpm", "openssl"), io), 7)[-1]   # OS passes at 0, 15, 30 s
    assert calls.count(RPM_NOW_CMD) == 3 and calls.count(RPM_BEFORE_CMD) == 3
    assert ex.build(src, now=LOG_NOW, mono=MONO, boot=BOOT)["os"]["packages"] is None


@pytest.mark.parametrize("name", ["-qa", "--dbpath", "a b", "a;b", "a/b", "../x", "", "x" * 101, "$(id)"])
def test_a_shown_package_name_that_is_not_a_package_name_is_refused(name):
    with pytest.raises(SystemExit):
        ex.parse_args(["--out", "/x/o.json", "--show-rpm", name])


def test_shown_packages_keep_their_order_once_each_and_are_capped():
    a = ex.parse_args(["--out", "/x/o.json", "--show-rpm", "zstd", "--show-rpm", "openssl", "--show-rpm", "zstd"])
    assert a.show_rpm == ["zstd", "openssl"] and ex.parse_args(["--out", "/x/o.json"]).show_rpm == []
    many = [part for i in range(ex.RPM_SHOWN_MAX + 1) for part in ("--show-rpm", f"p{i}")]
    with pytest.raises(SystemExit):
        ex.parse_args(["--out", "/x/o.json", *many])


# ── what must never get out ──

# bootc's ostree block (checksums, deploy serials, the stateroot) must not get out. "ostree" itself is now a
# word the output may hold: the update tool of that name reports its version (os.tools.ostree).
OSTREE_CHECKSUMS = [BOOTC["status"][k]["ostree"]["checksum"] for k in ("booted", "rollback")]
SECRETS = ["registry.invalid", "example/os-image", "apikey-0123", "tok_live_abc", "192.0.2.7", "serial-SYNTH-0001",
           "hostname.corp", *OSTREE_CHECKSUMS]


def poisoned():
    b = json.loads(json.dumps(BOOTC))
    b["spec"]["image"]["image"] = "registry.invalid/example/os-image:tag"
    b["status"]["booted"]["image"]["image"]["image"] = "registry.invalid/example/os-image@sha256:" + "ab" * 32
    b["status"]["booted"]["image"]["apikey"] = "apikey-0123"
    b["status"]["booted"]["token"] = "tok_live_abc"
    b["status"]["booted"]["host"] = "192.0.2.7"
    e = json.loads(json.dumps(ESYNC))
    e["RHIVOS-APP-CANDUMP"]["apikey"] = "apikey-0123"
    e["RHIVOS-APP-CANDUMP"]["deviceId"] = "serial-SYNTH-0001"
    e["RHIVOS-APP-CANDUMP"]["server"] = "hostname.corp"
    e["SECRET-COMPONENT"] = {"version": "1", "provisionKey": "tok_live_abc"}
    return b, e


def test_nothing_in_the_inputs_but_the_named_fields_reaches_the_output():
    b, e = poisoned()
    text = json.dumps(build(bootc=b, esync=e))
    for secret in SECRETS:
        assert secret not in text, secret
    assert "provisionKey" not in text and "deviceId" not in text


def test_the_real_capture_alone_leaks_no_registry_checksum_or_server_field():
    text = json.dumps(build_full())
    for word in ("registry", "transport", "checksum", "stateroot", "deploySerial", "image\"", "spec", "bootOrder",
                 *OSTREE_CHECKSUMS):
        assert word not in text, word


def test_the_output_is_what_the_dashboards_reader_accepts_whole():
    """Nothing the exporter writes is dropped or blanked by the whitelist on the other side."""
    d = build_full()
    out = ota.clean(d)
    for key in ("os", "components", "services", "esync", "health", "board", "interval_s", "os_interval_s"):
        assert out[key] == d[key], key
    assert out["next_boot"]["version"] == "4.5.0" and out["next_boot"]["source"] == "rollback"
    assert d["esync"]["campaigns"] and d["esync"]["agents"] and d["os"]["build_records"]   # not vacuous
    assert d["os"]["distro"] and d["board"]["model"] and d["esync"]["os_package"]
    assert d["os"]["packages"]["compared"] and len(d["os"]["packages"]["shown"]) == len(SHOWN)


def test_the_output_is_strict_json():
    json.dumps(build(), allow_nan=False)


# ── it only reads ──

READ_ONLY = {
    ("bootc", "status", "--format", "json"),
    ("timedatectl", "show", "-p", "NTPSynchronized"),
    ("bootc", "-V"),
    ("ostree", "--version"),
    ("podman", "-v"),
}


def test_the_commands_it_may_run_are_reads_and_nothing_else():
    ran = []

    def fake(cmd, timeout=10):
        ran.append(tuple(cmd))
        return {"bootc": json.dumps(BOOTC), "timedatectl": HOST["ntp"]}.get(cmd[0], HOST["systemctl_show"]["esync"])

    io = fake_io(run=fake, read_file=lambda p: json.dumps(ESYNC))
    ex.collect(ex.parse_args(["--out", "/x/ota.json", "--service", "esync", "--service", "cangen",
                              "--show-rpm", "openssl"]), io)
    assert ran, "nothing was collected"
    for cmd in ran:
        assert (cmd in READ_ONLY or cmd in (RPM_NOW_CMD, RPM_BEFORE_CMD)
                or (cmd[0] == "systemctl" and cmd[1] == "show" and cmd[2] in ("esync", "cangen"))), cmd
    assert RPM_BEFORE_CMD in ran                                           # rpm only queries, here a database by path
    forbidden = {"rollback", "upgrade", "switch", "apply", "restart", "start", "stop", "kill", "reboot", "edit",
                 "enable", "disable", "mask", "set-property", "daemon-reload", "pull", "rm", "-e", "-i", "-U",
                 "--erase", "--install", "--upgrade", "--rebuilddb", "--initdb", "--import"}
    assert not forbidden & {part for cmd in ran for part in cmd}


def test_it_reads_the_named_files_and_no_other():
    """eSync's directories also hold its certificates, its device tree and audit records: only the component
    state, and the client log's tail, are opened. /proc is read only for the SDK's own processes."""
    seen = []

    def note(kind):
        return lambda path, *a: (seen.append((kind, path)), None)[1]

    io = fake_io(read_file=note("file"), read_state_dir=note("state dir"), processes=note("processes"),
                 log_tail=note("log"))
    ex.collect(ex.parse_args(["--out", "/x/ota.json", "--build-record", "/opt/kit/os-builds.txt"]), io)
    assert seen == [("file", "/var/lib/esync/state.json"), ("state dir", "/var/lib/esync/state.d"),
                    ("processes", "/root/esync/bin/"), ("log", "/root/esync/data/log/esyncclient.log"),
                    ("file", "/sys/fs/selinux/enforce"), ("file", "/opt/kit/os-builds.txt"),
                    ("file", "/etc/build-info"), ("file", "/etc/os-release"), ("file", "/usr/lib/os-release"),
                    ("file", "/proc/device-tree/model")]


@pytest.mark.parametrize("name", ["--help", "-p", "-x", "a b", "a;b", "a\nb", "$(id)", "a/b", "", "x" * 300, "../x"])
def test_a_service_name_that_could_be_an_option_or_a_path_is_refused(name):
    with pytest.raises(SystemExit):
        ex.parse_args(["--out", "/x/o.json", "--service", name])


@pytest.mark.parametrize("name", ["esync", "candump", "cangen", "vehicletap", "getty@tty1", "systemd-resolved.service", "a_b"])
def test_ordinary_unit_names_are_fine(name):
    assert ex.parse_args(["--out", "/x/o.json", "--service", name]).service == [name]


def test_collecting_survives_every_source_failing():
    def boom(*a, **k):
        raise OSError("nope")

    io = SimpleNamespace(run=lambda c, timeout=10: None, read_file=boom, statvfs=boom, kernel_release=boom,
                         read_state_dir=boom, processes=boom, log_tail=boom)
    src = ex.collect(ex.parse_args(["--out", "/x/o.json", "--service", "esync", "--agent", "/SDK/A",
                                    "--build-record", "/x/r.txt", "--show-rpm", "openssl"]), io)
    d = ex.build(src, now=NOW, mono=MONO, boot=BOOT)
    assert d["os"]["booted"] is None and d["components"] == [] and d["services"][0]["active_state"] == "unknown"
    assert d["os"]["packages"] is None
    assert d["os"]["build"] is None and d["os"]["tools"] == {"bootc": "", "ostree": "", "podman": ""}
    assert d["os"]["build_records"] == [] and d["os"]["as_of"] == ""
    assert d["esync"] == {"unit": "esync", "os_package": "", "processes": None, "outside_unit": None,
                          "agents": [{"topic": "/SDK/A", "running": None}], "client_log": "unavailable", "campaigns": []}
    assert d["os"]["distro"] == "" and d["board"] == {"model": ""}
    assert d["schema"] == 1 and ota.clean(d)["schema"] == 1


# ── the file ──

def test_the_file_is_written_whole_and_readable_by_the_container(tmp_path):
    path = tmp_path / "ota_status.json"
    doc = build()
    ex.write_atomic(str(path), doc)
    assert json.loads(path.read_text()) == doc
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644         # the container runs as another user
    assert sorted(os.listdir(tmp_path)) == ["ota_status.json"], "no temporary file left behind"
    assert path.read_text().endswith("\n")


def test_a_write_replaces_the_file_in_one_step_so_a_reader_never_sees_half_of_it(tmp_path, monkeypatch):
    path = tmp_path / "ota_status.json"
    path.write_text(json.dumps({"old": True}))
    replaced = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append((os.path.dirname(a), b)), real(a, b))[1])
    ex.write_atomic(str(path), build())
    assert replaced == [(str(tmp_path), str(path))], "the temporary file is in the same directory, so the rename is atomic"
    assert "old" not in json.loads(path.read_text())


def test_a_failed_write_leaves_the_old_file_and_no_litter(tmp_path, monkeypatch):
    path = tmp_path / "ota_status.json"
    path.write_text('{"keep": 1}')

    class Bad:
        pass

    with pytest.raises(TypeError):
        ex.write_atomic(str(path), {"x": Bad()})                # not serialisable
    assert json.loads(path.read_text()) == {"keep": 1}
    assert os.listdir(tmp_path) == ["ota_status.json"]


def test_a_directory_that_does_not_exist_is_an_error_not_created(tmp_path):
    with pytest.raises(OSError):
        ex.write_atomic(str(tmp_path / "nope" / "ota.json"), build())
    assert not (tmp_path / "nope").exists()


# ── the program ──

def fakes(monkeypatch):
    monkeypatch.setattr(ex, "run", lambda cmd, timeout=10: {"bootc": json.dumps(BOOTC), "timedatectl": HOST["ntp"]}.get(
        cmd[0], HOST["systemctl_show"]["esync"]))
    monkeypatch.setattr(ex, "read_file", lambda p: json.dumps(ESYNC) if "esync" in p else HOST["selinux_enforce"])
    monkeypatch.setattr(ex, "statvfs", lambda p: vfs())
    monkeypatch.setattr(ex, "kernel_release", lambda: HOST["kernel"])
    monkeypatch.setattr(ex, "read_state_dir", lambda p: {})
    monkeypatch.setattr(ex, "processes", lambda prefix: [])
    monkeypatch.setattr(ex, "log_tail", lambda p: None)


def test_one_shot_writes_the_file_and_exits_zero(tmp_path, monkeypatch):
    fakes(monkeypatch)
    out = tmp_path / "ota_status.json"
    assert ex.main(["--out", str(out), "--service", "esync"]) == 0
    d = json.loads(out.read_text())
    assert d["schema"] == 1 and d["os"]["booted"]["version"] == "4.4.0" and d["services"][0]["name"] == "esync"
    assert ota.read(str(out), now=ota.parse_time(d["generated_at"]))["status"] == "ok"


def test_one_shot_exits_non_zero_when_it_cannot_write(tmp_path, monkeypatch, capsys):
    fakes(monkeypatch)
    assert ex.main(["--out", str(tmp_path / "nope" / "ota.json")]) == 1
    assert "cannot write" in capsys.readouterr().err


def test_interval_mode_keeps_going_after_a_bad_pass(tmp_path, monkeypatch, capsys):
    fakes(monkeypatch)
    out = tmp_path / "ota_status.json"
    calls = {"n": 0}
    real_write = ex.write_atomic

    def flaky(path, doc):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk hiccup")
        return real_write(path, doc)

    clock = FakeClock()

    def nap(s):
        clock.sleep(s)
        if len(clock.naps) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(ex, "write_atomic", flaky)
    assert ex.main(["--out", str(out), "--interval", "30"], sleep=nap, clock=clock) == 0
    assert calls["n"] == 3 and clock.naps == [30, 30, 30]
    assert out.exists() and "disk hiccup" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "1.5", "99999999"])
def test_interval_must_be_a_sensible_number_of_seconds(bad):
    with pytest.raises(SystemExit):
        ex.parse_args(["--out", "/x/o.json", "--interval", bad])


def test_out_is_required():
    with pytest.raises(SystemExit):
        ex.parse_args([])


def test_the_script_is_standalone_it_imports_nothing_from_the_project():
    """It is copied to the board on its own; the project's modules are not there."""
    with open(os.path.join(ROOT, "tools", "ota_export.py")) as f:
        src = f.read()
    imported = {line.split()[1].split(".")[0] for line in src.splitlines() if line.startswith(("import ", "from "))}
    assert imported <= {"argparse", "functools", "json", "os", "re", "subprocess", "sys", "tempfile", "time", "collections",
                        "datetime", "__future__"}, imported
