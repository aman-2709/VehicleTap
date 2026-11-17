#!/usr/bin/env python3
"""
ota_export.py — write the board's OTA status file for the dashboard (docs/OTA.md).

Runs on the HOST, not in the container: the dashboard container is --read-only with no `bootc`, no
`systemctl` and no view of the host, so this reads those and writes one small JSON file that the container
only reads. Standalone on purpose (standard library only, imports nothing from the project) so it can be
copied to a board on its own.

What it reads, and nothing else:

  bootc status --format json            the booted / staged / rollback images, whether a rollback is queued
  /var/lib/esync/state.d/<package>.json the installed update components (eSync's per-package records), and
  /var/lib/esync/state.json             the older shared record, for a package that has no file of its own
  systemctl show <unit> -p ...          for each --service: state, sub-state, start time, restart count
  timedatectl show -p NTPSynchronized   whether the clock is synchronised
  /sys/fs/selinux/enforce, uname, statvfs("/var")
  /etc/build-info                       the version and build time the image was stamped with
  /etc/os-release (else /usr/lib/...)   the OS's name ('Automotive Stream Distribution 10')
  /proc/device-tree/model               the board's model, as its device tree names it; a PC has none
  bootc -V, ostree --version, podman -v the versions of the update tools
  --build-record FILE                   what each OS version was built with (optional)
  /proc/<pid>/cmdline, cgroup           to find the eSync SDK's processes (program under <esync-home>/bin/);
                                        only theirs are kept, and only their agent topic gets out
  <esync-home>/data/log/esyncclient.log the last 1 MiB: the campaigns the eSync client is working on, with each
                                        one's rollback version and result from its status line
  rpm -qa                               with --show-rpm: the OS image's packages (name, epoch:version-release), and
  rpm --dbpath <rollback image>/... -qa the rollback image's, from that deployment's own package database

It never changes anything: the commands it may run are the ones above and are fixed, a --service name
cannot be an option or a path, the rollback image's database path is built only from what bootc names it by,
each part in the one form it has, and nothing is written but the output file (a temporary file beside it,
renamed over it, so a reader never sees half of one).

The document is built from named fields. The `bootc` output also carries the image registry and transport
and ostree checksums, eSync's state and log carry more than versions (certificate paths, the device's ID,
server addresses), a process's command line carries key paths: none of that is read out of its input, so it
cannot be in the output. (The rollback deployment's stateroot, checksum and serial locate its package database;
they are not written.) The dashboard's reader whitelists again on the other side.

How often (docs/OTA.md "How often"): with --interval it rewrites the file every --interval seconds, on a
fixed rate. The cheap sources (files, /proc, systemd) are read on every pass; `bootc status`, the one
expensive call, every --os-interval seconds, and its last good answer is kept, with the time it was given,
when a call fails; the build stamp, the OS's name, the board's model, the kernel and the tool versions cannot
change while the board runs, so they are read once (again only while a tool has not answered). With --show-rpm,
the running image's package list is read once too, and the rollback image's whenever bootc names another
rollback deployment (each about half a second on an arm64 board).

Usage:
  python3 ota_export.py --out /var/lib/vehicletap-ota/ota_status.json --service esync --service candump
  python3 ota_export.py --out FILE --service esync --interval 5      # keep it fresh; Ctrl-C to stop
"""

import argparse
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import namedtuple
from datetime import datetime, timezone

SCHEMA = 1
ESYNC_STATE = "/var/lib/esync/state.json"
ESYNC_STATE_DIR = "/var/lib/esync/state.d"
ESYNC_HOME = "/root/esync"
ESYNC_UNIT = "esync"
CLIENT_LOG = "data/log/esyncclient.log"        # under --esync-home
AGENT_PROGRAM = "bin/workloadagent"            # under --esync-home
BUILD_INFO = "/etc/build-info"
OS_RELEASE = ("/etc/os-release", "/usr/lib/os-release")   # os-release(5): the first one that exists
BOARD_MODEL = "/proc/device-tree/model"
SELINUX_FLAG = "/sys/fs/selinux/enforce"
PROC = "/proc"
TEXT_MAX = 120
LIST_MAX = 32                                  # components, records, agents, campaigns: what the reader keeps
STATE_FILE_MAX = 65_536                        # one eSync state file; a real one is ~200 bytes
LOG_TAIL_MAX = 1 << 20                         # of the client log; it rotates at about 100 KB
INTERVAL_MAX = 86400
OS_INTERVAL = 15
ACTIVE_STATES = ("active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance")
TOOLS = ("bootc", "ostree", "podman")
TOOL_COMMANDS = {"bootc": ["bootc", "-V"], "ostree": ["ostree", "--version"], "podman": ["podman", "-v"]}
# The OS image's packages (--show-rpm). Another deployment's package database is in its own tree:
# <DEPLOY_ROOT>/<stateroot>/deploy/<checksum>.<serial>/<DEPLOY_RPMDB>, read-only like the running one's.
RPM_FORMAT = "%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\n"     # the epoch only orders versions; it is not shown
RPM_KEYS = ("gpg-pubkey",)                     # rpm lists the signing keys it trusts as packages; they are not
DEPLOY_ROOT = "/sysroot/ostree/deploy"
DEPLOY_RPMDB = "usr/lib/sysimage/rpm"
RPM_SHOWN_MAX = 24

