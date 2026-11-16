"""The OTA status file: what the board is running, as the dashboard shows it (docs/OTA.md).

Written by a process outside the container (tools/ota_export.py, on the host), read here. That makes it
input from another trust domain, so nothing is passed through: `clean()` is a whitelist -- unknown keys
are dropped at every level, strings are cleaned and capped, digests and times are kept only in the one
form they have, numbers are range-checked. Nothing in this module raises on a bad file; the answer is a
status, and the page says what it means:

    waiting   no file yet (the exporter has not run, or the directory is not mounted)
    invalid   there is a file and it cannot be used (not JSON, wrong schema, no usable timestamp, too big)
    stale     it parsed, but it is old (or its clock is ahead of ours): shown, labelled with its age. Old
              means four missed writes at the interval the file declares, or 5 minutes if it declares none
    ok        fresh

Each read stands alone: an invalid file never shows the last good one.
"""

import copy
import json
import os
import re
import stat
import time
from datetime import datetime, timedelta, timezone
from math import isfinite

import datadir
from reader import clean_label
from util import env

SCHEMA = 1
FILE_MAX = 65_536          # bytes; a real file is ~1 KB. Larger is refused unparsed.
TEXT_MAX = 120             # one cleaned string
LIST_MAX = 32              # components, and services
COUNT_MAX = 1_000_000      # a restart counter, a process count
BYTES_MAX = 10 ** 15
CAMPAIGN_MAX = 10 ** 10    # a DMC campaign number
INTERVAL_MAX = 86400       # seconds; what the exporter accepts
STALE_AFTER_S = 300        # a file that declares no interval (a one-shot run, an older exporter)
STALE_MISSED = 4           # a file that declares its interval is stale after this many missed writes ...
STALE_MIN_S = 20           # ... and never sooner than this
SKEW_S = 60                # a timestamp this far ahead of our clock is clock skew, not "now"
TOOLS = ("bootc", "ostree", "podman")
CLIENT_LOG_STATES = ("ok", "unavailable")
PACKAGE_CHANGES = ("same", "updated", "downgraded", "added", "removed")
PACKAGES_MAX = 100_000     # packages in an OS image

ACTIVE_STATES = ("active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance")
SELINUX_MODES = ("enforcing", "permissive", "disabled")

_TIME = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,9})?"
                   r"(Z|[+-][0-9]{2}:[0-9]{2})")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_RESULT = re.compile(r"[A-Z][A-Z-]{0,39}")                  # eSync's word for a campaign's state: ROLLBACK-COMPLETED
_RPM_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+-]{0,99}")
# A package's VERSION-RELEASE ('3.5.9-1.el10'), with its epoch ('2:1.0-1') only where the two images' versions
# differ in nothing else; a package installed in several versions has them joined by ', '.
_RPM_VERSIONS = re.compile(r"(?:[0-9]{1,10}:)?[0-9A-Za-z][0-9A-Za-z.+~^_-]{0,79}"
                           r"(?:, (?:[0-9]{1,10}:)?[0-9A-Za-z][0-9A-Za-z.+~^_-]{0,79}){0,3}")


def path():
    """The status file: VEHICLETAP_OTA_FILE, else ota_status.json in the data folder."""
    return env("VEHICLETAP_OTA_FILE") or datadir.path("ota_status.json")


# ── the clock ────────────────────────────────────────────────────────────
# Parsed by hand: datetime.fromisoformat only learned 'Z' and nine-digit fractions in Python 3.11, and the
# project supports 3.10. bootc writes nanoseconds and systemd its own format; the exporter normalises to
# whole seconds, and anything else is not a time.

def parse_time(text):
    """Epoch seconds (int) of an ISO-8601 time with a zone, else None."""
    if not isinstance(text, str) or len(text) > 40:
        return None
    m = _TIME.fullmatch(text)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups()[:6])
    zone = m.group(7)
    try:
        if zone == "Z":
            tz = timezone.utc
        else:
            zh, zm = int(zone[1:3]), int(zone[4:6])
            if zh > 23 or zm > 59:
                return None
            tz = timezone(timedelta(hours=zh, minutes=zm) * (1 if zone[0] == "+" else -1))
        return int(datetime(y, mo, d, h, mi, s, tzinfo=tz).timestamp())
    except (ValueError, OverflowError):
        return None


