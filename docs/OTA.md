# OTA status: the file the dashboard reads

The board VehicleTap runs on can tell you which operating-system image it booted, what is waiting behind it, which
update components are installed and whether the services around them are up. The dashboard container cannot see
any of that itself: it runs `--read-only --cap-drop all`, with no `bootc`, no `systemctl` and no view of the host's
`/etc/os-release` or SELinux. So a process **on the host** writes one small JSON file, and the app only reads it.
Nothing in the app can change the board's state, and nothing here sends anything to a car.

This page is the contract for that file. It covers the file, the reader (`web/ota.py`), the endpoint, the page and
the host-side exporter (`tools/ota_export.py`).

## The endpoint and the page

| | |
|---|---|
| `GET /api/ota` | `ota.read()` as JSON. **Always 200**: a missing, broken or old file is a `status` in the body, not an error. The path is never taken from the request. |
| `GET /ota` | the page. It renders no board data itself; `web/static/ota.js` asks `/api/ota` every 5 s and writes the answer with `textContent`. |
| `GET /ota/demo` | the demo view: the same answer laid out for an audience (below). `web/static/ota_demo.js`, the same rules. |
| Other methods | 405. Nothing in the app can change the board. |
| `--demo` | serves `docs/demo/ota.json`, re-stamped so it looks current, with `demo: true`; the real file is never opened. |

The page shows the booted, staged and rollback images side by side with the rows that differ highlighted, the update
components, the services and the health facts. When a reboot would start an image other than the one running, a
one-sentence warning sits above the images, for example *A reboot would start 4.5.0, not the running 4.4.0 (queued
rollback).* It says nothing when nothing is queued or staged, and names an image by its digest if it has no version.
Under the images the OS card says whether the running image is the build its version names (the **build stamp** and
the **build record**, below) and which versions of `bootc`, `ostree` and `podman` are running. When bootc has not
answered for two of its intervals, a line above the images says how old the image data is; the last good answer stays
on screen. The **eSync** card shows how many of the SDK's processes run and whether any run outside eSync's unit (a
stack started by hand), each workload agent the board should have, and the campaigns the eSync client is working on,
from its log: package, target version, campaign number and when the client last logged it, with a row it has not
logged for two minutes in grey.
A banner says what `waiting`, `invalid` and `stale` mean; `stale` still shows the last state, labelled with its age.
The page is about the board, so it does not carry the vehicle's name. The dashboard header has an **OTA** link to it.

### The demo view

`/ota/demo` shows the same `/api/ota` answer for an audience on a shared screen: fewer details, larger type, and
**only values the answer carries**: a field it does not carry is left out, never filled in. The two pages link to
each other.

* **Header:** the OS's name and the board's model (`os.distro`, `board.model`), and a badge: *Live · N s ago* while
  the file is fresh, *Stale · last data N min ago* (amber, the cards dimmed) when it is stale, *No data* otherwise,
  with the reason in a banner.
* **OS OTA Status:** the OS package (`--os-package`), the running version in large type with its digest, then a box
  each for the kernel (across the card, so its version never wraps) and the `bootc`,
  `ostree` and `podman` versions, highlighted, and each of these in its own box:
  * *Update*: a queued rollback (*Rollback queued: a reboot starts 4.5.0*), else a staged update, else the OS
    package's campaign if the client logged it in the last two minutes, else *No active campaign*, with the last
    campaign and its age under it. A campaign needs `--os-package` and the client's log (`client_log: ok`); without
    them the box shows only what bootc says, or is left out, and never claims there is no campaign.
  * *Deployed version*, *Rollback version* and *Result* of the OS package's last campaign, when the client logged a
    status line for it: the version it deployed, the version eSync rolls back to if that fails (*none registered*
    when the package named none), and eSync's result (*Updated*, *Rolled back*, *In progress*, or its own word for
    anything else), with when it was last seen once the client stopped logging it.
  * *Build check*: the `build_check` above, worst finding first (*as built for 4.6.0*).
  * *eSync record*: eSync's recorded version of the OS package against the running version, amber when they differ
    (*4.7.0 (the board runs 4.6.0)*). That is what the record shows after the platform rolled an update back and the
    agent has not caught up; the box says no more than that, and it is left out when either version is missing.
