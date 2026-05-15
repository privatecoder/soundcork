# Deployment Guide

Four ways to run SoundCork, from simplest to most customizable.

## ⚠️ Critical: `BASE_URL` must be reachable from your speakers

`BASE_URL` is the URL that **your Bose speakers** use to talk to soundcork. The speakers receive this URL during configuration and use it for every API call (Marge, BMX registry, source token authentication, etc.).

If the speakers cannot resolve or reach the URL, sources like **TUNEIN, INTERNET_RADIO, and LOCAL_INTERNET_RADIO will silently fail to activate** at boot. The symptom is `UNKNOWN_SOURCE_ERROR` (code 1005) when trying to play a preset — even though the source is correctly listed in the device's `Sources.xml`.

**Do not use:**

- `http://localhost:8000` or `http://127.0.0.1:8000` — speakers cannot reach loopback addresses on the host
- `http://soundcork:8000` or any container name — speakers are not part of your Docker network
- A hostname that only resolves inside Kubernetes / Docker

**Use one of:**

- The host's **LAN IP address**: `http://192.168.1.50:8000`
- A **DNS name resolvable on the speaker network**: `http://soundcork.lan:8000`
- A **publicly routable** hostname behind your reverse proxy: `https://soundcork.example.com`

After changing `BASE_URL`, you must **re-run "Switch to Soundcork"** in the admin UI for each speaker (this rewrites `OverrideSdkPrivateCfg.xml` on the speaker and reboots it). The admin page (`/admin`) shows a warning when `BASE_URL` looks misconfigured for your detected devices.

## Option 1: Docker (Simplest)

```bash
docker run -d --name soundcork \
  --network host \
  -v /path/to/your/data:/soundcork/data \
  -e BASE_URL=http://192.168.1.50:8000 \
  -e DATA_DIR=/soundcork/data \
  ghcr.io/deborahgu/soundcork:main
```

> Replace `192.168.1.50` with your host's LAN IP — the address your speakers can reach. Host networking is recommended because UPnP discovery and SoundTouch callbacks need LAN visibility.

## Option 2: Docker Compose

Create a `docker-compose.yml`:

```yaml
services:
  soundcork:
    image: ghcr.io/deborahgu/soundcork:main
    network_mode: host
    environment:
      # Must be reachable from your speakers - use your host's LAN IP, not localhost
      - BASE_URL=http://192.168.1.50:8000
      - DATA_DIR=/soundcork/data
      # Optional: log 404/unhandled requests for debugging
      - UNHANDLED_LOG_DIR=/soundcork/logs/traffic
    volumes:
      - ./data:/soundcork/data
      - ./logs:/soundcork/logs
    restart: unless-stopped
```

Then run:

```bash
docker compose up -d
```

## Option 3: Kubernetes

Customize the hostname, volume paths, and ingress controller for your environment.

### Namespace

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: soundcork
```

### Deployment

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: soundcork
  namespace: soundcork
spec:
  replicas: 1
  selector:
    matchLabels:
      app: soundcork
  template:
    metadata:
      labels:
        app: soundcork
    spec:
      containers:
        - name: soundcork
          image: ghcr.io/deborahgu/soundcork:main
          ports:
            - containerPort: 8000
          env:
            # BASE_URL must be reachable from your speakers (not from inside the cluster)
            - name: BASE_URL
              value: "https://soundcork.example.com"
            - name: DATA_DIR
              value: "/soundcork/data"
            # Optional: log 404/unhandled requests for debugging
            - name: UNHANDLED_LOG_DIR
              value: "/soundcork/logs/traffic"
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
          volumeMounts:
            - name: data
              mountPath: /soundcork/data
            - name: logs
              mountPath: /soundcork/logs
      volumes:
        - name: data
          hostPath:
            path: /srv/soundcork/data
            type: DirectoryOrCreate
        - name: logs
          hostPath:
            path: /srv/soundcork/logs
            type: DirectoryOrCreate
```

> **Note:** This example uses `hostPath` volumes. Adapt the volume type for your cluster (e.g., `persistentVolumeClaim`, NFS, or a CSI driver).

### Service

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: soundcork
  namespace: soundcork
spec:
  type: ClusterIP
  selector:
    app: soundcork
  ports:
    - port: 8000
      targetPort: 8000
```

### Ingress

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: soundcork
  namespace: soundcork
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt
spec:
  tls:
    - hosts:
        - soundcork.example.com
      secretName: soundcork-tls
  rules:
    - host: soundcork.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: soundcork
                port:
                  number: 8000
```

> **Note:** This setups is for Traefik IngressRoute. Adapt the ingress for your controller (nginx, Traefik, etc.).

### Apply

```bash
kubectl apply -f namespace.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f ingress.yaml
```

## Option 4: Bare Metal

### Prerequisites

- Python 3.12

### Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### Running

**Development:**

```bash
fastapi dev soundcork/main.py
```

**Production:**

```bash
gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 soundcork.main:app
```

### Systemd

A sample unit file is included at `soundcork.service.example`. Copy it to `/etc/systemd/system/soundcork.service`, adjust paths and user, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now soundcork
```

> **Note:** Make sure `PYTHONPATH` includes the project root and that the working directory is set correctly in your service file or shell environment.

## Container Image

- **Image:** `ghcr.io/deborahgu/soundcork:main`
- **Multi-architecture:** `linux/amd64` + `linux/arm64` (works on Raspberry Pi)
- Built automatically via GitHub Actions on every push to main
- Source: see `.github/workflows/docker-publish.yml`

## Verifying It Works

```bash
curl http://your-server:8000/
# Expected: {"Bose":"Can't Brick Us"}
```

After redirecting your speaker (see [Speaker Setup](speaker-setup.md)), you should see incoming requests in the server logs.