Vfs = namedtuple("Vfs", "f_frsize f_blocks f_bfree f_bavail")

_TIME = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,9})?(Z|[+-][0-9]{2}:[0-9]{2})")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,99}")
_SUB = re.compile(r"[a-z][a-z-]{0,39}")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+~_-]{0,63}")
_TOPIC = re.compile(r"(?:/[A-Za-z0-9_]{1,40}){1,4}")
_BUILD_LINE = re.compile(r'([A-Z_]+)="([^"]*)"')
# os-release(5) is shell-compatible: a value is double-quoted, single-quoted or bare. A line that needs the shell
# (an escape, an expansion) is not read.
_OS_RELEASE_LINE = re.compile(r"""([A-Z0-9_]+)=(?:"([^"\\`$]*)"|'([^']*)'|([^\s"'\\`$]*))""")
_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_BUILD_TIME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}) ([0-9]{2}:[0-9]{2}:[0-9]{2}) UTC")
_OSTREE_VERSION = re.compile(r"^\s*Version:\s*'([^']*)'\s*$", re.M)
_RPM_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+-]{0,99}")             # 'openssl', 'libstdc++'
_RPM_EVR = re.compile(r"[0-9]{1,10}:[0-9A-Za-z][0-9A-Za-z.+~^_-]{0,79}")  # EPOCH:VERSION-RELEASE: '1:3.5.9-1.el10'
_STATEROOT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_CHECKSUM = re.compile(r"[0-9a-f]{64}")
# A client-log line about a campaign: "11-12:01:35:59.878 func:file.cpp:313 [main/1] <PACKAGE:VERSION#CAMPAIGN@RECORD> ..."
# (UTC, no year). Only the time and the token are read; the rest of the line is never looked at ...
_CAMPAIGN_HEAD = (rb"^([0-9]{2})-([0-9]{2}):([0-9]{2}):([0-9]{2}):([0-9]{2})\.[0-9]{1,6} [^<\n]{0,200}"
                  rb"<([A-Z0-9][A-Z0-9-]{0,63}):([0-9A-Za-z.+~_-]{0,64})#([0-9]{1,10})@[0-9]{1,10}>")
_CAMPAIGN = re.compile(_CAMPAIGN_HEAD, re.M)
# ... except the client's status line for the campaign, logged with it at every session: "<token> Package is of version
# 3.4.0, needs to be of version 3.7.0, registered rollback to 3.4.0 (forced:FALSE), pending update: FALSE, had QP: TRUE,
# TV: FALSE, considered already updated: ROLLBACK-COMPLETED (ROLLBACK-COMPLETED)". From it, the rollback version
# ('<null>' when the package registered none) and the result after "considered already updated:" are read.
_CAMPAIGN_STATUS = re.compile(_CAMPAIGN_HEAD + rb" Package is of version [^,\n]{0,64}, needs to be of version [^,\n]{0,64}, "
                              rb"registered rollback to ([0-9A-Za-z.+~_-]{1,64}|<null>) \([^)\n]{0,40}\)"
                              rb"[^\n]{0,200}?, considered already updated: ([A-Z][A-Z-]{0,39})", re.M)


def warn(msg):
    print(f"ota_export: {msg}", file=sys.stderr)


# ── small, pure helpers ──────────────────────────────────────────────────

def iso(epoch):
    """'2026-10-28T03:58:12Z' for epoch seconds."""
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_seconds(text):
    """An ISO-8601 time with a zone, cut to whole seconds ('...53.358191364Z' -> '...53Z'); '' if it is not one."""
    m = _TIME.fullmatch(text) if isinstance(text, str) else None
    if not m:
        return ""
    y, mo, d, h, mi, s = (int(g) for g in m.groups()[:6])
    zone = m.group(7)
    try:
        datetime(y, mo, d, h, mi, s)
        if zone != "Z" and (int(zone[1:3]) > 23 or int(zone[4:6]) > 59):
            return ""
    except ValueError:
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}{zone}"


def text(v):
    return " ".join(v.split())[:TEXT_MAX] if isinstance(v, str) else ""


def digest(v):
    return v if isinstance(v, str) and _DIGEST.fullmatch(v) else ""


def version(v):
    """A version or a kernel release as a program prints it ('1.17.1', '6.12.0-281.el10iv.aarch64'), else ''."""
    return v if isinstance(v, str) and _VERSION.fullmatch(v) else ""


def image(deployment):
    """{version, digest, built_at} of one bootc deployment, or None. Not the registry, not the transport."""
    img = deployment.get("image") if isinstance(deployment, dict) else None
    if not isinstance(img, dict):
        return None
    out = {"version": text(img.get("version")), "digest": digest(img.get("imageDigest")),
           "built_at": iso_seconds(img.get("timestamp"))}
    return out if out["version"] or out["digest"] else None


def os_section(bootc, kernel, now, boot):
    status = bootc.get("status") if isinstance(bootc, dict) else None
    status = status if isinstance(status, dict) else {}
    return {"booted": image(status.get("booted")), "staged": image(status.get("staged")),
            "rollback": image(status.get("rollback")), "rollback_queued": status.get("rollbackQueued") is True,
            "kernel": text(kernel), "booted_at": iso(now - boot)}