* **APP OTA Status:** every other package eSync has a record or a campaign for, with its installed version and the
  same *Update*, *Deployed version*, *Rollback version* and *Result* boxes, then a box for every service the exporter reports except eSync's own unit, with its state. On
  the J7 that is `candump` and `cangen`, which ship in the same signed transaction, so a broken `candump` update shows
  red while `cangen` stays green.
* **OS Packages**, across the page under the two cards, when the exporter read the image's packages (`--show-rpm`).
  It starts folded to one line: the counts against the rollback image (*119 updated · 1 added · 0 removed*, then
  *since 4.3.0 (the rollback image) · 255 packages in 4.6.0*; *No package changed* when that is so) and a *Show
  packages* button. Opened (by a click or the keyboard; a native `<details>`), it shows a box for each `--show-rpm`
  package with its version: highlighted with *was 3.5.7-2* under it when the update changed it, *new since
  4.3.0* when it added it, dim and *unchanged* when it did not. The page never opens or closes it, so it stays as the
  presenter left it through every refresh. Without a rollback image, or when its packages could not be read, the line
  is the count and says why there is no comparison. After an update back to an older image (the demo's reset) the
  line names the downgrades too (*0 updated · 119 downgraded · 0 added · 1 removed*) and each box says *downgraded
  from* the newer version; with none, the line is as above. On the J7 this is what an OS update changed beyond the
  kernel and the update tools: OpenSSL, OpenSSH, the crypto policy, the CA certificates, the SELinux policy, glibc,
  systemd and the boot chain.
* **Versions** on this page are the upstream version and the build number: the build's distribution tag (`.el10`,
  `.el10iv`, `.el10_0`, wherever it sits in the release) and the architecture in the kernel's own name are left out, so
  the kernel *6.12.0-281.el10iv.aarch64* shows as *6.12.0-281* and openssl *3.5.9-1.el10* as *3.5.9-1*. Every package
  in an image carries one of a few tags, so they do not say what an update changed. Anything else stays, a kernel
  variant too (*6.12.0-281+debug*), and an update that changed only the tag (the same version rebuilt under another
  one) keeps the tags in its box. Hovering over the kernel box or a package box shows the strings as `uname -r` and
  `rpm` write them; `/api/ota` and `/ota` keep them whole.
* **eSync**, across the page under the two cards: a box for each workload agent (`--agent`) with whether it runs, else
  one box for how many of eSync's processes run. The agents are not put on the OTA cards: nothing in the file says
  which package an agent serves.

## Where the file is

| | |
|---|---|
| Default | `ota_status.json` in the data folder ([DATA.md](DATA.md)) |
| Override | `VEHICLETAP_OTA_FILE=/path/to/ota_status.json` |
| Size limit | 64 KB, refused unparsed beyond that (a real file is about 1 KB) |

Dropping a sample file into the data folder is the whole setup on a laptop. `docs/demo/ota.json` is a complete one.

## Schema 1

Times are ISO-8601 UTC with a zone (`2026-10-28T03:30:00Z`). Digests are `sha256:` and 64 lower-case hex digits.
A field that does not have its form is blanked (`""` or `null`), never passed through.

