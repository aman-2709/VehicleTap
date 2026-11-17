"""The OTA status file: what the dashboard reads from the host (docs/OTA.md).

The file is written by a process outside the container (tools/ota_export.py on the board), so it is
input from another trust domain: every key is whitelisted, every string cleaned and capped, every
number checked, and nothing here may raise. A missing file is "waiting", a broken one "invalid", an old
one "stale" -- never a 500.
"""
import copy
import json
import math

import pytest

from conftest import fixture

import ota  # noqa: E402  (web/ is on sys.path via conftest)

NOW = 1_790_000_000.0                      # an arbitrary "now" for the injected clock
GOOD = fixture("ota/status_v1.json")


def at(offset_s=0.0):
    """A generated_at string `offset_s` seconds before NOW (negative = in the future)."""
    t = ota.format_time(NOW - offset_s)
    assert ota.parse_time(t) == int(NOW - offset_s)
    return t


def good(**over):
    d = copy.deepcopy(GOOD)
    d["generated_at"] = at(5)
    d.update(over)
    return d


def write(tmp_path, obj, name="ota_status.json"):
    p = tmp_path / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return str(p)


# ── the clock: ISO-8601 in, epoch out, no dependency on Python 3.11's parser ──

@pytest.mark.parametrize("text,epoch", [
    ("2026-08-07T23:03:53Z", 1786143833),
    ("2026-08-07T23:03:53.358191364Z", 1786143833),        # bootc writes nanoseconds
    ("2026-08-07T16:03:53-07:00", 1786143833),
    ("2026-08-08T01:03:53+02:00", 1786143833),
])
def test_parse_time_accepts_iso_8601_with_a_zone(text, epoch):
    assert ota.parse_time(text) == epoch


@pytest.mark.parametrize("text", [
    "", None, 5, "yesterday", "2026-08-07", "2026-08-07 23:03:53Z", "2026-13-07T23:03:53Z",
    "2026-08-07T25:03:53Z", "2026-08-07T23:03:53", "2026-08-07T23:03:53+25:00", "2026-02-30T00:00:00Z",
    "Z" * 4000,
])
def test_parse_time_refuses_everything_else(text):
    assert ota.parse_time(text) is None


def test_format_time_round_trips():
    assert ota.format_time(1786143833) == "2026-08-07T23:03:53Z"
    assert ota.parse_time(ota.format_time(NOW)) == int(NOW)


# ── where the file is ──

def test_the_path_is_the_environment_variable_else_the_data_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("VEHICLETAP_OTA_FILE", raising=False)
    import datadir
    assert ota.path() == datadir.path("ota_status.json")
    monkeypatch.setenv("VEHICLETAP_OTA_FILE", str(tmp_path / "elsewhere.json"))
    assert ota.path() == str(tmp_path / "elsewhere.json")
    monkeypatch.setenv("VEHICLETAP_OTA_FILE", "")                 # empty is unset (util.env)
    assert ota.path() == datadir.path("ota_status.json")


# ── the three states that are not "ok" ──

def test_a_missing_file_is_waiting_and_says_how_to_fill_it(tmp_path):
    out = ota.read(str(tmp_path / "nope.json"), now=NOW)
    assert out["status"] == "waiting" and out["age_s"] is None
    assert "ota_export" in out["message"] and "docs/OTA.md" in out["message"]
    assert "os" not in out and "components" not in out


@pytest.mark.parametrize("content", ["", "{", '{"schema": 1,', "not json", "null", "[]", "42", '"str"', "\x00\x01"])
def test_a_file_that_is_not_a_json_object_is_invalid_not_an_error(tmp_path, content):
    out = ota.read(write(tmp_path, content), now=NOW)
    assert out["status"] == "invalid" and out["message"]
    assert "os" not in out


def test_deeply_nested_json_is_invalid_not_a_crash(tmp_path):
    """Well under the size cap, but deep enough to exhaust the parser's recursion limit."""
    nested = "[" * 30_000 + "]" * 30_000
    assert len(nested) < ota.FILE_MAX
    assert ota.read(write(tmp_path, nested), now=NOW)["status"] == "invalid"


def test_a_pipe_or_other_special_file_is_refused_without_blocking(tmp_path):
    import os
    fifo = str(tmp_path / "fifo")
    os.mkfifo(fifo)
    out = ota.read(fifo, now=NOW)                         # would hang forever if it were opened for reading
    assert out["status"] == "invalid" and "regular" in out["message"]