def format_time(epoch):
    """'2026-08-07T23:03:53Z' for epoch seconds."""
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def restamp(raw, now):
    """A copy of a parsed file moved in time so that it was written at `now`, for a sample that is otherwise
    weeks old (demo mode). `generated_at` becomes `now`, and the times that say how recent an observation is
    (`os.as_of`, each campaign's `last_seen`) move with it, so their ages are kept. Build, install and start
    times are history and stay where they are."""
    out = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    then = parse_time(out.get("generated_at"))
    out["generated_at"] = format_time(now)
    if then is None:
        return out
    shift = int(now) - then

    def move(holder, key):
        t = parse_time(holder.get(key)) if isinstance(holder, dict) else None
        if t is not None:
            holder[key] = format_time(t + shift)

    move(out.get("os"), "as_of")
    esync = out.get("esync")
    campaigns = esync.get("campaigns") if isinstance(esync, dict) else None
    for campaign in campaigns if isinstance(campaigns, list) else []:
        move(campaign, "last_seen")
    return out


# ── the whitelist ────────────────────────────────────────────────────────

def _text(v):
    return clean_label(v, TEXT_MAX) if isinstance(v, str) else ""


def _when(v):
    """The string itself if it is a time (it matched the strict pattern, so it is safe), else ''."""
    return v if parse_time(v) is not None else ""


def _digest(v):
    return v if isinstance(v, str) and _DIGEST.fullmatch(v) else ""


def _int(v, top):
    return v if type(v) is int and 0 <= v <= top else None


def _pct(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) or not 0 <= v <= 100:
        return None
    return round(float(v), 1)


def _image(raw):
    """One OS image: version, digest, build time. No registry, no transport -- those name a server."""
    if not isinstance(raw, dict):
        return None
    img = {"version": _text(raw.get("version")), "digest": _digest(raw.get("digest")),
           "built_at": _when(raw.get("built_at"))}
    return img if img["version"] or img["digest"] else None


def _entries(raw, key, build):
    out = []
    for r in raw if isinstance(raw, list) else []:
        e = build(r) if isinstance(r, dict) else None
        if e and e[key]:
            out.append(e)
            if len(out) == LIST_MAX:
                break
    return out


def _component(r):
    return {"id": _text(r.get("id")), "version": _text(r.get("version")),
            "transaction_digest": _digest(r.get("transaction_digest")), "installed_at": _when(r.get("installed_at"))}


def _service(r):
    state = r.get("active_state")
    return {"name": _text(r.get("name")),
            "active_state": state if state in ACTIVE_STATES else "unknown",
            "sub_state": _text(r.get("sub_state")), "since": _when(r.get("since")),
            "restarts": _int(r.get("restarts"), COUNT_MAX)}


def _build(raw):
    """The image's build stamp: the version it was stamped with and when it was built."""
    if not isinstance(raw, dict):
        return None
    b = {"version": _text(raw.get("version")), "built_at": _when(raw.get("built_at"))}
    return b if b["version"] or b["built_at"] else None


