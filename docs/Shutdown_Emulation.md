# Shutdown Emulation Notes

Bose SoundTouch cloud support ended on May 6, 2026, after an extension from the
original February 18, 2026 date. Soundcork emulates enough of the Bose cloud
surface for speakers to keep using cloud-backed features from a local server.

## What the Speaker Normally Uses

The firmware's default config points at Bose services:

```xml
<SoundTouchSdkPrivateCfg>
  <margeServerUrl>https://streaming.bose.com</margeServerUrl>
  <statsServerUrl>https://events.api.bosecm.com</statsServerUrl>
  <swUpdateUrl>https://worldwide.bose.com/updates/soundtouch</swUpdateUrl>
  <usePandoraProductionServer>true</usePandoraProductionServer>
  <isZeroconfEnabled>true</isZeroconfEnabled>
  <saveMargeCustomerReport>false</saveMargeCustomerReport>
  <bmxRegistryUrl>https://content.api.bose.io/bmx/registry/v1/services</bmxRegistryUrl>
</SoundTouchSdkPrivateCfg>
```

Soundcork does not edit this file. The admin **Switch to Soundcork** action
writes `/mnt/nv/OverrideSdkPrivateCfg.xml` instead:

```xml
<SoundTouchSdkPrivateCfg>
  <margeServerUrl>{BASE_URL}/marge</margeServerUrl>
  <statsServerUrl>{BASE_URL}</statsServerUrl>
  <swUpdateUrl>{BASE_URL}/updates/soundtouch</swUpdateUrl>
  <isZeroconfEnabled>true</isZeroconfEnabled>
  <usePandoraProductionServer>true</usePandoraProductionServer>
  <saveMargeCustomerReport>false</saveMargeCustomerReport>
  <bmxRegistryUrl>{BASE_URL}/bmx/registry/v1/services</bmxRegistryUrl>
</SoundTouchSdkPrivateCfg>
```

Deleting the override file and rebooting returns the speaker to the firmware
defaults, but it does not restore Bose cloud features now that the Bose service
has ended.

## Emulated Surfaces

- **Marge (`/marge`)**: account, device, presets, recents, sources, groups,
  provider settings, update checks, and Spotify token endpoints.
- **BMX (`/bmx` and `/core02/...`)**: service registry, TuneIn navigation and
  playback, custom stream playback, SiriusXM service metadata, media assets.
- **Stats (`/v1/scmudc`, `/v1/stapp`)**: no-op 200 responses to reduce speaker
  log noise.
- **Software update (`/updates/soundtouch`)**: local update XML response.
- **Service group helpers (`/service/account/...`)**: optional helper endpoints
  for stereo group create/modify/remove when direct speaker calls are not
  enough.

## Local-Only Features

The original SoundTouch app still talks to speakers on the LAN for many direct
actions. AUX, Bluetooth, AirPlay, UPnP/DLNA, and Spotify Connect do not depend
on Soundcork's cloud emulation in the same way as Bose cloud presets and BMX
services.

## Operational Notes

- `BASE_URL` must be reachable by speakers, not just by browsers.
- When `BASE_URL` changes, rerun **Switch to Soundcork** on every speaker.
- Grouping behavior is partly direct speaker-to-speaker behavior and partly
  Marge/service-state behavior; Soundcork stores group XML and the miniapp now
  exposes group/ungroup controls.
- Some external providers still depend on their own upstream APIs. Soundcork
  replaces Bose's API surface, not TuneIn, Spotify, SiriusXM, or radio stream
  hosts themselves.