def test_a_path_that_cannot_be_read_is_invalid_not_an_error(tmp_path):
    d = tmp_path / "a_directory"
    d.mkdir()
    out = ota.read(str(d), now=NOW)
    assert out["status"] == "invalid" and "read" in out["message"]


def test_an_oversize_file_is_refused_without_parsing_it(tmp_path):
    pad = " " * (ota.FILE_MAX + 1)
    out = ota.read(write(tmp_path, json.dumps(good()) + pad), now=NOW)
    assert out["status"] == "invalid" and "large" in out["message"]
    just_under = json.dumps(good())
    assert len(just_under) < ota.FILE_MAX
    assert ota.read(write(tmp_path, just_under), now=NOW)["status"] == "ok"


@pytest.mark.parametrize("schema", [None, 0, 2, "1", 1.5, True, [1]])
def test_an_unknown_schema_is_invalid(tmp_path, schema):
    d = good()
    d["schema"] = schema
    out = ota.read(write(tmp_path, d), now=NOW)
    assert out["status"] == "invalid" and "schema" in out["message"]


def test_a_missing_schema_is_invalid(tmp_path):
    d = good()
    del d["schema"]
    assert ota.read(write(tmp_path, d), now=NOW)["status"] == "invalid"


@pytest.mark.parametrize("stamp", [None, "", "soon", 1790000000, "2026-08-07", "2026-13-01T00:00:00Z"])
def test_a_file_that_does_not_say_when_it_was_written_is_invalid(tmp_path, stamp):
    d = good()
    d["generated_at"] = stamp
    out = ota.read(write(tmp_path, d), now=NOW)
    assert out["status"] == "invalid" and "generated_at" in out["message"]


def test_a_fresh_file_is_ok_with_its_age(tmp_path):
    out = ota.read(write(tmp_path, good()), now=NOW)
    assert out["status"] == "ok" and out["age_s"] == 5
    assert out["schema"] == 1 and out["generated_at"] == at(5)


def test_an_old_file_is_stale_but_still_shown(tmp_path):
    """A file that declares no interval (a one-shot run, an older exporter) is stale after STALE_AFTER_S."""
    d = good(generated_at=at(ota.STALE_AFTER_S + 1), interval_s=None)
    out = ota.read(write(tmp_path, d), now=NOW)
    assert out["status"] == "stale" and out["age_s"] == ota.STALE_AFTER_S + 1
    assert out["os"]["booted"]["version"] == GOOD["os"]["booted"]["version"]    # the last known state, labelled
    assert "ago" in out["message"] or "old" in out["message"]
    on_the_line = good(generated_at=at(ota.STALE_AFTER_S), interval_s=None)
    assert ota.read(write(tmp_path, on_the_line, "b.json"), now=NOW)["status"] == "ok"


def test_a_timestamp_in_the_future_is_stale_not_trusted(tmp_path):
    out = ota.read(write(tmp_path, good(generated_at=at(-ota.SKEW_S - 5))), now=NOW)
    assert out["status"] == "stale" and "ahead" in out["message"]
    near = ota.read(write(tmp_path, good(generated_at=at(-ota.SKEW_S + 5)), "b.json"), now=NOW)
    assert near["status"] == "ok" and near["age_s"] == 0       # small skew: clamped, never negative


def test_each_read_stands_alone(tmp_path):
    p = write(tmp_path, good())
    assert ota.read(p, now=NOW)["status"] == "ok"
    write(tmp_path, "{")
    again = ota.read(p, now=NOW)
    assert again["status"] == "invalid" and "os" not in again, "an invalid file must not show the last good one"


# ── the whitelist: nothing the exporter did not mean to say gets through ──

POISON = ["ghcr.example/secret/image", "apikey-0123", "tok_live_abc", "192.0.2.7", "serial-SYNTH-0001", "hostname.corp"]


def poisoned():
    d = good()
    d["apikey"] = POISON[1]
    d["os"]["spec"] = {"image": POISON[0], "transport": "registry"}
    d["os"]["booted"]["image"] = POISON[0]
    d["os"]["booted"]["registry"] = POISON[0]
    d["os"]["rollback"]["transport"] = "registry"
    d["components"][0]["token"] = POISON[2]
    d["components"][0]["device_id"] = POISON[4]
    d["services"][0]["exec_start"] = f"/usr/bin/x --key={POISON[1]}"
    d["services"][0]["host"] = POISON[3]
    d["health"]["hostname"] = POISON[5]
    d["os"]["build"]["uuid"] = POISON[4]
    d["os"]["tools"]["path"] = POISON[0]
    d["os"]["build_records"][0]["comment"] = POISON[2]
    d["esync"]["home"] = POISON[5]
    d["esync"]["agents"][0]["argv"] = ["/x/workloadagent", "-k", POISON[1]]
    d["esync"]["campaigns"][0]["line"] = f"Setting {POISON[4]} value for node VIN"
    d["esync"]["campaigns"][0]["record"] = POISON[3]
    return d