```
{ "schema": 1,
  "generated_at": "2026-10-28T03:30:00Z",
  "interval_s": 5, "os_interval_s": 15,
  "os": { "booted":   {"version": "4.4.0", "digest": "sha256:…", "built_at": "…Z"},
          "staged":   null,
          "rollback": {"version": "4.5.0", "digest": "sha256:…", "built_at": "…Z"},
          "rollback_queued": true,
          "kernel": "6.12.0-251.el10iv.aarch64",
          "booted_at": "…Z",
          "as_of": "…Z",
          "build": {"version": "4.4.0", "built_at": "…Z"},
          "tools": {"bootc": "1.16.6", "ostree": "2026.1", "podman": "6.0.2"},
          "build_records": [ {"version": "4.4.0", "kernel": "…", "bootc": "…", "ostree": "…", "podman": "…"} ],
          "distro": "Automotive Stream Distribution 10",
          "packages": { "total": 255, "compared": true, "updated": 119, "downgraded": 0, "added": 1, "removed": 0,
                        "shown": [ {"name": "openssl", "version": "3.5.9-1.el10", "before": "3.5.7-2.el10",
                                    "change": "updated"} ] } },
  "components": [ {"id": "…", "version": "…", "transaction_digest": "sha256:…", "installed_at": "…Z"} ],
  "services":   [ {"name": "esync", "active_state": "active", "sub_state": "running", "since": "…Z", "restarts": 0} ],
  "esync":      { "unit": "esync", "os_package": "RHIVOS-OS-AUTOSD", "processes": 7, "outside_unit": 0,
                  "agents": [ {"topic": "/SDK/RHIVOS_APP", "running": 1} ],
                  "client_log": "ok",
                  "campaigns": [ {"package": "RHIVOS-OS-AUTOSD", "version": "4.5.0", "campaign": 1003, "last_seen": "…Z",
                                  "rollback_version": "4.4.0", "result": "NOT-COMPLETED"} ] },
  "health":     { "selinux": "enforcing", "ntp_synced": true, "var_used_pct": 25.0, "var_free_bytes": 22000000000 },
  "board":      { "model": "Texas Instruments J784S4 EVM" } }
```

* `generated_at` and `schema` are required; everything else may be absent. A missing section comes out empty. The
  fields added on 2026-11-12 (`interval_s`, `os_interval_s`, `os.as_of`, `os.build`, `os.tools`, `os.build_records`,
  `esync`, then `os.distro`, `board` and `esync.os_package` for the demo view) are additions to schema 1: a reader
  from before them drops them, and this reader shows a file without them as it did before (no build check, an eSync
  card that says it knows nothing, a demo view with no OS name and every package as an application). `os.packages`
  (2026-11-13) is one more: `null` without `--show-rpm`, and the demo view then has no OS Packages card; its
  `downgraded` count and change (2026-11-14) are `null` and absent in a file from an exporter before them.
* `interval_s` and `os_interval_s` are what the exporter was run with (`null` for a one-shot run); `os.as_of` is when
  bootc gave the image data, which the exporter keeps when a later call fails.
* `os.build` is the image's build stamp (`/etc/build-info`: `IMAGE_VERSION` and `TIMESTAMP`); `os.build_records` is the
  record file the exporter was given (what each OS version was built with), `[]` when it was given none.
* `esync.processes` and `outside_unit` are `null` when `/proc` could not be read, and `running` is `null` then too.
  `client_log` is `ok` or `unavailable`. Each `agents` entry is a topic the board should serve (`--agent`) or one an
  agent was found serving. `os_package` is the package that updates the OS image (`--os-package`), `""` if not given.
* A campaign's `version` is the version it deploys. `rollback_version` is the version eSync rolls back to if that
  fails (`""` when the package registered none) and `result` is eSync's word for the campaign's state
  (`UPDATE-COMPLETED`, `ROLLBACK-COMPLETED`, `NOT-COMPLETED`; the reader keeps any upper-case word), both from the
  client's latest status line for it, and `""` when the log's tail holds none.
* `os.distro` is the OS's name from os-release; `board.model` is the board's model from its device tree (a product
  name, not a serial number), `""` on a machine without one.
