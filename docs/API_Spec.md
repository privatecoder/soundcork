# API Overview

Soundcork exposes three kinds of endpoints:

- Bose-compatible endpoints that SoundTouch speakers call directly.
- Local web UI endpoints for humans.
- Management/helper endpoints used by Soundcork operators or the miniapp.

The generated OpenAPI schema at `/docs` is the authoritative route list for
the running server. This file explains route ownership and protocol intent.

## Bose-Compatible Endpoints

These routes emulate Bose cloud behavior and usually must remain
unauthenticated so SoundTouch firmware can call them.

### Marge

Marge is the main account/device XML API. Bose firmware originally used
`https://streaming.bose.com`; Soundcork serves it under `/marge`.

Important routes:

- `POST /marge/streaming/support/power_on`
- `GET /marge/streaming/sourceproviders`
- `GET /marge/streaming/account/{account}/full`
- `GET /marge/streaming/account/{account}/devices`
- `GET /marge/streaming/account/{account}/sources`
- `POST /marge/streaming/account/{account}/source`
- `DELETE /marge/streaming/account/{account}/source/{source_id}`
- `GET /marge/streaming/account/{account}/provider_settings`
- `POST /marge/streaming/music/musicprovider/{provider_id}/is_eligible`
- `POST /marge/streaming/music/musicprovider/{provider_id}/trial/is_eligible`
- `GET /marge/streaming/account/{account}/device/{device}/presets`
- `GET /marge/streaming/account/{account}/presets/all`
- `PUT /marge/streaming/account/{account}/device/{device}/preset/{preset_number}`
- `DELETE /marge/streaming/account/{account}/device/{device}/preset/{preset_number}`
- `GET /marge/streaming/account/{account}/device/{device}/recents`
- `POST /marge/streaming/account/{account}/device/{device}/recent`
- `POST /marge/streaming/account/{account}/device/`
- `PUT /marge/streaming/account/{account}/device/{device_id}`
- `DELETE /marge/streaming/account/{account}/device/{device}`
- `GET /marge/streaming/software/update/account/{account}`
- `GET /marge/streaming/device/{device_id}/streaming_token`
- `POST /marge/streaming/account/login`
- `POST /marge/oauth/device/{device_id}/music/musicprovider/{provider_id}/token/{token_type}`

Responses are XML with Bose-compatible structure. Preserve tag names, MIME
types, status behavior, and date formats when changing these routes. The device
update route (`PUT /marge/streaming/account/{account}/device/{device_id}`) is
also used by speakers during local `/name` renames; it must stay responsive,
return `200 OK`, and echo fields such as `macaddress` that firmware sends.

The OAuth route currently handles Spotify provider `15`; unsupported providers
return `404`.

### Marge Groups

Groups are SoundTouch stereo/zone group state. Current Marge group routes:

- `GET /marge/streaming/account/{account}/device/{device}/group`
- `POST /marge/streaming/account/{account}/group`
- `POST /marge/streaming/account/{account}/group/{group}`
- `DELETE /marge/streaming/account/{account}/group/{group}`

The miniapp also talks directly to local speaker APIs for grouping where
possible, then relies on Marge state and datastore XML for consistency.

### BMX

BMX is the music-service JSON API. Bose firmware originally used
`https://content.api.bose.io/bmx`; Soundcork serves:

- `GET /bmx/registry/v1/services`
- `GET /bmx/tunein`
- `GET /bmx/tunein/v1/navigate`
- `GET /bmx/tunein/v1/navigate/{encoded_uri}`
- `GET /bmx/tunein/v1/navigate/sub/{subsection}/{encoded_uri}`
- `GET /bmx/tunein/v1/navigate/profiles/{profile_type}/{program_id}/{encoded_uri}`
- `GET /bmx/tunein/v1/search`
- `GET /bmx/tunein/v1/playback/station/{station_id}`
- `GET /bmx/tunein/v1/playback/episodes/{episode_id}`
- `GET /bmx/tunein/v1/playback/episode/{episode_id}`
- `POST /bmx/tunein/v1/report`
- `GET /core02/svc-bmx-adapter-orion/prod/orion`
- `GET /core02/svc-bmx-adapter-orion/prod/orion/station`
- `GET /core02/svc-bmx-adapter-siriusxm-everest-eco1/prod/live-adapter`
- `GET /media/{filename}`