def test_keys_that_are_not_in_the_schema_are_dropped_at_every_level(tmp_path):
    out = ota.read(write(tmp_path, poisoned()), now=NOW)
    assert out["status"] == "ok"
    blob = json.dumps(out)
    for secret in POISON:
        assert secret not in blob, secret
    assert set(out) == {"status", "age_s", "message", "schema", "generated_at", "interval_s", "os_interval_s", "os",
                        "components", "services", "esync", "health", "board", "next_boot", "build_check"}
    assert set(out["os"]["booted"]) == {"version", "digest", "built_at"}
    assert set(out["components"][0]) == {"id", "version", "transaction_digest", "installed_at"}
    assert set(out["services"][0]) == {"name", "active_state", "sub_state", "since", "restarts"}
    assert set(out["health"]) == {"selinux", "ntp_synced", "var_used_pct", "var_free_bytes"}
    assert set(out["os"]) == {"booted", "staged", "rollback", "rollback_queued", "kernel", "booted_at", "as_of", "build",
                              "tools", "build_records", "distro", "packages"}
    assert set(out["os"]["build"]) == {"version", "built_at"}
    assert set(out["os"]["tools"]) == {"bootc", "ostree", "podman"}
    assert set(out["os"]["build_records"][0]) == {"version", "kernel", "bootc", "ostree", "podman"}
    assert set(out["esync"]) == {"unit", "os_package", "processes", "outside_unit", "agents", "client_log", "campaigns"}
    assert set(out["board"]) == {"model"}
    assert set(out["esync"]["agents"][0]) == {"topic", "running"}
    assert set(out["esync"]["campaigns"][0]) == {"package", "version", "campaign", "last_seen", "rollback_version", "result"}


@pytest.mark.parametrize("result,want", [("ROLLBACK-COMPLETED", "ROLLBACK-COMPLETED"), ("UPDATE-COMPLETED", "UPDATE-COMPLETED"),
                                         ("SOME-NEW-STATE", "SOME-NEW-STATE"), ("rollback", ""), ("A B", ""), ("X" * 41, ""),
                                         ("<b>x</b>", ""), ("", ""), (None, ""), (7, ""), (["X"], "")])
def test_a_campaign_result_is_esyncs_upper_case_word_or_nothing(result, want):
    d = good()
    d["esync"]["campaigns"][0]["result"] = result
    d["esync"]["campaigns"][0]["rollback_version"] = "4.4.0\n<x>"
    out = ota.clean(d)["esync"]["campaigns"][0]
    assert out["result"] == want
    assert "\n" not in out["rollback_version"]


def test_the_os_name_the_board_model_and_the_os_package_are_kept_as_cleaned_text():
    d = good()
    d["os"]["distro"] = "Automotive Stream Distribution 10"
    d["board"] = {"model": "Texas Instruments J784S4 EVM", "serial": "SYNTH-0001"}
    d["esync"]["os_package"] = "RHIVOS-OS-AUTOSD"
    out = ota.clean(d)
    assert out["os"]["distro"] == "Automotive Stream Distribution 10"
    assert out["board"] == {"model": "Texas Instruments J784S4 EVM"}            # the model, never a serial number
    assert out["esync"]["os_package"] == "RHIVOS-OS-AUTOSD"
    d["os"]["distro"], d["board"]["model"], d["esync"]["os_package"] = "A\x00B\nC" + "x" * 500, 7, ["RHIVOS"]
    out = ota.clean(d)
    assert "\x00" not in out["os"]["distro"] and "\n" not in out["os"]["distro"] and len(out["os"]["distro"]) <= ota.TEXT_MAX
    assert out["board"]["model"] == "" and out["esync"]["os_package"] == ""


def packages(**over):
    p = {"total": 255, "compared": True, "updated": 119, "downgraded": 0, "added": 1, "removed": 0,
         "shown": [{"name": "openssl", "version": "3.5.9-1.el10", "before": "3.5.7-2.el10", "change": "updated"},
                   {"name": "zstd", "version": "1.5.5-9.el10", "before": "", "change": "added"}]}
    p.update(over)
    return p