def rpm_list(raw):
    """{name: epoch:version} from `rpm -qa --qf RPM_FORMAT` ('openssl': '1:3.5.9-1.el10'), without the signing
    keys rpm lists as packages; None if `raw` is not text. A name installed in more than one version keeps them
    all, sorted and joined by ', '. A line that is not a name, a tab and an epoch:version is skipped."""
    if not isinstance(raw, str):
        return None
    found = {}
    for line in raw.splitlines():
        name, tab, ver = line.partition("\t")
        if tab and name not in RPM_KEYS and _RPM_NAME.fullmatch(name) and _RPM_EVR.fullmatch(ver):
            found.setdefault(name, set()).add(ver)
    return {name: ", ".join(sorted(vers)) for name, vers in found.items()}


_DIGITS = frozenset("0123456789")
_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def rpmvercmp(a, b):
    """1, 0 or -1 as version (or release) `a` is newer than, the same as or older than `b`, in rpm's own order
    (rpmvercmp in rpm's lib/rpmvercmp.c, ported as it is): digit runs compare as numbers and beat letter runs,
    other characters only separate, '~' sorts before anything (1.0~rc1 < 1.0) and '^' after the end
    (1.0 < 1.0^git1 < 1.0.1)."""
    if a == b:
        return 0
    i = j = 0
    while i < len(a) or j < len(b):
        while i < len(a) and a[i] not in _DIGITS and a[i] not in _LETTERS and a[i] not in "~^":
            i += 1
        while j < len(b) and b[j] not in _DIGITS and b[j] not in _LETTERS and b[j] not in "~^":
            j += 1
        x, y = a[i:i + 1], b[j:j + 1]                      # '' past the end
        if x == "~" or y == "~":
            if x != "~":
                return 1
            if y != "~":
                return -1
            i, j = i + 1, j + 1
            continue
        if x == "^" or y == "^":
            if not x:
                return -1
            if not y:
                return 1
            if x != "^":
                return 1
            if y != "^":
                return -1
            i, j = i + 1, j + 1
            continue
        if not (x and y):
            break
        kind = _DIGITS if x in _DIGITS else _LETTERS
        si, sj = i, j
        while i < len(a) and a[i] in kind:
            i += 1
        while j < len(b) and b[j] in kind:
            j += 1
        one, two = a[si:i], b[sj:j]
        if not two:                                        # a number against letters: the number is newer
            return 1 if kind is _DIGITS else -1
        if kind is _DIGITS:
            one, two = one.lstrip("0"), two.lstrip("0")
            if len(one) != len(two):
                return 1 if len(one) > len(two) else -1
        if one != two:
            return 1 if one > two else -1
    if i >= len(a) and j >= len(b):
        return 0
    return -1 if i >= len(a) else 1


def _evr(text):
    """(epoch, version, release) of 'E:V-R'; an epoch left out is 0 and a release left out is ''."""
    epoch, colon, vr = text.partition(":")
    if not colon:
        epoch, vr = "0", text
    version, dash, release = vr.rpartition("-")
    return (int(epoch), version, release) if dash else (int(epoch), vr, "")


def evr_compare(a, b):
    """1, 0 or -1 as 'E:V-R' `a` is newer than, the same as or older than `b`, as rpm orders packages: the epoch,
    then the version, then the release."""
    (ea, va, ra), (eb, vb, rb) = _evr(a), _evr(b)
    if ea != eb:
        return 1 if ea > eb else -1
    return rpmvercmp(va, vb) or rpmvercmp(ra, rb)


def _newest(versions):
    """The newest of a package's versions as rpm_list joins them."""
    return max(versions.split(", "), key=functools.cmp_to_key(evr_compare))


def _shown_versions(versions, with_epoch):
    """A package's versions as `rpm -q` writes them, without the epoch; with it (when not 0) for a package whose
    versions differ only in the epoch, so that the two do not read the same."""
    out = []
    for evr in versions.split(", ") if versions else []:
        epoch, _, vr = evr.partition(":")
        out.append(epoch + ":" + vr if with_epoch and epoch != "0" else vr)
    return ", ".join(out)


def deployment_rpmdb(deployment, root=DEPLOY_ROOT):
    """The package database of one bootc deployment, from the stateroot, checksum and serial bootc names it by;
    None unless each is in the one form it has, so nothing else can make the path."""
    tree = deployment.get("ostree") if isinstance(deployment, dict) else None
    if not isinstance(tree, dict):
        return None
    stateroot, checksum, serial = tree.get("stateroot"), tree.get("checksum"), tree.get("deploySerial")
    if not (isinstance(stateroot, str) and _STATEROOT.fullmatch(stateroot)):
        return None
    if not (isinstance(checksum, str) and _CHECKSUM.fullmatch(checksum)):
        return None
    if type(serial) is not int or not 0 <= serial <= 1_000_000:
        return None
    return f"{root}/{stateroot}/deploy/{checksum}.{serial}/{DEPLOY_RPMDB}"


def rollback_rpmdb(bootc):
    """The rollback image's package database, None if bootc names no rollback deployment."""
    status = bootc.get("status") if isinstance(bootc, dict) else None
    return deployment_rpmdb(status.get("rollback")) if isinstance(status, dict) else None


