# Fork Changelog

This changelog summarizes the fork-specific work in this repository compared
with the source baseline commit `cdb62d2` (`Fix miniapp preset playback and
cookies (#311)`). It covers the 63 fork commits through `2dca10e`.

Range summary: 47 files changed, 6,746 insertions, 2,539 deletions.

## Product Direction

- Repositioned the project as a heavily modified fork focused on keeping Bose
  SoundTouch devices usable after the official Bose cloud API shutdown.
- Rewrote the README and expanded project documentation for local, Docker,
  development, systemd, USB shell-access, rollback, security, Spotify, Radio
  Browser, deployment, and API behavior.
- Added contributor/agent guidance (`AGENTS.md`, `CLAUDE.md`) that documents the
  current architecture, async speaker-call rules, orphan-device handling, and
  testing expectations.

## Admin UI and Device Setup

- Rebuilt `/admin/` into a faster, fragment-loaded UI with cached discovery,
  parallel reachability checks, refresh controls, reboot countdowns, and
  Base URL health warnings.
- Added inline device rename and account rename flows.
- Added repair/adoption handling for orphaned speakers: devices pointed at
  Soundcork but missing from the datastore now remain visible and can be added
  back into a configured account.
- Added **Reset to Bose** for orphaned devices, deleting
  `/mnt/nv/OverrideSdkPrivateCfg.xml` over SSH and rebooting without touching
  Soundcork datastore state.
- Hardened removal so Soundcork no longer deletes a datastore row when it fails
  to remove the speaker override file, avoiding half-configured orphan states.
- Added account selection when adopting a reachable speaker that has no
  `margeAccountUUID` and multiple Soundcork accounts exist.

## Bose/Marge Compatibility

- Fixed speaker rename deadlock by moving blocking speaker calls out of the
  FastAPI event loop with `asyncio.to_thread(...)`.
- Corrected the Marge device update response used during `/name` rename flows:
  returns `200 OK`, echoes `macaddress`, and avoids epoch-like `createdOn`
  values that cause speakers to reject persistent renames.
- Added `/setMargeAccount` repair support so adopted speakers can persist the
  chosen account UUID in speaker NVRAM.
- Added `/marge/streaming/resources/api_versions.xml` and documented route
  ownership and compatibility constraints.
- Improved group-state handling and fixed asymmetric group derivation by using
  unioned peer IDs and better master/member state.

## Miniapp and Dashboard

- Modernized the miniapp dashboard UI with clearer device selection, refreshed
  account/login styling, footer year handling, and improved visual feedback.
- Added live device-state polling so playback state, selected devices, active
  presets, volume, and local source changes update without relying on stale
  cookies.
- Added volume controls, mute behavior, Bluetooth/Aux/AirPlay indicators,
  track/artist display, Next/Previous controls, and smarter Play/Stop display.
- Added source buttons for Bluetooth and Aux, and refined transport visibility
  for local inputs.
- Added fetch-based play/stop/preset/source actions with instant UI feedback and
  spinner handling until the speaker confirms the transition.
- Added miniapp group controls, group panel UI, leave-group diagnostics, and
  volume sync when grouping speakers.

## Preset Management

- Added preset management UI for adding, editing, deleting, and selecting preset
  slots.
- Changed preset slot entry to a 1-6 dropdown.
- Added complete timestamp and preset fields needed for proper playback.
- Improved preset-card readability, selected/playing highlighting, circular
  badges, cold-start auto-reload, and empty/hidden states before device
  selection.

## Device Discovery and Speaker Interaction

- Improved discovery reliability and diagnostics, including a discovery cache,
  forced refresh support, and better logging.
- Removed the standalone `zeroconf_primer.py` experiment.
- Added SSH/HTTP timeouts and narrowed error handling around speaker operations.
- Added helpers for removing files, rebooting speakers, reading speaker state,
  and checking reachability without hanging admin requests.

## Datastore, Config, and Runtime

- Fixed `StopIteration` crashes when `DATA_DIR` is empty or missing.
- Defaulted `DATA_DIR` to `./data` and auto-created required directories.
- Standardized environment variables on `UPPER_SNAKE_CASE`.
- Fixed `UNHANDLED_LOG_DIR` wiring and added raw unhandled-request logging for
  unknown Bose endpoints.
- Added Base URL validation/warnings to catch deployments where speakers cannot
  reach Soundcork.
- Cleaned stale cookies and local state paths after dashboard refactors.

## Docker, Packaging, and CI

- Added Docker `DEV_MODE` and fixed `fastapi dev` working-directory behavior.
- Updated Docker, compose, `.dockerignore`, `.gitignore`, `.env.shared`,
  `pyproject.toml`, package data, systemd example, and requirements split for
  runtime vs development usage.