def test_the_os_packages_are_kept_with_their_counts_and_each_shown_package():
    d = good()
    d["os"]["packages"] = packages()
    d["os"]["packages"]["list"] = ["openssl-3.5.9"]                      # not in the schema
    d["os"]["packages"]["shown"][0]["arch"] = "aarch64"
    out = ota.clean(d)["os"]["packages"]
    assert out == packages()
    assert ota.clean(good())["os"]["packages"] is None                   # an exporter run without --show-rpm


def test_without_a_comparison_the_package_counts_are_dropped():
    d = good()
    d["os"]["packages"] = packages(compared=False)
    out = ota.clean(d)["os"]["packages"]
    assert (out["compared"], out["updated"], out["downgraded"], out["added"], out["removed"], out["total"]) == (
        False, None, None, None, None, 255)
    d["os"]["packages"] = packages(compared="yes")                        # only a real true compares
    assert ota.clean(d)["os"]["packages"]["compared"] is False


@pytest.mark.parametrize("field,value,want", [
    ("version", "3.5.9-1.el10", "3.5.9-1.el10"), ("version", "6.12.0-251.el10iv, 6.12.0-281.el10iv", None),
    ("version", "3.5.9 <b>", ""), ("version", "-rf", ""), ("version", "x" * 81, ""), ("version", 359, ""),
    ("before", "a\nb", ""), ("name", "libstdc++", "libstdc++"), ("name", "../etc", ""), ("name", "-x", ""),
    ("name", "a b", ""), ("change", "updated", "updated"), ("change", "UPDATED", ""), ("change", "gone", ""),
    ("change", None, ""), ("change", "downgraded", "downgraded"),
    ("before", "2:1.0-1", "2:1.0-1"), ("before", "1:1.0-1, 2:1.0-1", None),     # an epoch-only change keeps the epoch
    ("before", "12345678901:1.0-1", ""), ("before", ":1.0-1", ""), ("before", "1:", "")])
def test_a_shown_package_keeps_each_field_only_in_the_form_it_has(field, value, want):
    d = good()
    d["os"]["packages"] = packages()
    d["os"]["packages"]["shown"][0][field] = value
    shown = ota.clean(d)["os"]["packages"]["shown"]
    if field == "name" and want == "":
        assert [s["name"] for s in shown] == ["zstd"]                     # a package with no usable name is dropped
    else:
        assert shown[0][field] == (value if want is None else want)


@pytest.mark.parametrize("bad", [-1, 100_001, 1.5, True, "119", None])
def test_a_package_count_out_of_range_is_none(bad):
    d = good()
    d["os"]["packages"] = packages(updated=bad, total=bad, downgraded=bad)
    out = ota.clean(d)["os"]["packages"]
    assert out["updated"] is None and out["total"] is None and out["downgraded"] is None and out["added"] == 1


def test_a_file_from_an_exporter_that_did_not_count_downgrades_has_none():
    """An exporter before 2026-11-14 wrote no `downgraded`: the page then shows the other counts as before."""
    d = good()
    p = packages()
    del p["downgraded"]
    d["os"]["packages"] = p
    out = ota.clean(d)["os"]["packages"]
    assert out["downgraded"] is None and (out["updated"], out["added"], out["removed"]) == (119, 1, 0)


def test_a_string_in_an_image_field_is_cleaned_not_trusted():
    d = good()
    d["os"]["booted"]["version"] = "4.4.0\n<script>alert(1)</script>​‮"
    out = ota.clean(d)
    v = out["os"]["booted"]["version"]
    assert "\n" not in v and "​" not in v and "‮" not in v
    assert len(v) <= ota.TEXT_MAX
    # markup is left alone here (this is data); the page renders with textContent -- tests/test_html_escape.py


def test_strings_are_capped():
    d = good()
    d["os"]["kernel"] = "k" * 5000
    d["components"][0]["id"] = "c" * 5000
    out = ota.clean(d)
    assert len(out["os"]["kernel"]) <= ota.TEXT_MAX and len(out["components"][0]["id"]) <= ota.TEXT_MAX


