# Security Policy

Soundcork is designed for a trusted home LAN. It is not a hardened public
service and should not be exposed directly to the internet.

## Security Model

Soundcork intentionally emulates Bose SoundTouch cloud endpoints and provides
local admin and miniapp web UIs. These endpoints currently have no user
authentication, authorization, CSRF protection, or multi-tenant isolation.

Run Soundcork only where every client on the network is trusted. A typical safe
deployment is a home network behind a router/firewall, with Soundcork reachable
only from the same LAN or a trusted VPN.

## Sensitive Capabilities

- `/admin` can discover speakers, rename/remove stored devices, copy speaker
  state, write or delete `/mnt/nv/OverrideSdkPrivateCfg.xml`, set the speaker's
  Marge account UUID during repair, and reboot speakers.
- `/miniapp` can control playback, volume, local sources, presets, and groups.
- Bose protocol endpoints under `/marge`, `/bmx`, `/service`, `/updates`, and
  `/media` are unauthenticated because SoundTouch speakers do not authenticate
  to the original Bose services in a way Soundcork can reuse locally.
- Device setup requires root shell access on each speaker after the
  `remote_services` USB process. Treat that access as powerful and LAN-only.

## Stored Data

`DATA_DIR` may contain:

- account and speaker identifiers
- speaker names and IP addresses
- presets, recents, and configured source data
- copied `Sources.xml` data, which may include service tokens or account IDs
- Spotify OAuth tokens when Spotify integration is configured

Protect `DATA_DIR` like credentials. Do not publish it, commit it, or mount it
into unrelated containers.

## Deployment Requirements

- Do not expose Soundcork directly on a public IP.
- Do not port-forward `/admin`, `/miniapp`, or Bose protocol endpoints from the
  internet.
- If remote access is needed, use a VPN. If you must place Soundcork behind a
  reverse proxy, add authentication and TLS at the proxy layer and understand
  that speakers still need unauthenticated access to the Bose-compatible URLs.
- Keep `BASE_URL` on an address the speakers can reach, usually the host LAN IP
  or a LAN DNS name.
- Use host networking for Docker when relying on UPnP discovery.

## Speaker Shell Access

The USB `remote_services` process enables root shell access on the speaker. On
some firmware, shell access can be made persistent by running:

```sh
touch /mnt/nv/remote_services
```

Do not expose speaker SSH or telnet outside the LAN. Do not experiment with port
17000 / TAP console commands; some demo commands can put a speaker into
factory/demo mode.

## Reporting Security Issues

Report vulnerabilities through GitHub Security Advisories:

https://github.com/deborahgu/soundcork/security/advisories/new
