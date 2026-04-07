# Biplane Viewer Web

Web-based 3D biplane fluoroscopy viewer built with Django Channels, Celery, and Three.js.  
Connects to an Orthanc PACS server to search, download, and render biplane frame stacks in the browser.

---

## Architecture

```
Browser (Three.js 3D viewer)
    ↕ HTTP + WebSocket
Django / Daphne (ASGI)
    ↕
Celery Worker  ──→  Orthanc PACS
    ↕
Redis (frame cache + task broker + WebSocket channel layer)
```

- **Daphne** — ASGI server, handles both HTTP and WebSocket (live progress)
- **Celery** — background worker that downloads and decodes frames from Orthanc
- **Redis** — three roles: frame cache (DB 1), Celery broker (DB 2), WebSocket channel layer (DB 0)
- **Three.js** — renders the 3D transverse stack + sagittal plane in the browser

---

## Requirements

- Python 3.11+
- Redis 7+
- Orthanc PACS (accessible from the server)

---

## Deployment

### 1. Clone the repo

```bash
git clone https://github.com/lEuvill/biplane_viewer_web.git
cd biplane_viewer_web
```

### 2. Create virtual environment and install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in all required values (see `.env.example` for descriptions):

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✓ | Django secret key — generate with command below |
| `ORTHANC_URL` | ✓ | Orthanc base URL e.g. `http://127.0.0.1:8042` |
| `ORTHANC_USER` | ✓ | Orthanc username |
| `ORTHANC_PASS` | ✓ | Orthanc password |
| `ALLOWED_HOSTS` | ✓ | Comma-separated server IPs/domains |
| `CSRF_TRUSTED_ORIGINS` | ✓ | Full origin URLs e.g. `https://your-domain.com` |
| `REDIS_URL` | — | Defaults to `redis://127.0.0.1:6379` |
| `FRAME_TTL` | — | Frame cache lifetime in seconds, default `86400` (24 h) |
| `DEBUG` | — | Set `True` only for local development |

Generate a secret key:
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 4. One-time setup

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

### 5. Start services

You need three processes running concurrently. Use systemd, supervisor, or separate terminals.

**Redis** (if not already running as a system service):
```bash
redis-server
```

**Celery worker:**
```bash
celery -A biplane_web worker --concurrency=4 --loglevel=info -P threads
```

**Django / Daphne (ASGI):**
```bash
daphne -b 0.0.0.0 -p 8000 biplane_web.asgi:application
```

Open `http://<server-ip>:8000` in the browser.

---

## Systemd example (Ubuntu/Debian)

Create `/etc/systemd/system/biplane-daphne.service`:
```ini
[Unit]
Description=Biplane Viewer — Daphne ASGI
After=network.target redis.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/biplane_viewer_web
EnvironmentFile=/home/ubuntu/biplane_viewer_web/.env
ExecStart=/home/ubuntu/biplane_viewer_web/venv/bin/daphne -b 0.0.0.0 -p 8000 biplane_web.asgi:application
Restart=always

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/biplane-celery.service`:
```ini
[Unit]
Description=Biplane Viewer — Celery Worker
After=network.target redis.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/biplane_viewer_web
EnvironmentFile=/home/ubuntu/biplane_viewer_web/.env
ExecStart=/home/ubuntu/biplane_viewer_web/venv/bin/celery -A biplane_web worker --concurrency=4 --loglevel=info -P threads
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable biplane-daphne biplane-celery
sudo systemctl start biplane-daphne biplane-celery
```

---

## Nginx reverse proxy (optional but recommended for HTTPS)

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> Static files are served by WhiteNoise directly from Django — no separate `location /static/` block needed.

---

## Pages

| URL | Description |
|---|---|
| `/` | Patient search + study/recording selection |
| `/viewer/<study_id>/` | 3D viewer for a loaded study |

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/search/` | POST | Search patients on Orthanc |
| `/api/instances/<study_id>/` | GET | List biplane recording instances |
| `/api/load/<study_id>/` | POST | Enqueue download + decode task |
| `/api/status/<study_id>/` | GET | Check cache/task status |
| `/api/frames/<study_id>/<frame>/<plane>/` | GET | Serve a decoded RGBA PNG frame |
| `/api/preview/<study_id>/<plane>/` | GET | Serve a small preview JPEG |
| `/ws/progress/<job_id>/` | WS | Live download/decode progress |