@pytest.mark.parametrize("digest,kept", [
    ("sha256:" + "ab" * 32, True),
    ("sha256:" + "AB" * 32, False),                       # the form tools print is lower case
    ("sha256:" + "ab" * 31, False), ("sha256:" + "zz" * 32, False), ("md5:abc", False),
    ("ab" * 32, False), ("", False), (None, False), (12, False), ("sha256:" + "ab" * 32 + "\n", False),
])
def test_a_digest_is_kept_only_in_the_one_form_a_digest_has(digest, kept):
    d = good()
    d["os"]["booted"]["digest"] = digest
    out = ota.clean(d)["os"]["booted"]["digest"]
    assert (out == digest) if kept else (out == "")


def test_times_are_kept_only_when_they_are_times():
    d = good()
    d["os"]["booted_at"] = "not a time"
    d["os"]["booted"]["built_at"] = "2026-08-07T23:03:53.358191364Z"
    d["components"][0]["installed_at"] = "2026-13-45T00:00:00Z"
    d["services"][0]["since"] = "Tue 2026-10-27 23:47:46 UTC"       # systemd's own format is not ours
    out = ota.clean(d)
    assert out["os"]["booted_at"] == ""
    assert out["os"]["booted"]["built_at"] == "2026-08-07T23:03:53.358191364Z"
    assert out["components"][0]["installed_at"] == ""
    assert out["services"][0]["since"] == ""


def test_an_image_with_neither_version_nor_digest_is_nothing():
    d = good()
    d["os"]["staged"] = {"built_at": at(1)}
    d["os"]["rollback"] = {}
    d["os"]["booted"] = "4.4.0"
    out = ota.clean(d)["os"]
    assert out["staged"] is None and out["rollback"] is None and out["booted"] is None


def test_enumerations_are_checked():
    d = good()
    d["services"][0]["active_state"] = "<b>active</b>"
    d["services"][1]["active_state"] = "failed"
    d["health"]["selinux"] = "Enforcing"                  # case matters: the exporter lower-cases it
    out = ota.clean(d)
    assert out["services"][0]["active_state"] == "unknown" and out["services"][1]["active_state"] == "failed"
    assert out["health"]["selinux"] == "unknown"
    d["health"]["selinux"] = "permissive"
    assert ota.clean(d)["health"]["selinux"] == "permissive"


@pytest.mark.parametrize("bad", [-1, 1.5, "3", None, True, float("nan"), 10 ** 9, [1]])
def test_a_restart_count_is_a_small_non_negative_integer_or_nothing(bad):
    d = good()
    d["services"][0]["restarts"] = bad
    assert ota.clean(d)["services"][0]["restarts"] is None


def test_a_restart_count_that_is_good_is_kept():
    d = good()
    d["services"][0]["restarts"] = 3
    assert ota.clean(d)["services"][0]["restarts"] == 3


@pytest.mark.parametrize("bad", [-0.1, 100.1, float("nan"), float("inf"), "25", None, True, [25]])
def test_a_percentage_outside_zero_to_a_hundred_is_nothing(bad):
    d = good()
    d["health"]["var_used_pct"] = bad
    assert ota.clean(d)["health"]["var_used_pct"] is None


def test_health_numbers_that_are_good_are_kept():
    d = good()
    d["health"].update(var_used_pct=25.4, var_free_bytes=22_000_000_000, ntp_synced=True)
    h = ota.clean(d)["health"]
    assert h["var_used_pct"] == 25.4 and h["var_free_bytes"] == 22_000_000_000 and h["ntp_synced"] is True
    d["health"].update(var_free_bytes=-5, ntp_synced="yes")
    h = ota.clean(d)["health"]
    assert h["var_free_bytes"] is None and h["ntp_synced"] is None, "only a real boolean counts"


def test_a_queued_rollback_needs_a_real_true():
    for v, want in [(True, True), (False, False), ("true", False), (1, False), (None, False)]:
        d = good()
        d["os"]["rollback_queued"] = v
        assert ota.clean(d)["os"]["rollback_queued"] is want, v


def test_lists_are_capped_and_junk_entries_skipped():
    d = good()
    d["components"] = [{"id": f"c{i}", "version": "1"} for i in range(ota.LIST_MAX + 40)] + [None, 5, "x", [], {}]
    d["services"] = [{"name": f"s{i}"} for i in range(ota.LIST_MAX + 40)] + [{"name": ""}, {"active_state": "active"}]
    d["os"]["build_records"] = [{"version": f"{i}.0"} for i in range(ota.LIST_MAX + 40)] + [{"kernel": "k"}]
    d["esync"]["agents"] = [{"topic": f"/SDK/A{i}"} for i in range(ota.LIST_MAX + 40)] + [{"running": 1}]
    d["esync"]["campaigns"] = [{"package": f"P{i}"} for i in range(ota.LIST_MAX + 40)] + [{"campaign": 1}]
    out = ota.clean(d)
    assert len(out["components"]) == ota.LIST_MAX and len(out["services"]) == ota.LIST_MAX
    for lst in (out["os"]["build_records"], out["esync"]["agents"], out["esync"]["campaigns"]):
        assert len(lst) == ota.LIST_MAX
    assert all(c["id"] for c in out["components"]) and all(s["name"] for s in out["services"])
    d["components"] = "not a list"
    d["services"] = {"name": "esync"}
    out = ota.clean(d)
    assert out["components"] == [] and out["services"] == []