def _tools(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {name: _text(raw.get(name)) for name in TOOLS}


def _record(r):
    return {k: _text(r.get(k)) for k in ("version", "kernel") + TOOLS}


def _rpm_version(v):
    return v if isinstance(v, str) and _RPM_VERSIONS.fullmatch(v) else ""


def _shown_rpm(r):
    name, change = r.get("name"), r.get("change")
    return {"name": name if isinstance(name, str) and _RPM_NAME.fullmatch(name) else "",
            "version": _rpm_version(r.get("version")), "before": _rpm_version(r.get("before")),
            "change": change if change in PACKAGE_CHANGES else ""}


def _packages(raw):
    """The OS image's packages: how many, how many changed against the rollback image (only when `compared`), and
    the packages the board was asked to show, each with its version now and in the rollback image."""
    if not isinstance(raw, dict):
        return None
    compared = raw.get("compared") is True
    count = (lambda key: _int(raw.get(key), PACKAGES_MAX)) if compared else (lambda key: None)
    return {"total": _int(raw.get("total"), PACKAGES_MAX), "compared": compared,
            "updated": count("updated"), "downgraded": count("downgraded"), "added": count("added"),
            "removed": count("removed"),
            "shown": _entries(raw.get("shown"), "name", _shown_rpm)}


def _os(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {"booted": _image(raw.get("booted")), "staged": _image(raw.get("staged")),
            "rollback": _image(raw.get("rollback")), "rollback_queued": raw.get("rollback_queued") is True,
            "kernel": _text(raw.get("kernel")), "booted_at": _when(raw.get("booted_at")),
            "as_of": _when(raw.get("as_of")), "build": _build(raw.get("build")), "tools": _tools(raw.get("tools")),
            "build_records": _entries(raw.get("build_records"), "version", _record), "distro": _text(raw.get("distro")),
            "packages": _packages(raw.get("packages"))}


def _agent(r):
    return {"topic": _text(r.get("topic")), "running": _int(r.get("running"), COUNT_MAX)}


def _campaign(r):
    result = r.get("result")
    return {"package": _text(r.get("package")), "version": _text(r.get("version")),
            "campaign": _int(r.get("campaign"), CAMPAIGN_MAX), "last_seen": _when(r.get("last_seen")),
            "rollback_version": _text(r.get("rollback_version")),
            "result": result if isinstance(result, str) and _RESULT.fullmatch(result) else ""}


def _esync(raw):
    """The eSync stack as the host saw it. `processes` and `outside_unit` are null when unknown."""
    raw = raw if isinstance(raw, dict) else {}
    log = raw.get("client_log")
    return {"unit": _text(raw.get("unit")), "os_package": _text(raw.get("os_package")),
            "processes": _int(raw.get("processes"), COUNT_MAX),
            "outside_unit": _int(raw.get("outside_unit"), COUNT_MAX),
            "agents": _entries(raw.get("agents"), "topic", _agent),
            "client_log": log if log in CLIENT_LOG_STATES else "unavailable",
            "campaigns": _entries(raw.get("campaigns"), "package", _campaign)}


def _board(raw):
    """The board's model, as its device tree names it. A model, not an identifier: no serial number."""
    raw = raw if isinstance(raw, dict) else {}
    return {"model": _text(raw.get("model"))}


def _interval(v):
    """A declared interval in whole seconds, 1 to a day, else None."""
    return v if type(v) is int and 1 <= v <= INTERVAL_MAX else None


def _health(raw):
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("selinux")
    return {"selinux": mode if mode in SELINUX_MODES else "unknown",
            "ntp_synced": raw.get("ntp_synced") if isinstance(raw.get("ntp_synced"), bool) else None,
            "var_used_pct": _pct(raw.get("var_used_pct")), "var_free_bytes": _int(raw.get("var_free_bytes"), BYTES_MAX)}


def next_boot(os_):
    """Which image the board starts next. `bootc rollback` queues the deployment under `rollback` for the next
    boot and discards a staged one (bootc 1.16.6, `bootc rollback --help`), so a queued rollback outranks
    a staged update; otherwise a staged update will boot; otherwise the board stays on what it runs."""
    if os_["rollback_queued"] and os_["rollback"]:
        source, img = "rollback", os_["rollback"]
    elif os_["staged"]:
        source, img = "staged", os_["staged"]
    elif os_["booted"]:
        source, img = "booted", os_["booted"]
    else:
        return None
    return {"source": source, "version": img["version"], "digest": img["digest"]}


def build_check(os_):
    """Is the running image the build its version names? Derived, not read:

    stamp   the version the image was stamped with at build time against the version bootc says is booted:
            match, mismatch, missing (no stamp), unknown (bootc names no version)
    record  the kernel and update tools running against what the build record says that version was built
            with: match, mismatch (with what differs), none (no record for that version), unknown (bootc names
            no version), off (no record file given to the exporter)

    A relabelled image (one build re-tagged with a new version) shows as a stamp mismatch; a different build
    published under a recorded version shows as a record mismatch."""
    booted = (os_["booted"] or {}).get("version", "")
    stamp_version = (os_["build"] or {}).get("version", "")
    if not booted:
        stamp = "unknown"
    elif not stamp_version:
        stamp = "missing"
    else:
        stamp = "match" if stamp_version == booted else "mismatch"
    differs = []
    if not os_["build_records"]:
        record = "off"
    elif not booted:
        record = "unknown"
    else:
        want = next((r for r in os_["build_records"] if r["version"] == booted), None)
        if want is None:
            record = "none"
        else:
            have = {"kernel": os_["kernel"], **os_["tools"]}
            differs = [{"name": k, "have": have[k], "want": want[k]} for k in ("kernel",) + TOOLS if have[k] != want[k]]
            record = "mismatch" if differs else "match"
    return {"stamp": stamp, "record": record, "differs": differs}


def stale_after(interval_s):
    """Seconds after which a file is stale: STALE_MISSED writes at its declared interval (STALE_MIN_S at
    least), else STALE_AFTER_S."""
    return max(STALE_MIN_S, STALE_MISSED * interval_s) if interval_s else STALE_AFTER_S


def clean(raw):
    """The whitelisted form of a parsed status file. ValueError (with a plain-English reason) if it is not
    a schema-1 object with a usable generated_at; everything else bad is dropped or blanked, not refused."""
    if not isinstance(raw, dict):
        raise ValueError("the status file is not a JSON object")
    if type(raw.get("schema")) is not int or raw["schema"] != SCHEMA:
        raise ValueError(f"unsupported schema {_text(raw.get('schema')) or 'none'}; this build reads schema {SCHEMA}")
    stamp = raw.get("generated_at")
    if parse_time(stamp) is None:
        raise ValueError("generated_at is missing or is not an ISO-8601 time with a zone")
    os_ = _os(raw.get("os"))
    return {"schema": SCHEMA, "generated_at": stamp,
            "interval_s": _interval(raw.get("interval_s")), "os_interval_s": _interval(raw.get("os_interval_s")),
            "os": os_,
            "components": _entries(raw.get("components"), "id", _component),
            "services": _entries(raw.get("services"), "name", _service),
            "esync": _esync(raw.get("esync")),
            "health": _health(raw.get("health")), "board": _board(raw.get("board")),
            "next_boot": next_boot(os_), "build_check": build_check(os_)}


# ── the answer ───────────────────────────────────────────────────────────

def _age_text(s):
    return f"{int(s)} s" if s < 120 else f"{int(s // 60)} min" if s < 7200 else f"{int(s // 3600)} h"


def view(raw, now=None):
    """What /api/ota serves for a parsed file: a status, how old the file is, and the cleaned data."""
    now = time.time() if now is None else now
    try:
        data = clean(raw)
    except ValueError as e:
        return {"status": "invalid", "age_s": None, "message": str(e)}
    age = now - parse_time(data["generated_at"])
    if age < -SKEW_S:
        msg = f"the exporter's clock is ahead of this machine's by {_age_text(-age)}"
        return {"status": "stale", "age_s": None, "message": msg, **data}
    if age > stale_after(data["interval_s"]):
        return {"status": "stale", "age_s": round(age, 1), "message": f"last written {_age_text(age)} ago", **data}
    return {"status": "ok", "age_s": round(max(age, 0), 1), "message": "", **data}


def read(file=None, now=None):
    """`view()` of the status file. Never raises."""
    file = file or path()
    try:
        if not stat.S_ISREG(os.stat(file).st_mode):
            return {"status": "invalid", "age_s": None, "message": "cannot read the status file: not a regular file"}
        with open(file, "rb") as f:
            blob = f.read(FILE_MAX + 1)
    except FileNotFoundError:
        return {"status": "waiting", "age_s": None,
                "message": "No status file yet. Run tools/ota_export.py on the host and give this app its "
                           "directory read-only (docs/OTA.md)."}
    except OSError as e:
        return {"status": "invalid", "age_s": None, "message": f"cannot read the status file: {e.strerror}"}
    if len(blob) > FILE_MAX:
        return {"status": "invalid", "age_s": None, "message": f"the status file is too large (over {FILE_MAX} bytes)"}
    try:
        raw = json.loads(blob.decode("utf-8"))
    except (ValueError, RecursionError):               # UnicodeDecodeError is a ValueError
        return {"status": "invalid", "age_s": None, "message": "the status file is not valid JSON"}
    return view(raw, now=now)