* `os.packages` is the OS image's packages (`--show-rpm`): `total` in the running image (rpm's signing keys, which it
  lists as `gpg-pubkey` packages, not counted), and against the rollback image how many were `updated`,
  `downgraded`, `added` and `removed`. `updated` and `downgraded` are rpm's own order (epoch, then version, then
  release, as `rpmvercmp` compares them): a running version rpm puts before the rollback image's is a downgrade, as
  after the demo's reset to an older image; one rpm orders the same but writes otherwise counts as updated, and a
  package in several versions is compared by its newest. `compared` is false when there is no rollback image or its
  packages could not be read; the four counts are then `null`. Each `shown` entry is a `--show-rpm` package: its
  `version` (VERSION-RELEASE as `rpm -q` writes it, `""` if not installed), its version in the rollback image
  (`before`), and `change`: `updated`, `downgraded`, `added`, `removed`, `same`, or `""` without a comparison. The
  epoch only orders versions and is not written, except as `2:1.0-1` when the two versions differ in nothing else. A
  package installed in several versions has them joined by `, `.
* An image is `null` unless it has a version or a digest. It carries **no registry name and no transport**: those
  name a server.
* `active_state` is one of `active inactive failed activating deactivating reloading maintenance`, else `unknown`.
  `selinux` is `enforcing`, `permissive` or `disabled`, else `unknown`.
* At most 32 components, services, build records, agents and campaigns are kept. Strings are cleaned (control, zero-width and bidirectional
  characters removed, whitespace collapsed) and cut to 120 characters.
* Unknown keys are dropped at every level.
* `built_at` is the image's creation time as bootc reports it (`image.timestamp`), not when it was installed or
  booted, and the page labels the row **Image created**.
* **`components` is eSync's own record, and it can disagree with `os`.** The J7 test board's OS component had no
  `version` (the page shows "not reported"), a `transaction_digest` that matches neither bootc image, and an install
  date of 31 July against images from August. `/ota` does not compare the two: an empty version has nothing to
  compare, and an eSync transaction digest is not known to be an image digest, so a mismatch flag would be a guess.
  The card says so; bootc, not eSync, says which image is running. The demo view compares the **versions** only, when
  `--os-package` names the OS package and both sides have one, and never the digests.

## What the reader answers

`ota.read()` returns a status, the file's age and the cleaned data. It never raises.

| Status | When | Data shown |
|---|---|---|
| `waiting` | no file yet | none; the message says how to fill it |
| `invalid` | not JSON, not an object, too big, an unsupported `schema`, or no usable `generated_at` | none |
| `stale` | older than four of its writes (`interval_s` × 4, at least 20 s; 5 minutes when it declares no interval), or its timestamp is more than a minute ahead of this machine's clock | the last written state, labelled with its age |
| `ok` | otherwise | all of it |

Each read stands alone: a broken file never shows the previous good one.

### Which image boots next

`next_boot` is derived, not read: `{"source": "rollback"|"staged"|"booted", "version", "digest"}`.

1. If `rollback_queued` is true and there is a rollback image, that image. `bootc rollback` "queues the deployment
   under `rollback` for the next boot" and discards a staged update (bootc 1.16.6, `bootc rollback --help`), so a
   queued rollback outranks a staged one.
2. Otherwise a staged update, which boots next.
3. Otherwise the image that is running.

On the J7 test board the file would read `booted 4.4.0`, `rollback 4.5.0`, `rollback_queued: true`: **a reboot
there starts 4.5.0.**

### Is the running image the build its version names?

`build_check` is derived too: `{"stamp", "record", "differs"}`.

| | Compares | Answers |
|---|---|---|
| `stamp` | the version the image was stamped with when it was built (`os.build.version`) with the version bootc says is booted | `match`, `mismatch`, `missing` (no stamp), `unknown` (bootc names no version) |
| `record` | the running kernel and `bootc`, `ostree`, `podman` with what the build record says the booted version was built with | `match`, `mismatch` (and `differs`: each `{name, have, want}`), `none` (no record for that version), `unknown`, `off` (no record file) |