def package_change(now, before):
    """What happened to one package between the rollback image (`before`) and the running one (`now`); each is
    its versions as rpm_list gives them, '' where it is not installed. A version rpm orders after the one before,
    or the same but written otherwise ('1.01' and '1.1'), is "updated"; one rpm orders before it is "downgraded"
    (the demo's reset back to an older OS image). A package in several versions is compared by its newest."""
    if now and before:
        if now == before:
            return "same"
        return "downgraded" if evr_compare(_newest(now), _newest(before)) < 0 else "updated"
    if now:
        return "added"
    return "removed" if before else ""


def packages_section(now, before, shown):
    """The OS image's packages: how many, how many changed against the rollback image, and each package in `shown`
    with its version in both. None when the running image's list was not read. Without the rollback image's list
    (there is none, or it could not be read) `compared` is false, the counts are None and no change is named.
    Versions are written as `rpm -q` writes them, without the epoch, which only orders them."""
    if now is None:
        return None
    out = {"total": len(now), "compared": before is not None, "updated": None, "downgraded": None, "added": None,
           "removed": None, "shown": []}
    if before is not None:
        changes = [package_change(ver, before.get(name, "")) for name, ver in now.items()]
        for kind in ("updated", "downgraded", "added"):
            out[kind] = changes.count(kind)
        out["removed"] = sum(1 for name in before if name not in now)
    for name in shown:
        ver = now.get(name, "")
        was = before.get(name, "") if before is not None else ""
        epoch_only = bool(ver and was) and ver != was and _shown_versions(ver, False) == _shown_versions(was, False)
        out["shown"].append({"name": name, "version": _shown_versions(ver, epoch_only),
                             "before": _shown_versions(was, epoch_only),
                             "change": package_change(ver, was) if before is not None else ""})
    return out