- Tightened lint workflows to check formatting instead of silently rewriting.
- Fixed mypy typing drift and aligned Python 3.12 expectations.

## Tests

- Added or updated tests for admin behavior, datastore edge cases, miniapp login
  and dashboard rendering, BMX routes, and speaker UI helpers.
- Added coverage for account info persistence, empty datastore handling, admin
  rename behavior, and dashboard filtering of discovered-only devices.

## Commit Reference

- `174f821` Fix StopIteration error when data_dir is empty or doesn't exist
- `314eba8` Improve device discovery reliability, modernize dashboard UI, and add diagnostic logging
- `642178e` Add preset management UI with add, edit, and delete functionality
- `0fdacab` Fix preset table text colors for visibility
- `686bd5a` Change Preset Slot input to dropdown select 1-6
- `d0b40d2` Add timestamps and complete preset fields for proper playback
- `516f193` Add detailed logging for preset playback debugging
- `49df19b` Add DEV_MODE support for Docker development
- `7895ed5` Fix fastapi dev path in docker-entrypoint.sh
- `087c64b` Fix working directory for fastapi dev
- `2884f75` Redesign admin page to match modern dashboard UI
- `d17796b` Add debug logging for device source list
- `9bd20b8` Expand source list logging to show individual sources
- `f47fd7b` Fix unhandled_log_dir env var name
- `2ed8431` Add request logging middleware for debugging
- `233073e` Clean up debug logging, add base_url health check, document base_url
- `e5975c6` Fix skip-link visibility and position
- `cdd2e16` Add volume controls to dashboard
- `8f86c30` Scope admin device-card styles to admin grid only
- `b4a17ba` Show selected device clearly on dashboard
- `1333c95` Show live device state in top bar instead of stale cookies
- `a65d7cc` Remove SELECTED badge from active device
- `5fd1723` Poll device state after play/stop to avoid stale dashboard render
- `60c469f` Make preset names readable with a translucent chip
- `0c38c4a` Auto-refresh + loading UI while device transitions, highlight playing preset
- `42603bb` Add admin UI to rename accounts
- `2b23a66` Match login account row to admin style
- `5b0f04f` Reflect account renames live and gate Rename button on changes
- `0a1db70` Remove skip-link from all pages
- `ae3567e` Add admin panel link to dashboard and login
- `911fdd6` Make /admin/ load instantly via async fragment + cache + parallel probes
- `1d03c12` Drop redundant "Plays on X" subtitle from Presets section
- `2807ea8` Restore Presets subtitle and stretch preset cards to fill grid cells
- `bcdcaf0` Hide preset tiles until a device is selected, center the grid
- `6fb2967` Instant click feedback on play/stop/preset, shorten state-confirm poll
- `6e71853` Switch play/stop/preset to fetch-based submit with instant UI update
- `043dd35` Don't show stale preset when device switched to Bluetooth/Aux/etc.
- `cf5f7fb` Bind submit interceptor to all forms, not just those in `<main>`
- `cf0a1a1` Poll device status every 3s so external source changes reflect quickly
- `f0e126f` Add Bluetooth / Aux source buttons to the dashboard
- `3a86106` Keep spinner up until the device confirms the transition
- `7d08730` Resolve source-switch action on source change, hide Stop on local inputs
- `dc0d969` Only hide transport controls on AUX, keep them for Bluetooth
- `b7a1668` Show track/artist via Bluetooth/AirPlay/UPnP, add Next/Prev, smart Play
- `efe64c0` Add AirPlay status indicator, simplify track display
- `ca52932` Use current year in footer copyright
- `941081c` Require UPPER_SNAKE_CASE env var names
- `7c16673` Default DATA_DIR to ./data and auto-create the directory
- `5dc08c8` Ignore the local datastore directory
- `db83a91` Clean up SoundCork maintenance issues
- `15d209c` Admin polish: favicon route, README rewrite, group/remove/rename UI
- `20245bc` Admin polish: inline pencil-edit, bottom-aligned action buttons
- `62462f4` Group panel: sleeker UI, volume sync, leave-group diagnostics
- `7915a9e` Fix group asymmetry, inline chips, derive is_master correctly
- `b3a0cf1` Align docs and project configuration
- `1d76cc5` Fix CI lint and miniapp typing
- `9ef8d85` Dashboard: top-align columns, restore preset slot badges
- `f20c8e6` Dashboard: circular preset badges, cold-start auto-reload
- `dddeccf` Keep speaker online after admin rename
- `8265658` Fix admin device rename and unblock SSH-heavy admin endpoints
- `16c65f1` Admin: orphan-state hardening + async polish
- `6efd803` Document admin repair and reset flows
- `2dca10e` Refresh CLAUDE.md / AGENTS.md / README.md for current admin + marge flows
