# Soundcork

A self-hosted replacement for the Bose SoundTouch cloud, so your speakers keep working now that Bose cloud support has ended.

This repository is a heavily modified fork of the original Soundcork project, focused on keeping Bose SoundTouch devices usable after the official Bose cloud API shutdown.

## What this is

[Bose's SoundTouch cloud support ended on May 6, 2026, after being extended from the original February 18, 2026 shutdown date.](https://www.bose.com/soundtouch-end-of-life) Bose's updated SoundTouch app still supports local-only functions, but cloud-backed features such as cloud presets, built-in music services, internet radio, and future system updates no longer have Bose's cloud infrastructure behind them.

**Soundcork** is a FastAPI server you run on your own LAN. It implements enough of the Bose API surface for SoundTouch speakers to keep operating without Bose's cloud. After a one-time configuration step, each speaker is pointed at your soundcork instance instead of Bose's, and:

- **Presets keep playing** — TuneIn / internet radio / your custom streams
- **The original Bose SoundTouch app still works** for basic playback (it talks to the speaker over its local port, not to the cloud)
- **A built-in web UI (`/miniapp`)** lets you start/stop, switch sources, manage presets, change volume, and group/ungroup speakers from any browser
- **A web admin (`/admin`)** discovers speakers on your network, walks you through onboarding, and lets you rename, repair, remove, or reset speakers/accounts

The change soundcork pushes to the speaker is intentionally minimal and reversible (see "What soundcork changes on your speaker" below).

> ⚠️ **Security:** Soundcork has no authentication and exposes admin and SSH-driven endpoints. Run it inside your home network only, behind a router/firewall. See [SECURITY.md](SECURITY.md).

---

## Quick start

Start soundcork with Docker on the same LAN as your speakers:

```bash
git clone <this-repo>
cd soundcork
# edit docker-compose.yml — change BASE_URL to your host's LAN IP
docker compose up -d
```

Then open `http://<your-host-LAN-ip>:8001/admin`. The admin UI can discover speakers immediately, but completing onboarding still requires shell access on each speaker.

For a new speaker/account, prepare the USB stick described below, boot each speaker with it once, and wait until `/admin` shows **Reachable: Yes**. After that, the admin UI can copy the speaker data, write the soundcork override, and reboot the speaker.

If `/admin` shows a speaker that is already pointed at Soundcork but is not in Soundcork's datastore, use **Add to Soundcork** to repair it. If the speaker should go back to the firmware defaults instead, use **Reset to Bose**.

---

## Configuration

All settings are read from environment variables. The Python attributes are lowercase per PEP 8, but the environment variables themselves use `UPPER_SNAKE_CASE`.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `BASE_URL` | **Yes** | (none) | The URL **your speakers** use to reach soundcork. Must be a LAN IP or a hostname resolvable from the speaker network — *not* `localhost`, `127.0.0.1`, or a Docker container name. Example: `http://192.168.1.50:8000` |
| `DATA_DIR` | No | `./data` | Local directory where soundcork stores per-account presets, sources, recents, and device info. Resolved relative to the launch directory; auto-created on startup. |
| `UNHANDLED_LOG_DIR` | No | (off) | If set, unhandled 404 requests are dumped under `unhandled_raw/` as metadata, raw body, and a readable copy when possible. Useful for debugging which Bose endpoints a speaker is calling that soundcork doesn't handle yet. |
| `SPOTIFY_CLIENT_ID` | No | (off) | Spotify OAuth for the BMX Spotify adapter. Leave empty to disable Spotify integration. |
| `SPOTIFY_CLIENT_SECRET` | No | (off) | Spotify OAuth client secret. |
| `SPOTIFY_REDIRECT_URI` | No | (off) | Spotify OAuth redirect URI. |
| `DEV_MODE` | No | `false` | Docker only. Set to `true` to launch `fastapi dev` (hot reload, verbose logging) instead of gunicorn. |

> 💡 The biggest single source of "why isn't this working" reports is a wrong `BASE_URL`. If presets fail with `UNKNOWN_SOURCE_ERROR` (1005), the speaker can't reach the URL you set. See [docs/deployment.md](docs/deployment.md) for the full diagnosis.

You can place values in `.env.private` (gitignored) next to `.env.shared`, or pass them through environment variables / `docker compose`.

---

## Running locally (bare metal)

Requires **Python 3.12+**.

```bash
git clone <this-repo>
cd soundcork

python3.12 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell

pip install -r requirements.txt
```

Configure:

```bash
cd soundcork
cp .env.shared .env.private
# edit .env.private — at minimum:
#   BASE_URL = "http://<your-host-LAN-ip>:8000"
```

Start the server in production mode:

```bash
# run from the soundcork/ application directory so templates/resources resolve
fastapi run main.py
# listening on http://0.0.0.0:8000
```

Or as a systemd service:

```bash
cd ..                                # back to the repository root
pip install build
python -m build
pip install dist/*.whl
sudo cp soundcork.service.example /etc/systemd/system/soundcork.service
# edit User=, WorkingDirectory=, and environment handling as needed
sudo systemctl daemon-reload
sudo systemctl enable --now soundcork
```

### Local development mode

Hot reload + verbose logs:

```bash
cd soundcork
fastapi dev --host 0.0.0.0 main.py
# listening on http://0.0.0.0:8000, auto-reloads on file changes
```

Run the test suite:

```bash
cd ..
pip install -r requirements-dev.txt
pytest
```

---

## Running in Docker

The repository ships a `Dockerfile`, an entrypoint script that switches between dev and prod, and a `docker-compose.yml` with an optional nginx ETag sidecar (see [issue #129](https://github.com/deborahgu/soundcork/issues/129)).

```yaml
# docker-compose.yml — edit BASE_URL before starting
services:
  soundcork:
    build: .
    network_mode: host                     # required: UPnP needs LAN visibility
    environment:
      - BASE_URL=http://192.168.1.50:8001  # ← change to your host's LAN IP
      - DATA_DIR=/soundcork/data
      - UNHANDLED_LOG_DIR=/soundcork/logs/traffic   # optional
      # - SPOTIFY_CLIENT_ID=your-client-id          # optional
      # - SPOTIFY_CLIENT_SECRET=your-client-secret  # optional
      # - SPOTIFY_REDIRECT_URI=http://192.168.1.50:8001/mgmt/spotify/callback
      - DEV_MODE=${DEV_MODE:-false}
    volumes:
      - ./data:/soundcork/data
      - ./logs:/soundcork/logs
    restart: unless-stopped
  nginx-etag:
    image: nginx
    network_mode: host
    volumes:
      - ./nginx-ETag.conf:/etc/nginx/conf.d/default.conf:ro
    restart: unless-stopped
```

Why `network_mode: host`? Two reasons:

1. UPnP discovery uses multicast that Docker bridge networks don't forward — you'd never see your speakers.
2. The speakers call back to `BASE_URL` from outside the container — host networking is the simplest way for that to work without port-mapping gymnastics.

Start:

```bash
docker compose up -d
docker compose logs -f             # follow logs
docker compose down                # stop
```

Soundcork itself listens on `8000`; the nginx ETag sidecar listens on `8001` and proxies through (some Bose API behavior depends on ETag handling). **Use port `8001` for `BASE_URL`** if you keep the sidecar; use `8000` if you remove it.

### Docker development mode

```bash
DEV_MODE=true docker compose up --build
```

The entrypoint runs `fastapi dev` instead of `gunicorn`, so file changes inside the container reload automatically. Combine with a bind-mount of `./soundcork:/app/soundcork` (not included in the default compose) if you want host-side edits to trigger reloads:

```yaml
    volumes:
      - ./data:/soundcork/data
      - ./logs:/soundcork/logs
      - ./soundcork:/app/soundcork:ro      # dev-only
```

For Kubernetes and other deployment shapes, see [docs/deployment.md](docs/deployment.md).

---

## Preparing the USB stick (enabling shell access on a speaker)

Before soundcork can configure a speaker, the speaker has to be willing to give you a shell. SoundTouch firmware enables telnet/SSH only when it sees a specific file on a freshly plugged USB drive at boot. On firmware 27.x, the older TAP-console `remote_services on` command is no longer available, so the USB method is the supported path.

> ⚠️ **Do not experiment with port 17000 / TAP console commands.** Some TAP demo commands, including `enter`, can put a speaker into factory/demo mode. Soundcork does not need port 17000.

You need:

- A USB stick formatted as **FAT32** (or **vfat**)
- A single, **empty** file at the root of the stick named exactly **`remote_services`** — no extension, no contents

The filename is case-sensitive. Watch out for editors that silently append `.txt`. Keep the USB root clean: hidden files and metadata directories can prevent detection on some firmware versions.

### Windows

1. Plug in the USB stick. Open *This PC* → right-click the drive → **Format…**
2. **File system:** FAT32. **Allocation unit size:** default. Click **Start**.
3. Open the now-empty drive. **View** → tick **File name extensions** (so Windows can't hide a `.txt` ending from you).
4. Right-click in the empty space → **New** → **Text Document**. Rename the new file to `remote_services` exactly (you'll get a warning about removing the extension — confirm yes).
5. Delete any extra files or folders Windows may create at the root. The only visible file should be `remote_services`.
6. Eject the drive cleanly.

To verify, open PowerShell and run `Get-ChildItem -Force <drive>:\` — you should see `remote_services` as a 0-byte file.

If anything else appears, remove it before ejecting:

```powershell
Get-ChildItem -Force E:\ | Where-Object Name -ne 'remote_services' | Remove-Item -Recurse -Force
```

Replace `E:` with the USB drive letter.

### macOS

1. Plug in the USB stick. Open **Disk Utility** → select the **drive** (not just the partition) → **Erase**.
2. **Format:** `MS-DOS (FAT)`. **Scheme:** `Master Boot Record`. Click **Erase**.
3. In Terminal:
   ```sh
   mdutil -i off /Volumes/UNTITLED
   touch /Volumes/UNTITLED/remote_services
   rm -rf /Volumes/UNTITLED/.fseventsd /Volumes/UNTITLED/.Spotlight-V100
   find /Volumes/UNTITLED -name '._*' -delete
   ls -la /Volumes/UNTITLED/                # confirm: -rw-r--r--  remote_services  0
   diskutil eject /Volumes/UNTITLED
   ```
   (Replace `UNTITLED` with whatever you named the volume.)

### Linux

```sh
# find the device — make ABSOLUTELY SURE it's the USB stick, not your /home drive
lsblk
# then format:
sudo mkfs.vfat -F 32 -n SOUNDCORK /dev/sdX1
# mount (most desktops auto-mount; if not):
mkdir -p /tmp/usb && sudo mount /dev/sdX1 /tmp/usb
sudo touch /tmp/usb/remote_services
sudo find /tmp/usb -mindepth 1 ! -name remote_services -exec rm -rf {} +
ls -la /tmp/usb                         # confirm only remote_services is present
sudo umount /tmp/usb
```

### Activating shell access on the speaker

1. **Unplug** the speaker from power.
2. Insert the prepared USB stick into the USB port on the back.
3. Plug power back in. Wait for the speaker to fully boot (the front display will go through its normal startup).
4. You can leave the stick in or unplug it.
5. From any machine on the same LAN:
   ```sh
   ssh root@<speaker-ip>      # password is empty — just press Enter
   ```
   The older firmware ships an OpenSSH that may reject modern clients. On macOS and other modern OpenSSH clients, this may be required:
   ```sh
   ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<speaker-ip>
   ```
   If `ssh` still refuses, fall back to telnet:
   ```sh
   telnet <speaker-ip>        # user: root, password: (empty)
   ```

### Making shell access persistent

After logging in once, run this command on the speaker:

```sh
touch /mnt/nv/remote_services
```

That keeps shell access enabled across future reboots without leaving the USB stick inserted.

If `/admin` still shows **Reachable: No** after the USB boot, retry with a freshly formatted USB stick and no extra root files. If that still fails, connect the speaker via Ethernet for the initial setup; this has helped during firmware 27.0.6 testing, though the exact cause may be USB detection, network reachability, or both.

Once a speaker shows **"Reachable: Yes"** on `/admin`, soundcork can finish onboarding it for you with one click. If more than one Soundcork account exists and the speaker does not report a `margeAccountUUID`, the admin UI asks which account should own the speaker.

---

## What soundcork changes on your speaker

For normal onboarding, Soundcork writes **one override file** to the speaker, leaves the original Bose config untouched, and reboots once.

| File on the speaker | Owner | Soundcork action |
|---|---|---|
| `/opt/Bose/etc/SoundTouchSdkPrivateCfg.xml` | Bose firmware (original config) | **Never modified.** |
| `/mnt/nv/OverrideSdkPrivateCfg.xml` | Persistent override read by the SDK if present | **Created** by the admin "Switch to Soundcork" action. Points the four cloud URLs (marge, stats, swUpdate, bmxRegistry) at your soundcork host. |
| `/mnt/nv/BoseApp-Persistence/1/Sources.xml` | Speaker's local source/credential database | **Read only**, copied into soundcork's `DATA_DIR` so soundcork can serve the same source list back to the device. Never written. |
| Speaker Marge account UUID | Speaker NVRAM | **Only updated during repair/adoption** when the speaker has no `margeAccountUUID`; Soundcork posts `/setMargeAccount` so future `/info` calls report the chosen account. |

The override file soundcork writes looks like this (with `{SC_BASE_URL}` substituted from your `BASE_URL`):

```xml
<SoundTouchSdkPrivateCfg>
    <margeServerUrl>{SC_BASE_URL}/marge</margeServerUrl>
    <statsServerUrl>{SC_BASE_URL}</statsServerUrl>
    <swUpdateUrl>{SC_BASE_URL}/updates/soundtouch</swUpdateUrl>
    <bmxRegistryUrl>{SC_BASE_URL}/bmx/registry/v1/services</bmxRegistryUrl>
    <isZeroconfEnabled>true</isZeroconfEnabled>
    <usePandoraProductionServer>true</usePandoraProductionServer>
    <saveMargeCustomerReport>false</saveMargeCustomerReport>
</SoundTouchSdkPrivateCfg>
```

The presence of `OverrideSdkPrivateCfg.xml` makes the SoundTouch SDK use those URLs instead of the ones baked into firmware.

> Earlier versions of this guide told you to edit `SoundTouchSdkPrivateCfg.xml` directly. **Don't** — a malformed file there can put the speaker into a reboot loop that only a firmware update will recover from. The override file is the safe path (h/t [Ueberbose](https://github.com/julius-d/ueberboese-api) for finding this).

### Reverting a speaker to Bose

Use **Remove from Soundcork** in `/admin` for a configured speaker, or **Reset to Bose** for an orphaned speaker that is pointed at Soundcork but missing from the datastore. Both delete the override file over SSH and reboot when the speaker is reachable.

Manually, delete the override file and reboot. The speaker will fall back to the original firmware config and behave exactly as it did before soundcork. Because Bose cloud support ended on May 6, 2026, this does not restore Bose cloud features; it only removes soundcork's override cleanly.

```sh
ssh root@<speaker-ip>
rm /mnt/nv/OverrideSdkPrivateCfg.xml
reboot
```

If you made shell access persistent with `/mnt/nv/remote_services`, remove that marker and reboot:

```sh
ssh root@<speaker-ip>
rm /mnt/nv/remote_services
reboot
```

Keep speaker SSH/telnet reachable only from your LAN. Removing the override is the important step for reverting soundcork behavior; removing the `remote_services` marker only affects whether shell access remains available after reboot.

---

## Where to learn more

- [docs/deployment.md](docs/deployment.md) — production deployment patterns, Kubernetes, base-URL diagnostics
- [docs/API_Spec.md](docs/API_Spec.md) — reverse-engineered Bose API notes
- [docs/Shutdown_Emulation.md](docs/Shutdown_Emulation.md) — what each Bose endpoint actually does
- [docs/spotify.md](docs/spotify.md) — Spotify OAuth setup
- [Bose SoundTouch cloud service page](https://www.bose.com/soundtouch-end-of-life) — official end-of-service status
- [Project wiki](https://github.com/deborahgu/soundcork/wiki/) — developer guidelines
- [Current Bose cloud status thread](https://github.com/deborahgu/soundcork/discussions/181)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [project milestones](https://github.com/deborahgu/soundcork/milestones).