def test_sections_that_are_missing_or_the_wrong_type_come_out_empty_not_broken():
    for section in ("os", "components", "services", "esync", "health", "board"):
        for junk in (None, 5, "x", [], [1, 2]):
            d = good()
            d[section] = junk
            out = ota.clean(d)
            assert set(out) >= {"os", "components", "services", "esync", "health", "board", "build_check"}
    minimal = ota.clean({"schema": 1, "generated_at": at(1)})
    assert minimal["os"]["booted"] is None and minimal["components"] == [] and minimal["services"] == []
    assert minimal["health"]["selinux"] == "unknown" and minimal["health"]["ntp_synced"] is None
    assert minimal["os"]["build"] is None and minimal["os"]["tools"] == {"bootc": "", "ostree": "", "podman": ""}
    assert minimal["os"]["as_of"] == "" and minimal["os"]["build_records"] == []
    assert minimal["esync"] == {"unit": "", "os_package": "", "processes": None, "outside_unit": None, "agents": [],
                                "client_log": "unavailable", "campaigns": []}
    assert minimal["os"]["distro"] == "" and minimal["board"] == {"model": ""}
    assert minimal["interval_s"] is None and minimal["os_interval_s"] is None
    assert minimal["build_check"] == {"stamp": "unknown", "record": "off", "differs": []}


@pytest.mark.parametrize("junk", [None, 0, 1, -1, 1.5, float("nan"), float("inf"), "", "x", "x" * 10_000, True, [], {}, [None],
                                  {"a": {"b": [1, {"c": None}]}}, "\x00", "�"])
def test_junk_in_every_field_never_raises(junk):
    def fuzz(node):
        if isinstance(node, dict):
            return {k: fuzz(v) for k, v in node.items()}
        if isinstance(node, list):
            return [fuzz(v) for v in node]
        return junk
    d = fuzz(good())
    d["schema"], d["generated_at"] = 1, at(1)
    out = ota.view(d, now=NOW)
    assert out["status"] in ("ok", "stale", "invalid")
    json.dumps(out, allow_nan=False)                      # whatever comes out is valid JSON, no NaN


def test_the_payload_is_strict_json(tmp_path):
    out = ota.read(write(tmp_path, good()), now=NOW)
    assert json.loads(json.dumps(out, allow_nan=False)) == out
    assert not any(isinstance(v, float) and not math.isfinite(v) for v in out.values())


# ── the one derived field: what the board will boot next ──

def nb(**os_over):
    d = good()
    d["os"].update(os_over)
    return ota.clean(d)["next_boot"]


def test_next_boot_is_the_booted_image_when_nothing_is_queued():
    n = nb(staged=None, rollback_queued=False)
    assert n["source"] == "booted" and n["version"] == GOOD["os"]["booted"]["version"]


def test_next_boot_is_the_staged_image_when_an_update_is_waiting():
    n = nb(staged={"version": "9.9.9", "digest": "sha256:" + "cd" * 32}, rollback_queued=False)
    assert n["source"] == "staged" and n["version"] == "9.9.9" and n["digest"] == "sha256:" + "cd" * 32


def test_next_boot_is_the_rollback_image_when_a_rollback_is_queued():
    """`bootc rollback` queues the deployment under `rollback` for the next boot (bootc 1.16.6 help text)."""
    n = nb(staged=None, rollback_queued=True)
    assert n["source"] == "rollback" and n["version"] == GOOD["os"]["rollback"]["version"]


def test_a_queued_rollback_discards_a_staged_update_so_staged_does_not_win_over_it():
    """bootc's own rule: queuing a rollback discards a staged entry. If a file claims both, trust the queue."""
    n = nb(staged={"version": "9.9.9"}, rollback_queued=True)
    assert n["source"] == "rollback"


