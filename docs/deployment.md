# Deployment Guide

This guide covers production and development deployments. For first-time speaker
setup, including the USB `remote_services` process, start with the main
[README](../README.md).

## Required Configuration

`BASE_URL` is the most important setting. It is the URL your speakers receive in
`OverrideSdkPrivateCfg.xml` and later use for Marge, BMX, update, and stats
requests.

Use a value reachable from the speakers:

- `http://192.168.1.50:8001` when using the default Docker Compose nginx sidecar
- `http://192.168.1.50:8000` when running Soundcork directly
- `http://soundcork.lan:8001` if that name resolves on the speaker network

Do not use:

- `localhost`, `127.0.0.1`, or `0.0.0.0`
- Docker service names such as `soundcork`
- Kubernetes-only DNS names unless the speakers can resolve and reach them

After changing `BASE_URL`, run **Switch to Soundcork** again for each speaker so
the speaker receives a new override file.

## Environment Variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `BASE_URL` | Yes | empty | Device-reachable URL for this service. |
| `DATA_DIR` | No | `./data` | File-backed account, preset, source, device, group, and Spotify data. |
| `UNHANDLED_LOG_DIR` | No | empty | Enables raw 404 logging under `unhandled_raw/`. |
| `SPOTIFY_CLIENT_ID` | No | empty | Enables Spotify account linking when paired with the secret. |
| `SPOTIFY_CLIENT_SECRET` | No | empty | Spotify OAuth client secret. |
| `SPOTIFY_REDIRECT_URI` | No | empty | Optional explicit Spotify redirect URI. Browser flow normally derives it from `BASE_URL`. |
| `DEV_MODE` | Docker only | `false` | Runs `fastapi dev` instead of gunicorn in the container. |

## Docker Compose

The checked-in `docker-compose.yml` uses host networking so UPnP discovery can
see SoundTouch devices on the LAN. It also runs an nginx sidecar on port `8001`
to normalize ETag header casing for Bose clients.

```bash
# edit BASE_URL first
docker compose up -d
docker compose logs -f
```

Use:

- `http://<host-lan-ip>:8001` for `/admin`, `/miniapp`, and `BASE_URL` when the
  nginx sidecar is enabled.
- `http://<host-lan-ip>:8000` only when bypassing/removing nginx.

## Docker Development Mode

```bash
DEV_MODE=true docker compose up --build
```

For live source edits, add a development-only bind mount:

```yaml
volumes:
  - ./data:/soundcork/data
  - ./logs:/soundcork/logs
  - ./soundcork:/app/soundcork:ro
```

The container runs from `/app/soundcork`, which matters because templates,
static files, media, resources, and `swupdate.xml` are loaded relative to the
working directory.

## Bare Metal

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd soundcork
cp .env.shared .env.private
# set BASE_URL and optionally DATA_DIR
fastapi run main.py
```

For development:

```bash
pip install -r requirements-dev.txt
cd soundcork
fastapi dev --host 0.0.0.0 main.py
```

Run from the `soundcork/` application directory so relative paths resolve.

## Systemd

Use `soundcork.service.example` as a starting point. Confirm:

- `WorkingDirectory` points at the `soundcork/` application directory.
- The command uses the virtualenv gunicorn.
- Environment values include at least `BASE_URL` and any non-default `DATA_DIR`.
- The process user can read/write `DATA_DIR` and log paths.

Reload after edits:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now soundcork
```

## Kubernetes and Reverse Proxies

Kubernetes can host Soundcork, but it is usually not the easiest deployment
model because speakers must reach `BASE_URL` and UPnP discovery depends on LAN
multicast. If using Kubernetes:

- ensure ingress or load balancer addresses are reachable from the speaker LAN
- set `BASE_URL` to that reachable address, not an internal service name
- use persistent storage for `DATA_DIR`
- expect automatic discovery to be unreliable unless the pod has host-network
  access to the speaker LAN

If placing Soundcork behind a reverse proxy, keep speaker-facing Bose protocol
URLs unauthenticated and add authentication only around human UI routes if the
proxy can separate them safely. See [SECURITY.md](../SECURITY.md).

## Verification

After startup:

```bash
curl -I http://<host>:8001/admin/
curl -I http://<host>:8001/bmx/registry/v1/services
```

Then open `/admin/`. If speakers are not listed:

- confirm Docker host networking or equivalent LAN access
- use the admin Refresh button to force discovery
- check logs for discovery and reachability messages
- confirm speakers and Soundcork are on the same LAN/VLAN

If a configured speaker shows `UNKNOWN_SOURCE_ERROR`, verify `BASE_URL` from the
speaker's point of view and rerun **Switch to Soundcork**.

If `/admin` shows a reachable speaker with `Marge: Soundcork` but `In Soundcork:
No`, the speaker still has Soundcork's override file but Soundcork has no
matching datastore record. Use **Add to Soundcork** to repair/adopt it into an
account, or **Reset to Bose** to delete the override and reboot back to firmware
defaults.