def key_values(raw, line):
    """{KEY: value} of the lines of `raw` that `line` matches whole: group 1 is the key, the value is the first of
    the other groups that matched. {} if `raw` is not text."""
    values = {}
    for text_line in raw.splitlines() if isinstance(raw, str) else []:
        m = line.fullmatch(text_line.strip())
        if m:
            values[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    return values


def build_info(raw):
    """{version, built_at} from /etc/build-info (KEY="value" lines, as the image builder writes them), or None.
    Only IMAGE_VERSION and TIMESTAMP are read; the build's UUID and the rest stay where they are."""
    if not isinstance(raw, str):
        return None
    values = key_values(raw, _BUILD_LINE)
    stamp = _BUILD_TIME.fullmatch(values.get("TIMESTAMP", ""))
    out = {"version": text(values.get("IMAGE_VERSION")),
           "built_at": iso_seconds(f"{stamp.group(1)}T{stamp.group(2)}Z") if stamp else ""}
    return out if out["version"] or out["built_at"] else None


def distro(raw):
    """The OS's name from os-release: PRETTY_NAME, else NAME and VERSION_ID; '' if it names none. Only those three
    are read (the file also holds URLs and support contacts)."""
    v = key_values(raw, _OS_RELEASE_LINE)
    return text(v.get("PRETTY_NAME") or " ".join(x for x in (v.get("NAME"), v.get("VERSION_ID")) if x))


def board_model(raw):
    """The board's model from the device tree: the text before its closing NUL, if it is all printable; ''
    otherwise, and on a machine without a device tree."""
    name = raw.split("\0", 1)[0] if isinstance(raw, str) else ""
    return text(name) if name.isprintable() else ""


def tool_version(name, raw):
    """The version in what `bootc -V` ('bootc 1.17.1'), `ostree --version` (a YAML-ish block with
    "Version: '2026.4'") or `podman -v` ('podman version 6.1.0') printed; '' if it is not that."""
    if not isinstance(raw, str):
        return ""
    if name == "ostree":
        m = _OSTREE_VERSION.search(raw)
        return version(m.group(1)) if m else ""
    words = raw.split()
    lead = {"bootc": ["bootc"], "podman": ["podman", "version"]}[name]
    return version(words[len(lead)]) if words[:len(lead)] == lead and len(words) > len(lead) else ""


def tools(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {name: tool_version(name, raw.get(name)) for name in TOOLS}


def build_records(raw):
    """What each OS version was built with: lines of `version kernel bootc ostree podman` ('#' starts a
    comment). A line that is not five versions is skipped. [] when there is no record file."""
    out = []
    for line in raw.splitlines() if isinstance(raw, str) else []:
        fields = line.split("#", 1)[0].split()
        if len(fields) != 5 or not all(version(f) for f in fields):
            continue
        out.append(dict(zip(("version", "kernel") + TOOLS, fields)))
        if len(out) == LIST_MAX:
            break
    return out


def components(esync, esync_d=None):
    """The installed packages. A package's own file in state.d wins over its entry in the shared state.json,
    as the agent itself reads them (it migrates a package to state.d on its next write)."""
    merged = dict(esync) if isinstance(esync, dict) else {}
    if isinstance(esync_d, dict):
        merged.update(esync_d)
    out = []
    for name in sorted(merged):
        entry = merged[name]
        if not isinstance(name, str) or not name or not isinstance(entry, dict):
            continue
        out.append({"id": text(name), "version": text(entry.get("version")),
                    "transaction_digest": digest(entry.get("transactionDigest")),
                    "installed_at": iso_seconds(entry.get("installedAt"))})
    return out[:LIST_MAX]


def service(name, show, now, mono):
    """One unit from `systemctl show` text. `since` is on the wall clock: the unit's monotonic start time is
    moved by how far the monotonic clock is from now."""
    props = {}
    for line in (show or "").splitlines():
        key, eq, value = line.partition("=")
        if eq and key:
            props[key] = value
    if props.get("LoadState") == "not-found":
        state, sub = "inactive", "not-found"
    else:
        state = props.get("ActiveState")
        state = state if state in ACTIVE_STATES else "unknown"
        sub = props.get("SubState", "")
        sub = sub if _SUB.fullmatch(sub) else ""
    enter = props.get("ActiveEnterTimestampMonotonic", "")
    since = iso(now - mono + int(enter) / 1e6) if enter.isdigit() and int(enter) > 0 else ""
    n = props.get("NRestarts", "")
    return {"name": name, "active_state": state, "sub_state": sub, "since": since,
            "restarts": int(n) if n.isdigit() else None}


def agent_topic(argv):
    """The `-t` topic of a workload agent's command line ('/SDK/RHIVOS_APP'), or ''. The option may be its
    own argument followed by the value, or one argument with the value after it ('-t /SDK/X')."""
    for i, arg in enumerate(argv[1:], 1):
        if arg == "-t":
            value = argv[i + 1] if i + 1 < len(argv) else ""
        elif arg.startswith("-t"):
            value = arg[2:]
        else:
            continue
        value = value.strip()
        return value if _TOPIC.fullmatch(value) else ""
    return ""


def log_time(month, day, hour, minute, second, now):
    """Epoch seconds of a client-log time, which has no year: this year's, or last year's when this year's
    would be more than a day ahead of now (a December line read in January). None if it is not a date."""
    year = datetime.fromtimestamp(now, timezone.utc).year
    for y in (year, year - 1):
        try:
            t = datetime(y, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if t <= now + 86400:
            return t
    return None


def _campaign_key(m, now):
    """(package, version, campaign) of a campaign line and its time, or None if its time is not a date."""
    t = log_time(*(int(g) for g in m.groups()[:5]), now)
    return None if t is None else ((m.group(6).decode(), m.group(7).decode(), int(m.group(8))), t)


def campaigns(log, now):
    """The campaigns in the client log: package, target version, campaign number and when the client last
    logged it, most recent first. The client logs every campaign it is still working on at every session,
    so an old `last_seen` means it stopped logging it (the campaign ended, or the client is not running).
    From the campaign's latest status line: the version eSync rolls back to if it fails (`rollback_version`,
    '' when the package registered none) and eSync's result (`result`, e.g. ROLLBACK-COMPLETED); both '' when
    the tail holds no status line for it."""
    log = log if isinstance(log, bytes) else b""
    seen, status = {}, {}
    for m in _CAMPAIGN.finditer(log):
        found = _campaign_key(m, now)
        if found:
            key, t = found
            seen[key] = max(seen.get(key, t), t)
    for m in _CAMPAIGN_STATUS.finditer(log):
        found = _campaign_key(m, now)
        if found and (found[0] not in status or found[1] >= status[found[0]][0]):     # the latest line wins
            rollback = m.group(9).decode()
            status[found[0]] = (found[1], "" if rollback == "<null>" else rollback, m.group(10).decode())
    ordered = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for (p, v, c), t in ordered[:LIST_MAX]:
        _, rollback, result = status.get((p, v, c), (None, "", ""))
        out.append({"package": p, "version": v, "campaign": c, "last_seen": iso(t),
                    "rollback_version": rollback, "result": result})
    return out


def esync_section(procs, log, now, home=ESYNC_HOME, unit=ESYNC_UNIT, topics=(), os_package=""):
    """The eSync stack: how many of its processes run and how many of those are outside its unit's cgroup
    (a stack started by hand, which `systemctl stop` cannot reach), which workload agents run, and the
    campaigns in the client log. `procs` is [(argv, cgroup text)] of the SDK's processes, None if /proc
    could not be read; `log` the client log's tail, None if there is none. Every `topics` entry is listed,
    with 0 when no agent serves it. `os_package` is the package that updates the OS image, as configured
    ('' if not): the demo view shows that package's record and campaigns with the OS."""
    inside = re.compile(rf"^0::/system\.slice/{re.escape(unit)}\.service(/|$)", re.M)
    agent = os.path.join(home, AGENT_PROGRAM)
    count = {t: (None if procs is None else 0) for t in topics}
    total = outside = None
    if procs is not None:
        total, outside = len(procs), 0
        for argv, cgroup in procs:
            outside += 0 if inside.search(cgroup or "") else 1
            topic = agent_topic(argv) if argv and argv[0] == agent else ""
            if topic:
                count[topic] = count.get(topic, 0) + 1
    return {"unit": unit, "os_package": os_package, "processes": total, "outside_unit": outside,
            "agents": [{"topic": t, "running": n} for t, n in sorted(count.items())][:LIST_MAX],
            "client_log": "ok" if isinstance(log, bytes) else "unavailable",
            "campaigns": campaigns(log, now)}


def selinux_mode(flag):
    """`/sys/fs/selinux/enforce` holds 1 or 0; no such file means SELinux is not enabled."""
    if flag is None:
        return "disabled"
    return {"1": "enforcing", "0": "permissive"}.get(flag.strip(), "unknown")


def ntp_synced(show):
    value = (show or "").strip().partition("=")
    if value[0] != "NTPSynchronized":
        return None
    return {"yes": True, "no": False}.get(value[2])


def health(selinux, ntp, vfs):
    """The same percentage `df` prints: used / (used + available), so root-reserved blocks do not count."""
    used_pct = free = None
    if vfs is not None:
        used = (vfs.f_blocks - vfs.f_bfree) * vfs.f_frsize
        avail = vfs.f_bavail * vfs.f_frsize
        free = avail
        if used + avail > 0:
            used_pct = round(100.0 * used / (used + avail), 1)
    return {"selinux": selinux_mode(selinux), "ntp_synced": ntp_synced(ntp), "var_used_pct": used_pct,
            "var_free_bytes": free}


def build(src, now, mono, boot):
    """The status document from what was collected. `mono` and `boot` are the monotonic and boot-time clocks
    now; `now` is the wall clock. `bootc_at` is when the bootc answer in `src` was given (the collector keeps
    the last good one); without it, an answer is taken to be from now."""
    units = src.get("units") or {}
    os_ = os_section(src.get("bootc"), src.get("kernel"), now, boot)
    bootc_at = src.get("bootc_at", now if isinstance(src.get("bootc"), dict) else None)
    os_.update(as_of=iso(bootc_at) if bootc_at else "", build=build_info(src.get("build_info")),
               tools=tools(src.get("tools")), build_records=build_records(src.get("records")),
               distro=distro(src.get("os_release")),
               packages=packages_section(src.get("rpms"), src.get("rpms_before"), src.get("rpm_shown") or ()))
    cfg = src.get("esync_config") or {}
    return {"schema": SCHEMA, "generated_at": iso(now),
            "interval_s": src.get("interval"), "os_interval_s": src.get("os_interval"),
            "os": os_,
            "components": components(src.get("esync"), src.get("esync_d")),
            "services": [service(n, units.get(n), now, mono) for n in units],
            "esync": esync_section(src.get("processes"), src.get("client_log"), now, **cfg),
            "health": health(src.get("selinux"), src.get("ntp"), src.get("vfs")),
            "board": {"model": board_model(src.get("board_model"))}}


# ── the only things that touch the host ──────────────────────────────────

def run(cmd, timeout=10):
    """stdout of a read-only command, or None if it cannot be run or fails."""
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        warn(f"{cmd[0]}: {e}")
        return None
    if r.returncode != 0:
        warn(f"{cmd[0]} exited {r.returncode}")
        return None
    return r.stdout


def read_file(path):
    """The file's text, None if it does not exist."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return None


def read_state_dir(path):
    """{package: parsed record} of every <package>.json directly in eSync's state.d, {} if there is no such
    directory. Only regular files, not links; a file that is not JSON (the agent may be writing it) is
    skipped with a warning and read again on the next pass."""
    try:
        names = sorted(os.listdir(path))
    except FileNotFoundError:
        return {}
    out = {}
    for name in names:
        full = os.path.join(path, name)
        if name.startswith(".") or not name.endswith(".json") or os.path.islink(full) or not os.path.isfile(full):
            continue
        try:
            with open(full, "rb") as f:
                raw = f.read(STATE_FILE_MAX + 1)
            if len(raw) > STATE_FILE_MAX:
                raise ValueError("too large")
            out[name[:-len(".json")]] = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as e:                  # UnicodeDecodeError is a ValueError
            warn(f"{full}: not a readable JSON record ({e})")
            continue
        if len(out) == LIST_MAX:
            break
    return out


def processes(prefix, proc=PROC):
    """[(argv, cgroup text)] of every process whose program path starts with `prefix`. Every process's command
    line is read to find them and dropped unless it matches; only a match's cgroup is read. A process that exits
    while it is being read is skipped."""
    want = os.fsencode(prefix)
    out = []
    for pid in os.listdir(proc):
        if not pid.isdigit():
            continue
        try:
            with open(os.path.join(proc, pid, "cmdline"), "rb") as f:
                argv = f.read(65_536).split(b"\0")
            if not argv[0].startswith(want):              # kernel threads have no command line at all
                continue
            with open(os.path.join(proc, pid, "cgroup"), "rb") as f:
                cgroup = f.read(4096).decode("utf-8", "replace")
        except OSError:
            continue
        out.append(([a.decode("utf-8", "replace") for a in argv if a], cgroup))
    return out


def log_tail(path, limit=LOG_TAIL_MAX):
    """The last `limit` bytes of a log, None if there is no such file. The first line may be cut."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - limit))
            return f.read(limit)
    except FileNotFoundError:
        return None


def statvfs(path):
    v = os.statvfs(path)
    return Vfs(v.f_frsize, v.f_blocks, v.f_bfree, v.f_bavail)


def kernel_release():
    return os.uname().release


def _json(raw, what):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        warn(f"{what} is not JSON")
        return None


def _attempt(what, fn, default=None):
    try:
        return fn()
    except Exception as e:                     # a broken source must not stop the others
        warn(f"{what}: {e}")
        return default


def collect_fast(args, io):
    """The cheap sources, read on every pass: files, /proc and systemd."""
    units = {}
    for name in args.service:
        units[name] = io.run(["systemctl", "show", name, "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                              "-p", "ActiveEnterTimestampMonotonic", "-p", "NRestarts"])
    home = args.esync_home
    return {"esync": _attempt("esync state", lambda: _json(io.read_file(args.esync_state), "esync state")),
            "esync_d": _attempt("esync state.d", lambda: io.read_state_dir(args.esync_state_dir)),
            "units": units,
            "processes": _attempt("eSync processes", lambda: io.processes(os.path.join(home, "bin") + os.sep)),
            "client_log": _attempt("eSync client log", lambda: io.log_tail(os.path.join(home, CLIENT_LOG))),
            "esync_config": {"home": home, "unit": args.esync_unit, "topics": tuple(args.agent),
                             "os_package": args.os_package or ""},
            "selinux": _attempt("selinux", lambda: io.read_file(SELINUX_FLAG), default=""),
            "ntp": io.run(["timedatectl", "show", "-p", "NTPSynchronized"]),
            "vfs": _attempt("/var", lambda: io.statvfs("/var"))}


def first_file(paths, io):
    """The text of the first of `paths` that exists, None if none does."""
    for path in paths:
        found = io.read_file(path)
        if found is not None:
            return found
    return None


def collect_fixed(args, io):
    """What cannot change while the board runs: the image's build stamp, the OS's name, the board's model,
    the kernel and the update tools' versions, and the build record file."""
    records = None
    if args.build_record:
        records = _attempt("build record", lambda: io.read_file(args.build_record))
        if records is None:
            warn(f"no build record file {args.build_record}")
    return {"build_info": _attempt("build info", lambda: io.read_file(args.build_info)),
            "os_release": _attempt("os-release", lambda: first_file(OS_RELEASE, io)),
            "board_model": _attempt("board model", lambda: io.read_file(BOARD_MODEL)),
            "kernel": _attempt("kernel", io.kernel_release),
            "tools": {name: io.run(cmd) for name, cmd in TOOL_COMMANDS.items()},
            "records": records}


def collect_bootc(io):
    """`bootc status` as JSON, or None if it did not give a usable answer."""
    answer = _json(io.run(["bootc", "status", "--format", "json"]), "bootc status")
    return answer if isinstance(answer, dict) and isinstance(answer.get("status"), dict) else None


def collect_rpms(io, dbpath=None):
    """rpm's package list of the running image, or with `dbpath` of the deployment whose database that is; None
    if rpm did not answer."""
    cmd = ["rpm", *(["--dbpath", dbpath] if dbpath else []), "-qa", "--qf", RPM_FORMAT]
    return rpm_list(io.run(cmd))


def collect(args, io=None):
    """Everything the document is built from, all at once (a one-shot run). `io` supplies the host (this
    module by default; tests pass a fake). A source that cannot be read is None and the rest still comes."""
    io = io or sys.modules[__name__]
    src = {**collect_fast(args, io), **collect_fixed(args, io), "bootc": collect_bootc(io)}
    if args.show_rpm:
        db = rollback_rpmdb(src["bootc"])
        src.update(rpms=collect_rpms(io), rpms_before=collect_rpms(io, db) if db else None,
                   rpm_shown=tuple(args.show_rpm))
    return src


class Collector:
    """Collects for --interval mode, each source at its own pace (see the module docstring)."""

    def __init__(self, args, io=None):
        self.args = args
        self.io = io or sys.modules[__name__]
        self.fixed = None
        self.bootc = self.bootc_at = self.due = None
        self.rpms = self.rpms_before = self.rpms_before_db = None

    def collect(self, now, mono):
        src = collect_fast(self.args, self.io)
        if self.due is None or mono >= self.due:
            self.due = mono + self.args.os_interval
            answer = collect_bootc(self.io)
            if answer is not None:             # a failed call keeps the last good answer; bootc_at says how old
                self.bootc, self.bootc_at = answer, now
            if self.fixed is None or None in self.fixed["tools"].values():
                self.fixed = collect_fixed(self.args, self.io)
            if self.args.show_rpm:
                self.collect_packages()
        src.update(self.fixed, bootc=self.bootc, bootc_at=self.bootc_at,
                   interval=self.args.interval, os_interval=self.args.os_interval)
        if self.args.show_rpm:
            src.update(rpms=self.rpms, rpms_before=self.rpms_before, rpm_shown=tuple(self.args.show_rpm))
        return src

    def collect_packages(self):
        """The running image's package list once (again while rpm has not answered); the rollback image's when
        bootc names another rollback deployment, and again while it could not be read."""
        if self.rpms is None:
            self.rpms = collect_rpms(self.io)
        db = rollback_rpmdb(self.bootc)
        if db != self.rpms_before_db or (db and self.rpms_before is None):
            self.rpms_before = collect_rpms(self.io, db) if db else None
            self.rpms_before_db = db

    def document(self, wall=time.time, mono=time.monotonic, boot=lambda: time.clock_gettime(time.CLOCK_BOOTTIME)):
        src = self.collect(wall(), mono())
        return build(src, now=wall(), mono=mono(), boot=boot())


def write_atomic(path, doc):
    """Write `doc` to `path` so a reader sees the old file or the new one, never half. The temporary file is
    beside the target (same filesystem, so the rename is atomic) and the result is world-readable: the
    dashboard container runs as another user."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".ota_status.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=2, sort_keys=True, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── the program ──────────────────────────────────────────────────────────

def unit_name(value):
    """A systemd unit name. Not an option, not a path: it is passed to `systemctl show` as an argument."""
    if not _UNIT.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a plain unit name")
    return value


def topic(value):
    if not _TOPIC.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an agent topic like /SDK/RHIVOS_APP")
    return value


def package(value):
    if not _PACKAGE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an eSync package name like RHIVOS-OS-AUTOSD")
    return value


def rpm_name(value):
    """A package name. It is never passed to a command: the lists are read whole and the name looked up in them."""
    if not _RPM_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a package name like openssl")
    return value


def directory(value):
    if not os.path.isabs(value) or os.path.normpath(value) != value.rstrip("/") or value == "/":
        raise argparse.ArgumentTypeError(f"{value!r} is not an absolute directory path")
    return value.rstrip("/")


def seconds(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number of seconds") from None
    if not 1 <= n <= INTERVAL_MAX:
        raise argparse.ArgumentTypeError(f"interval must be 1 to {INTERVAL_MAX} seconds")
    return n


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Write the board's OTA status file for the VehicleTap dashboard (docs/OTA.md).")
    p.add_argument("--out", required=True, help="the status file to write (its directory must exist)")
    p.add_argument("--service", action="append", type=unit_name, metavar="UNIT",
                   help="a systemd unit to report on; repeat for several")
    p.add_argument("--esync-state", default=ESYNC_STATE, metavar="FILE",
                   help=f"eSync's shared component state (default {ESYNC_STATE})")
    p.add_argument("--esync-state-dir", default=ESYNC_STATE_DIR, metavar="DIR",
                   help=f"eSync's per-package component state, which wins over the shared file (default {ESYNC_STATE_DIR})")
    p.add_argument("--esync-home", type=directory, default=ESYNC_HOME, metavar="DIR",
                   help=f"where the eSync SDK is installed: its processes run from DIR/bin, its client logs to "
                        f"DIR/{CLIENT_LOG} (default {ESYNC_HOME})")
    p.add_argument("--esync-unit", type=unit_name, default=ESYNC_UNIT, metavar="UNIT",
                   help=f"the systemd unit eSync should run under (default {ESYNC_UNIT})")
    p.add_argument("--agent", action="append", type=topic, metavar="TOPIC",
                   help="a workload agent topic that should be running, e.g. /SDK/RHIVOS_APP; repeat for several")
    p.add_argument("--os-package", type=package, metavar="NAME",
                   help="the eSync package that updates the OS image, e.g. RHIVOS-OS-AUTOSD; the demo view shows "
                        "its record and campaigns with the OS, and every other package as an application")
    p.add_argument("--build-info", default=BUILD_INFO, metavar="FILE", help=f"the image's build stamp (default {BUILD_INFO})")
    p.add_argument("--build-record", metavar="FILE",
                   help="what each OS version was built with: lines of `version kernel bootc ostree podman`")
    p.add_argument("--show-rpm", action="append", type=rpm_name, metavar="NAME",
                   help="read the OS image's package list and the rollback image's, count what changed, and show "
                        f"NAME's version in both, e.g. openssl; repeat for several (at most {RPM_SHOWN_MAX})")
    p.add_argument("--interval", type=seconds, metavar="SECONDS", help="keep running, rewriting the file this often")
    p.add_argument("--os-interval", type=seconds, default=OS_INTERVAL, metavar="SECONDS",
                   help=f"with --interval: ask bootc this often (default {OS_INTERVAL})")
    args = p.parse_args(argv)
    args.service = args.service or []
    args.agent = args.agent or []
    args.show_rpm = list(dict.fromkeys(args.show_rpm or []))          # in the order given, each once
    if len(args.show_rpm) > RPM_SHOWN_MAX:
        p.error(f"--show-rpm: at most {RPM_SHOWN_MAX} packages")
    return args


def once(args):
    write_atomic(args.out, build(collect(args), now=time.time(), mono=time.monotonic(),
                                 boot=time.clock_gettime(time.CLOCK_BOOTTIME)))


def main(argv=None, sleep=time.sleep, clock=time.monotonic, collector=None):
    args = parse_args(argv)
    if not args.interval:
        try:
            once(args)
        except OSError as e:
            warn(f"cannot write {args.out}: {e}")
            return 1
        return 0
    collector = collector or Collector(args)
    due = clock()
    try:
        while True:
            try:
                write_atomic(args.out, collector.document())
            except Exception as e:             # keep the file fresh through a bad pass
                warn(f"cannot write {args.out}: {e}")
            due += args.interval               # a fixed rate: the time a pass takes is not added to the interval
            delay = due - clock()
            if delay < 0:                      # a pass took longer than the interval: carry on from now, no burst
                due, delay = clock(), 0
            sleep(delay)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