def test_next_boot_is_nothing_when_no_image_is_known():
    d = good()
    d["os"] = {}
    assert ota.clean(d)["next_boot"] is None
    d["os"] = {"rollback_queued": True}                   # queued, but there is no rollback image to name
    assert ota.clean(d)["next_boot"] is None


def test_a_queued_rollback_with_no_rollback_image_falls_back_to_booted():
    n = nb(rollback=None, staged=None, rollback_queued=True)
    assert n["source"] == "booted"


# ── the eSync section and the declared intervals ──

def test_the_esync_section_is_kept_whole_when_it_is_good():
    out = ota.clean(good())
    assert out["esync"] == GOOD["esync"]


@pytest.mark.parametrize("field,bad", [("processes", -1), ("processes", 1.5), ("processes", "4"), ("processes", True),
                                       ("outside_unit", ota.COUNT_MAX + 1), ("outside_unit", None)])
def test_an_esync_count_is_a_small_non_negative_integer_or_nothing(field, bad):
    d = good()
    d["esync"][field] = bad
    assert ota.clean(d)["esync"][field] is None


@pytest.mark.parametrize("bad", ["fine", "", None, 1, "OK"])
def test_the_client_log_state_is_one_of_two_words(bad):
    d = good()
    d["esync"]["client_log"] = bad
    assert ota.clean(d)["esync"]["client_log"] == "unavailable"


@pytest.mark.parametrize("bad", [-1, ota.CAMPAIGN_MAX + 1, "3904", 3.5, None, True])
def test_a_campaign_number_is_a_whole_number_or_nothing(bad):
    d = good()
    d["esync"]["campaigns"][0]["campaign"] = bad
    c = ota.clean(d)["esync"]["campaigns"][0]
    assert c["campaign"] is None and c["package"] == GOOD["esync"]["campaigns"][0]["package"]


def test_a_campaign_last_seen_that_is_not_a_time_is_blanked():
    d = good()
    d["esync"]["campaigns"][0]["last_seen"] = "11-12:01:35:59"
    assert ota.clean(d)["esync"]["campaigns"][0]["last_seen"] == ""


@pytest.mark.parametrize("value,kept", [(5, 5), (1, 1), (86400, 86400), (0, None), (-5, None), (86401, None),
                                        (5.0, None), ("5", None), (True, None), (None, None)])
def test_a_declared_interval_is_whole_seconds_from_one_to_a_day(value, kept):
    out = ota.clean(good(interval_s=value, os_interval_s=value))
    assert out["interval_s"] == kept and out["os_interval_s"] == kept


@pytest.mark.parametrize("interval,fresh_at,stale_at", [(5, 19, 21), (2, 19, 21), (30, 119, 121), (None, 299, 301)])
def test_a_file_is_stale_after_four_missed_writes_or_five_minutes_if_it_declares_no_interval(tmp_path, interval,
                                                                                             fresh_at, stale_at):
    p = write(tmp_path, good(generated_at=at(fresh_at), interval_s=interval))
    assert ota.read(p, now=NOW)["status"] == "ok"
    p = write(tmp_path, good(generated_at=at(stale_at), interval_s=interval))
    out = ota.read(p, now=NOW)
    assert out["status"] == "stale" and out["os"]["booted"], "stale still shows the data"


def test_os_as_of_is_kept_only_when_it_is_a_time():
    d = good()
    d["os"]["as_of"] = "a minute ago"
    assert ota.clean(d)["os"]["as_of"] == ""
    assert ota.clean(good())["os"]["as_of"] == GOOD["os"]["as_of"]


def test_restamp_moves_the_file_to_now_and_keeps_the_age_of_each_observation():
    d = good()                                           # written 5 s before NOW
    later = NOW + 86400
    out = ota.restamp(d, later)
    assert out["generated_at"] == ota.format_time(later)
    for before, after in ((d["os"]["as_of"], out["os"]["as_of"]),
                          (d["esync"]["campaigns"][0]["last_seen"], out["esync"]["campaigns"][0]["last_seen"])):
        assert ota.parse_time(after) - ota.parse_time(before) == 86400 + 5
    assert out["os"]["booted"] == d["os"]["booted"] and out["components"] == d["components"]   # history stays
    assert out["services"] == d["services"] and out["os"]["booted_at"] == d["os"]["booted_at"]
    assert d["generated_at"] == at(5), "the original is not changed"


@pytest.mark.parametrize("raw", [{}, {"generated_at": "junk"}, None, {"generated_at": at(1), "os": 5, "esync": []},
                                 {"generated_at": at(1), "esync": {"campaigns": [None, 5, {"last_seen": "x"}]}},
                                 {"generated_at": at(1), "esync": {"campaigns": 5}, "os": {"as_of": 7}}])