An image that is one build re-labelled with a new version shows as a stamp mismatch (its stamp keeps the old version);
a different build published under a recorded version shows as a record mismatch. Neither is a signature check: they
say whether the image is the build its name claims, and the stamp and the record are only as good as the build that
wrote them.

## The exporter

`tools/ota_export.py` runs on the **host**, as root, and writes the file. It is standard library only and imports
nothing from the project, so it is copied to a board on its own.

```bash
python3 ota_export.py --out /var/lib/vehicletap-ota/ota_status.json --service esync --service candump
python3 ota_export.py --out FILE --service esync --interval 30        # keep it fresh; Ctrl-C to stop
```

| Reads | For |
|---|---|
| `bootc status --format json` | booted / staged / rollback image (version, digest, build time) and `rollbackQueued` |
| `/var/lib/esync/state.d/<package>.json`, then `/var/lib/esync/state.json` | the components: a package's own file wins over its entry in the shared one, as the agent reads them. The only eSync state it opens; the rest of that directory may hold identifiers and keys |
| `systemctl show <unit> -p …` per `--service` | state, sub-state, restart count, and when it started (moved from the unit's monotonic time to the wall clock) |
| `timedatectl show -p NTPSynchronized`, `/sys/fs/selinux/enforce`, `uname`, `statvfs /var` | the health facts; `var_used_pct` is what `df` prints |
| `/etc/build-info` (`--build-info`) | `IMAGE_VERSION` and `TIMESTAMP` only |
| `/etc/os-release`, else `/usr/lib/os-release` | `PRETTY_NAME`, else `NAME` and `VERSION_ID`; the rest (URLs, support contacts) is not read. A line that needs the shell to parse is skipped |
| `/proc/device-tree/model` | the board's model, the text before its closing NUL, if it is all printable |
| `bootc -V`, `ostree --version`, `podman -v` | the version word of each |
| `--build-record FILE` | lines of `version kernel bootc ostree podman`, `#` comments; a line that is not five versions is skipped |
| `/proc/<pid>/cmdline`, then `/proc/<pid>/cgroup` | every command line is read to find the programs under `<esync-home>/bin/` and dropped unless it is one; for those, whether the cgroup is `--esync-unit`'s (or below it), and a workload agent's `-t` topic. No other argument gets out |
| `rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\n'`, with `--show-rpm` | the running image's packages, name and version only (the epoch to order versions) |
| the same with `--dbpath /sysroot/ostree/deploy/<stateroot>/deploy/<checksum>.<serial>/usr/lib/sysimage/rpm`, with `--show-rpm` | the rollback image's packages, from that deployment's own database (read-only: `/sysroot` is mounted read-only). The path is built from the stateroot, checksum and serial bootc gives the rollback deployment, each in the one form it has, and none of them is written to the file |
| the last 1 MiB of `<esync-home>/data/log/esyncclient.log` | lines that start with the client's time and carry `<PACKAGE:VERSION#CAMPAIGN@RECORD>`: the package, version and campaign number, and the line's time. From the campaign's status line (`Package is of version …, needs to be of version …, registered rollback to …, … considered already updated: …`) only the rollback version and the result after *considered already updated*. The rest of each line (and the record number) is never looked at; the log also holds the device's ID and server addresses |

* **It only reads.** The commands it can run are fixed, a `--service` name cannot be an option or a path, and the
  only thing written is the output file: a temporary file in the same directory renamed over it, mode 0644 because
  the container runs as another user. The directory must exist; it is never created.
* **It builds from named fields.** The `bootc` output also carries the image registry, the transport and ostree
  checksums, and esync's state may carry more than a version. None of that is read out of its input, so none can be
  in the output; the dashboard's reader whitelists again on the other side. Services come from `--service`, not a
  built-in list.
* **A source that fails is an empty section**, with one line on stderr, never an error: the rest of the file is still
  written. In `--interval` mode a bad pass is logged and the next one runs. A one-shot run exits 1 only if it cannot
  write the file.
* **How often.** With `--interval N` (5 on the J7) it writes every N seconds on a fixed rate, and the sources go at
  their own pace: the files, `/proc` and systemd on every pass; `bootc status`, the one expensive call, every
  `--os-interval` seconds (default 15), keeping its last good answer and the time it was given when a call fails (so a
  busy `bootc` during an update ages the OS card instead of blanking it); the build stamp, the OS's name, the board's
  model, the kernel and the tool versions once, because they cannot change while the board runs (again at the OS interval only while a tool has not
  answered). Measured on the J7 (2026-11-12, OS 4.6.0): a pass of the cheap sources takes about 130 ms (100 ms of
  CPU), a pass with bootc about 430 ms (255 ms), the first pass about 730 ms (540 ms) — about 3 % of one core at 5 s
  and 15 s, where asking everything every 5 s would be about 11 %. The page asks every 5 s and polls rather than being
  pushed: the file is 3 KB on the same machine, and a poll needs nothing to reconnect after a reboot or a tunnel drop.
* **eSync's layout is options**, with the J7's as defaults: `--esync-home /root/esync`, `--esync-unit esync`, and
  `--agent TOPIC` once per workload agent the board should run (`/SDK/RHIVOS_APP`, `/SDK/RHIVOS_OS`).
  `--os-package NAME` (`RHIVOS-OS-AUTOSD` on the J7) names the package that updates the OS image; it has no default,
  because a wrong guess would put an application's record next to the OS.
* **`--show-rpm NAME`**, once per package to show (at most 24), turns on the package comparison. A name is never
  passed to rpm: both lists are read whole and the names looked up in them. The running image's list is read once,
  the rollback image's whenever bootc names another rollback deployment, each about half a second on the J7
  (2026-11-13: 255 packages in 0.47 s, by `rpm -qa` and by `--dbpath`).
* **Mount the directory, not the file** (`deploy/quadlet/optional/vehicletap.container.d/10-ota.conf` does, and CONTAINER.md
  says how to install it). An atomic replace gives the file a new inode, and a single-file bind mount
  keeps pointing at the old one. Give the container the directory, read-only, and set `VEHICLETAP_OTA_FILE` to the file
  in it.

Run once on the J7 test board (2026-10-28) it produced a file the dashboard's reader accepted whole, with `booted
4.4.0`, `rollback 4.5.0`, `rollback_queued true`, three services and the two eSync components, and no registry
address anywhere in it. **How the exporter is kept running on that board is not decided:** `/etc` there is rebuilt at
every boot and the units that survive a reboot come from a signed deployment, so a persistent exporter has to go that
way (see [CONTAINER.md](CONTAINER.md)).

## What is deliberately not in it

Registry names and image references, device or vehicle identifiers, VINs, host names, IP addresses, API keys and
tokens never reach this file, and the reader would drop them if they did. The campaigns are what the client is
working on now, from its current log (it rotates every few minutes); there is still no campaign history, and no record
numbers. There is no signature or chain-of-trust display: OS images are not signature
verified on the test board, so a tick would claim something nobody checked.

## Tests

`tests/test_ota_export.py`: the document built from real captures (registry, digests and checksums replaced), what
must never get out (including the client log's other lines and the agents' command lines), the allowlist of commands
it may run, the files it opens, the cadence (bootc at its interval, the fixed facts once, a failed call keeping the last
good answer, a fixed-rate loop), a fake `/proc` and the real one, and that its output is exactly what the reader
accepts. `tests/test_ota.py`: the clock, every state, the whitelist (including a file seeded with secrets that must not come
out), caps, enumerations, number ranges, the derived `next_boot`, junk in every field, and a shipped sample that must
come through whole. `tests/test_ota_demo.py`: the demo view's route, every rule above in node, and full renders
against a fake DOM (a stale eSync record, a broken `candump` beside a running `cangen`, a stale file, no file, a file
that carries almost nothing, hostile strings). `tests/fixtures/ota/` and `docs/demo/ota.json` use synthetic digests.