The service catalog is built from `soundcork/resources/bmx_services.json` and
uses `BASE_URL` for media and service base URLs. Avoid hard-coded service array
positions; look up services by `id.name`.

### Stats and Updates

- `POST /v1/scmudc/{deviceid}`
- `POST /v1/stapp/{deviceid}`
- `GET /updates/soundtouch`
- `GET /marge/streaming/resources/api_versions.xml`

Stats endpoints intentionally return success without doing anything. Update
routes provide local compatibility responses and are not a firmware update
service.

## Local Web UI

### Admin UI

Routes:

- `GET /admin/`
- `GET /admin/devices-fragment`
- `POST /admin/addDevice/{device_id}`
- `POST /admin/repairDevice/{device_id}`
- `POST /admin/resetDevice/{device_id}`
- `POST /admin/switchToSoundcork/{device_id}`
- `POST /admin/renameDevice/{device_id}`
- `POST /admin/removeDevice/{device_id}`
- `POST /admin/renameAccount/{account_id}`

The admin UI discovers speakers, checks port-22 reachability, imports speaker
data, writes or deletes `OverrideSdkPrivateCfg.xml`, reboots speakers, repairs
orphaned Soundcork assignments, and lets operators rename/remove stored devices.
`repairDevice` is a compatibility alias for the current Add flow. The UI
requires trusted LAN access; it is not authenticated.

### Miniapp UI

Routes include:

- `GET /miniapp`, `/miniapp/login`, `/miniapp/dashboard`, `/miniapp/status`
- playback: `/miniapp/play`, `/miniapp/stop`, `/miniapp/media-play`,
  `/miniapp/media-next`, `/miniapp/media-previous`
- device/source selection: `/miniapp/select-device`,
  `/miniapp/select-content-item`, `/miniapp/select-source`
- volume: `/miniapp/volume-up`, `/miniapp/volume-down`, `/miniapp/mute`
- groups: `/miniapp/group-toggle`, `/miniapp/group-leave`
- presets: `/miniapp/presets`, `/miniapp/presets/save`,
  `/miniapp/presets/delete`
- session: `/miniapp/logout`

The miniapp uses cookies for lightweight local UI state. It polls
`/miniapp/status` so playback state, volume, local-source chips, preset
highlighting, and group state catch up when speakers change outside Soundcork.

## Management and Helper Endpoints

### Spotify Management

- `GET /mgmt/spotify/init`
- `POST /mgmt/spotify/init`
- `GET /mgmt/spotify/callback`
- `POST /mgmt/spotify/confirm`
- `GET /mgmt/spotify/accounts`

These endpoints link Spotify accounts and store refresh credentials under
`DATA_DIR`. See [spotify.md](spotify.md).

### Service Group Helpers

- `GET /service/account/{account}/listgroups`
- `GET /service/account/{account}/creategroup?master={device}&slave={device}`
- `GET /service/account/{account}/modgroup?groupid={id}&newname={name}`
- `GET /service/account/{account}/removegroup?groupid={id}`

These helper endpoints are for cases where direct SoundTouch group behavior is
not enough. Most users should use the miniapp group controls.

### Legacy Setup Helpers

- `GET /scan`
- `GET /scan_recents`
- `POST /add_device/{device_id}`

These routes predate the current admin UI. Prefer `/admin/` for normal setup.

## Data and Compatibility Rules

- `DATA_DIR` stores XML and JSON state. Do not assume it is disposable.
- `Sources.xml` is copied from speakers and may contain source credentials.
- XML shape matters more than Python object elegance.
- Speakers expect exact Bose-style field names such as `ContentItem`,
  `sourceAccount`, `updatedOn`, and `createdOn`.
- `BASE_URL` is embedded into speaker configuration and BMX responses. Wrong
  values often surface as playback errors rather than HTTP errors in browsers.