def test_restamp_survives_a_file_with_nothing_to_move(raw):
    out = ota.restamp(raw, NOW)
    assert out["generated_at"] == ota.format_time(NOW)


# ── the derived build check: is the running image the build its version names? ──

def check(**os_over):
    d = good()
    d["os"].update(os_over)
    return ota.clean(d)["build_check"]


def test_the_fixture_is_the_build_its_version_names():
    assert check() == {"stamp": "match", "record": "match", "differs": []}


def test_a_relabelled_image_is_a_stamp_mismatch():
    """4.4.0 was 4.3.0 re-tagged: its build stamp still says 4.3.0."""
    assert check(build={"version": "4.3.0", "built_at": "2026-08-01T20:17:50Z"})["stamp"] == "mismatch"


def test_no_build_stamp_is_missing_and_no_booted_version_is_unknown():
    assert check(build=None)["stamp"] == "missing"
    assert check(build={"version": "", "built_at": "2026-08-01T20:17:50Z"})["stamp"] == "missing"
    c = check(booted={"version": "", "digest": "sha256:" + "ab" * 32})
    assert c["stamp"] == "unknown" and c["record"] == "unknown"


def test_tools_that_differ_from_the_record_are_named_with_both_versions():
    c = check(tools={"bootc": "1.17.1", "ostree": GOOD["os"]["tools"]["ostree"], "podman": ""},
              kernel="6.12.0-281.el10iv.aarch64")
    want = {r["version"]: r for r in GOOD["os"]["build_records"]}[GOOD["os"]["booted"]["version"]]
    assert want["bootc"] != "1.17.1" and want["kernel"] != "6.12.0-281.el10iv.aarch64" and want["podman"]
    assert c["record"] == "mismatch"
    assert c["differs"] == [{"name": "kernel", "have": "6.12.0-281.el10iv.aarch64", "want": want["kernel"]},
                            {"name": "bootc", "have": "1.17.1", "want": want["bootc"]},
                            {"name": "podman", "have": "", "want": want["podman"]}]


def test_a_version_with_no_record_is_none_and_no_record_file_is_off():
    assert check(build_records=[{"version": "1.0.0", "kernel": "k", "bootc": "b", "ostree": "o", "podman": "p"}])["record"] == "none"
    assert check(build_records=[])["record"] == "off"
    assert check(build_records="junk")["record"] == "off"


def test_the_build_check_is_derived_never_read_from_the_file():
    d = good()
    d["build_check"] = {"stamp": "match", "record": "match", "differs": []}
    d["os"]["build"] = {"version": "0.0.1"}
    assert ota.clean(d)["build_check"]["stamp"] == "mismatch"


# ── the file the repo ships for demos and docs ──

def test_the_demo_file_is_a_valid_current_schema_file():
    import os
    from conftest import ROOT
    with open(os.path.join(ROOT, "docs", "demo", "ota.json")) as f:
        raw = json.load(f)
    raw["generated_at"] = at(1)
    out = ota.view(raw, now=NOW)
    assert out["status"] == "ok" and out["os"]["booted"]["version"]
    assert out["components"] and out["services"]
    assert out["esync"]["campaigns"] and out["esync"]["agents"] and out["os"]["build"]
    assert out["build_check"]["stamp"] in ("match", "mismatch") and out["build_check"]["record"] != "off"


def test_the_fixture_is_clean_as_it_stands():
    """Nothing in the shipped sample is dropped: the whitelist and the sample agree."""
    cleaned = ota.clean(GOOD)
    for k in ("booted", "rollback"):
        assert cleaned["os"][k] == GOOD["os"][k]
    assert cleaned["os"]["staged"] is None
    assert cleaned["components"] == GOOD["components"]
    assert cleaned["services"] == GOOD["services"]
    assert cleaned["health"] == GOOD["health"]
    assert cleaned["esync"] == GOOD["esync"]
    for k in ("as_of", "build", "tools", "build_records"):
        assert cleaned["os"][k] == GOOD["os"][k], k
    assert (cleaned["interval_s"], cleaned["os_interval_s"]) == (GOOD["interval_s"], GOOD["os_interval_s"])
    assert {k: cleaned["os"][k] for k in ("rollback_queued", "kernel", "booted_at")} == \
        {k: GOOD["os"][k] for k in ("rollback_queued", "kernel", "booted_at")}
