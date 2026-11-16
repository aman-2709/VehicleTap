# Changelog

VehicleTap is based on **Ha-Kake**, created by David D. Karnowski
(<https://github.com/davidkarnowski/Ha-Kake>), and keeps its AGPL-3.0-or-later license and its copyright notices
(see [NOTICE](NOTICE)). This file records what was changed in VehicleTap, with dates. The first change was made on
2026-10-09; the git history is the full record, and everything before that date is the original project's.

## 2026-11-13: what an OS update changed, on the demo view

- **OS Packages** on `/ota/demo`: how many of the OS image's packages an update changed against the rollback image
  (on the J7, 4.3.0 to 4.6.0: *119 updated · 1 added · 0 removed* of 255), on one folded line, and behind *Show
  packages* a box for each package the board is asked to show with its version and what it was (*3.5.9-1, was
  3.5.7-2*), so a presenter can open a whole-OS update beyond the kernel and the update tools when the audience
  is ready for it: OpenSSL, OpenSSH, the crypto policy, the SELinux policy, glibc, systemd, the boot chain.
- **Downgrades:** the exporter orders versions as rpm does (epoch, version, release; `rpmvercmp` ported and checked
  against rpm on the J7, 97 pairs and the 119 changed packages), so after the demo's reset to an older image the
  line reads *0 updated · 119 downgraded · 0 added · 1 removed* and each box *downgraded from* the newer version,
  where it said *updated* before. `rpm -qa` now asks for the epoch (`%{EPOCHNUM}`), which orders versions and is not
  written. `os.packages.downgraded` is an addition to schema 1 (2026-11-14).
- The *OS OTA Status* card goes from the running version straight to the kernel and tool versions: the *Staged*
  and *Rollback* boxes are gone, and what the next boot starts is said in the *Update* box when it matters (*Rollback
  queued: a reboot starts …*, *Update staged: …*). Presenters found two version slots beside the running one
  confusing.
- Versions on `/ota/demo` leave out the build's distribution tag and the kernel's architecture: the kernel box shows
  *6.12.0-281* for *6.12.0-281.el10iv.aarch64*. An update that changed only the tag keeps it in its box; hovering over
  a box shows the full strings, and `/api/ota` and `/ota` keep them.
- The exporter takes `--show-rpm NAME` (repeat; at most 24) and then reads `rpm -qa` for the running image and the
  rollback image's own package database by path, from the deployment bootc names; the path is never written out.
  `os.packages` is an addition to schema 1. [docs/OTA.md](docs/OTA.md).

## 2026-11-16: the demo view's cards show the latest OTA attempt

- **Latest OTA attempt · Campaign #N** replaces the *Update*, *Deployed version*, *Rollback version* and *Result*
  boxes on both cards: *Attempted*, *Rolls back to* and *Running now* (bootc's booted version on the OS card, eSync's
  record on an application's), then one line of eSync's result: *Update to X did not pass its checks. The device
  rolled back to Y.*, *Updated to X.*, *Update to X is in progress.*, or *No recent activity for campaign #N.* A
  campaign with no rollback version registered shows *none registered* and never mentions a rollback. The old
  *Campaign #N → X active* text is gone: the client keeps confirming a finished campaign every 30 s, and a
  rolled-back campaign read *active* beside *Rolled back*. Only a campaign in progress is called active.
- Every value comes from the board (the client log, bootc, eSync's record); the page holds no version, campaign
  number or package name. The *Update* box remains on the OS card for what bootc says the next boot starts (a queued
  rollback or a staged update).
  [docs/OTA.md](docs/OTA.md) "The demo view".

## 2026-11-12: a demo view of the OTA status, /ota/demo

- **`/ota/demo`** shows the same `/api/ota` answer for an audience: an *OS OTA Status* card (the running version in
  large type, the staged and rollback slots, a box each for the kernel, `bootc`, `ostree` and `podman` versions, what
  the next boot starts or the campaign under way, the build check, eSync's record of the OS package against the
  running image) and an *APP OTA Status* card (each
  application package's installed version and campaign, and a box for each of its services, so a broken `candump`
  shows red beside a running `cangen`). A badge says *Live*, *Stale* or *No data*; a stale file stays on screen,
  dimmed. Only values the answer carries are shown. `/ota` and `/ota/demo` link to each other.
- **Deployed version, Rollback version and Result** on both cards: for each package's last campaign, the version it
  deployed, the version eSync rolls back to if that fails, and eSync's own result (*Updated*, *Rolled back*, *In
  progress*), read from the eSync client's status line for the campaign (`registered rollback to …`, `considered
  already updated: …`).
- The exporter reads the OS's name (`/etc/os-release`) and the board's model (`/proc/device-tree/model`) once, and
  takes `--os-package` to name the package that updates the OS image. Additions to schema 1.
- `ota.js` shares its poll loop and its version formatting with the demo view instead of each page having a copy.
  [docs/OTA.md](docs/OTA.md) "The demo view".

## 2026-11-12: the /ota page shows the board's own record, every 5 seconds

- **Is the running image the build its version names?** The OS card compares the image's build stamp
  (`/etc/build-info`) with the version bootc reports, and the running kernel, `bootc`, `ostree` and `podman` with a
  build record (`--build-record`), and shows the tools' versions. The kernel and tool version numbers are set in
  the accent colour so they stand out from the words around them.
- **An eSync card:** how many of the SDK's processes run and whether any run outside its unit, each workload agent
  the board should have (`--agent`), and the campaigns the eSync client is working on, from its log, with when each
  was last logged.
- **Update components** read eSync's per-package files (`state.d/`), which the current agent writes, before the old
  shared `state.json`.
- **How often:** the exporter writes on a fixed rate (5 s on the J7) and asks `bootc` every 15 s, keeping its last good
  answer with its age when a call fails; the build stamp and tool versions are read once. A file is stale after four
  missed writes instead of five minutes.
- Additions to schema 1: an older reader drops the new fields, this one shows an older file as before.
  [docs/OTA.md](docs/OTA.md).

## 2026-11-03: the project is renamed VehicleTap

Ha-Kake and its leaf logo are trademarks of the original author, who asks forks to use their own name and logo.

**Nothing you have has to be moved or re-recorded.** The old names keep working:

- The data folder. An existing `Ha-Kake` (Linux: `ha-kake`) folder is used as it is when there is no `VehicleTap`
  one; nothing is created beside it, moved, copied or deleted. See [docs/DATA.md](docs/DATA.md).
- Environment variables. Every `HAKAKE_*` name is still read; the `VEHICLETAP_*` name wins if both are set.
- Replay recordings. Files with the old `"hakake_replay": 1` key still load; only the new
  `"vehicletap_replay": 1` is written.
- Saved layouts and settings in the browser (their storage keys are unchanged).

**What does change, and what to do about it:**

| Was | Is now | If you use it |
|---|---|---|
| `HAKAKE_*` variables | `VEHICLETAP_*` | nothing; the old names still work |
| `hakake_sim.py` | `vehicletap_sim.py` | change the command you type |
| `bridge/hakake_bridge.py`, `hakake-bridge.service` | `vehicletap_bridge.py`, `vehicletap-bridge.service` | reinstall the unit on the Pi; the old one keeps running until you do |
| MQTT topic root `hakake/leaf` (default) | `vehicletap/leaf` | **breaking if you rely on the default**: a bridge and a reader on different versions see nothing until both are updated, or set `prefix` explicitly to the old root |
| JSON schema ids `hakake/mqtt/*`, bridge name `hakake-bridge` | `vehicletap/mqtt/*`, `vehicletap-bridge` | only if you match on them |
| Container image, Quadlet unit and volume `ha-kake` | `vehicletap` | rename them on a board that has the old ones |
| the header's leaf mark and the profile's `LOGO` field | a neutral dial for every vehicle; `LOGO` is no longer read | nothing, unless your own profile sets `LOGO`: it is ignored |
| `web/static/hakake.css` | `web/static/vehicletap.css` | only if you link it |

The commercial-licensing offer in the original project is the original author's to make, so it is not carried
over: contributions to VehicleTap are accepted under AGPL-3.0-or-later, and anyone who wants a commercial license
for the original code should ask the upstream project. Vulnerability reports go through the repository's GitHub
private reporting instead of a personal e-mail address.

## 2026-10-27 to 2026-10-28: the board's OS-update state on an /ota page

- `/ota` and `GET /api/ota`: which OS image is booted, staged or queued for rollback, which image the next
  boot starts, the update components, the services around them and basic health. The dashboard container cannot
  see any of that, so a host process writes one JSON file and the app only reads it through a whitelist.
  [docs/OTA.md](docs/OTA.md).
- `tools/ota_export.py`: the host-side writer (standard library only, read-only commands, atomic write).
- An opt-in Quadlet drop-in that gives the container the status file read-only.

## 2026-10-24 to 2026-10-27: containers

- A `Containerfile` and a Podman Quadlet unit that run the dashboard and simulator on a headless board
  (RHIVOS / TDA4), viewed through an SSH tunnel; demos 1 and 2 in a container. [docs/CONTAINER.md](docs/CONTAINER.md).

## 2026-10-09 to 2026-10-16: the simulator and its cockpit

- `tools/sim_to_can.py` puts the simulated ECUs on a kernel CAN device and serves the simulator's control API, so
  the `/sim` cockpit can drive a real bus; `--can-channel` and `--sim-control-url` on the dashboard.
- `/sim` cockpit: a tidy first-run layout, a filterable live model state that flashes what changed, foldable knob
  panels with compact switches, shipped dashboard and cockpit arrangements, and a favicon on both pages.
- The body, vehicle and climate tiles: rolling wheels, reverse lamps with a beam, a larger shifter, boxed readings.
- `Demo-Readme.md` (how to bring up each demo and what every argument does), Ubuntu setup notes and Credits.
- `pyobjc` packages install only on macOS.
