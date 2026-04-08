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

| Component | Role |
|---|---|
| **Daphne** | ASGI server — handles HTTP requests and WebSocket connections for live progress |
| **Celery** | Background worker — downloads DICOM files from Orthanc and decodes frames in parallel |
| **Redis DB 0** | Django Channels layer (WebSocket routing) |
| **Redis DB 1** | Frame cache — stores decoded RGBA PNGs keyed by `cache_id` |
| **Redis DB 2** | Celery broker and result backend |
| **SQLite** | Stores `SharedStudy` records — maps `cache_id` → instance IDs so shared links can auto-reload after the Redis cache expires |
| **Three.js** | Browser-side 3D rendering — image planes stacked in 3D space |

---

## Requirements

- Python 3.11+
- Redis 7+
- Orthanc PACS server reachable from this machine
- ~500 MB–2 GB RAM in Redis depending on study size and TTL (decoded frames are ~300×300 RGBA PNGs cached per frame per plane)

---

## First-time Deployment

### 1. Clone

```bash
git clone https://github.com/lEuvill/biplane_viewer_web.git
cd biplane_viewer_web
```

### 2. Python environment

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment variables

```bash
cp .env.example .env
nano .env                       # fill in all required values
```

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✓ | Django secret key — generate with the command below |
| `ORTHANC_URL` | ✓ | Orthanc base URL e.g. `http://127.0.0.1:8042` |
| `ORTHANC_USER` | ✓ | Orthanc username |
| `ORTHANC_PASS` | ✓ | Orthanc password |
| `ALLOWED_HOSTS` | ✓ | Comma-separated server IPs/hostnames e.g. `10.0.0.5,biplane.example.com` |
| `CSRF_TRUSTED_ORIGINS` | ✓ (HTTPS) | Full origin URLs e.g. `https://biplane.example.com` — required when running behind a reverse proxy |
| `REDIS_URL` | — | Defaults to `redis://127.0.0.1:6379` |
| `FRAME_TTL` | — | Frame cache lifetime in seconds. Default `86400` (24 h). Lower if Redis RAM is limited. |
| `DEBUG` | — | `True` for local dev only. **Must be `False` in production.** |

Generate a secret key:
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 4. One-time database and static file setup

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

> `migrate` creates the `SharedStudy` table in `db.sqlite3`. This table maps cache IDs to Orthanc instance IDs so that shared links and recent studies can auto-reload frames after the Redis cache expires. **Do not skip this step.**

### 5. Start all three services

You need three processes running concurrently. In production use systemd (see below). For local dev, open three terminals:

**Terminal 1 — Redis** (skip if already running as a system service):
```bash
redis-server
```

**Terminal 2 — Celery worker:**
```bash
celery -A biplane_web worker --concurrency=4 --loglevel=info -P threads
```

> `-P threads` uses threads instead of processes, which avoids multiprocessing spawn issues on some platforms. `--concurrency=4` controls how many frames decode in parallel — increase on machines with more cores.

**Terminal 3 — Django / Daphne:**
```bash
daphne -b 0.0.0.0 -p 8000 biplane_web.asgi:application
```

Open `http://<server-ip>:8000` in the browser.

---

## Updating (after git pull)

```bash
git pull
pip install -r requirements.txt      # pick up any new dependencies
python manage.py migrate             # apply any new DB migrations
python manage.py collectstatic --noinput
# then restart both services:
sudo systemctl restart biplane-celery biplane-daphne
```

> **Always restart the Celery worker after a code update.** It imports modules at startup and caches them — it will not pick up changes until restarted.

---

## Systemd (Ubuntu/Debian production)

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

## Nginx reverse proxy (recommended for HTTPS)

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

> Static files are served by **WhiteNoise** directly from Django — no separate `location /static/` block needed.

When running behind Nginx with HTTPS, make sure `.env` has:
```
CSRF_TRUSTED_ORIGINS=https://your-domain.com
ALLOWED_HOSTS=your-domain.com
DEBUG=False
```

---

## Pages

| URL | Description |
|---|---|
| `/` | Patient search + study/recording selection |
| `/viewer/<study_id>/` | Full 3D viewer with control panel |
| `/share/<study_id>/` | Display-only viewer for shared links — no control panel |

Both viewer URLs accept a `cache_id` query parameter (e.g. `/viewer/abc/?cache_id=abc:1a2b3c4d`) and an optional `job_id` for in-progress loads.

---

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/search/` | POST | Search patients on Orthanc by name |
| `/api/instances/<study_id>/` | GET | List biplane recording instances for a study |
| `/api/load/<study_id>/` | POST | Enqueue download + decode task; returns `cache_id` and `job_id` |
| `/api/status/<study_id>/` | GET | Check cache/task status (`ready`, `loading`, `error`) |
| `/api/frames/<study_id>/<frame>/<plane>/` | GET | Serve a decoded RGBA PNG frame (`plane`: `trans` or `sag`) |
| `/api/preview/<study_id>/<plane>/` | GET | Serve a 160×160 JPEG preview of frame 0 |
| `/ws/progress/<job_id>/` | WS | WebSocket — live download/decode progress messages |

### `POST /api/load/<study_id>/` body

```json
{
  "instance_ids": ["orthanc-id-1", "orthanc-id-2"],
  "swapped": false
}
```

| Field | Type | Description |
|---|---|---|
| `instance_ids` | `string[]` | Orthanc instance IDs to include. Empty array = load all instances in the study. |
| `swapped` | `bool` | `true` to swap transverse/sagittal planes. Used when the DICOM image stores the planes in reverse order (sagittal on top, transverse on bottom). Default `false`. |

Returns:
```json
{ "status": "loading", "job_id": "...", "cache_id": "..." }
// or, if already cached:
{ "status": "ready", "job_id": "...", "cache_id": "..." }
```

---

## How frames are processed

Each biplane DICOM instance contains frames where the image is split vertically:
- **Top half** → transverse plane (default)
- **Bottom half** → sagittal plane (default)

When `swapped=true` the halves are flipped. The Celery worker decodes frames using a `ProcessPoolExecutor` for parallel throughput, normalises pixel values per-frame (min-max to 0–255), and stores 300×300 RGBA PNGs in Redis with dark pixels made transparent for Three.js compositing.

The sagittal half is also analysed for a blue cursor line (depth marker) to set the initial sagittal Z position in the viewer.

---

## Viewer features

### Full viewer (`/viewer/`)
- **Display mode** — Stack (all frames as layered image planes) or Single frame
- **Opacity & tint** — Independent opacity and colour tint for transverse stack and sagittal plane
- **Sagittal controls** — Z offset, Y depth, clip distance, hide/show toggle, reset button
- **Camera presets** — Perspective, Transverse face-on, Sagittal face-on, Top-down; plus 9 custom save slots (right-click to manage)
- **ViewCube** — Clickable 3D orientation cube in the corner; click any face or corner to snap the camera
- **Playback** — Play/Stop, Prev/Next buttons and frame scrubber; FPS slider (4–60)
- **ROI mask** — Draw a polygon on the transverse plane to suppress background regions
- **Export** — Exports the frame loop as a WebM video file
- **Share link** — Copies a `/share/` URL to clipboard for read-only external sharing

### Shared viewer (`/share/`)
Read-only viewer for external sharing. Has no control panel — only:
- Full 3D canvas with ViewCube
- Play/Prev/Next buttons and frame scrubber
- Display mode toggle (Stack / Single)
- FPS slider

### Search page (`/`)
- Patient name search against Orthanc
- Per-recording instance selection with frame counts and instance numbers
- Live preview thumbnails (transverse and sagittal) for selected recordings
- **Swap planes button** — swaps the transverse/sagittal assignment before loading, for studies where the DICOM image halves are stored in reverse order
- Recent studies list with Redis cache status indicators (green = cached, blue = will auto-reload)
- Bookmarks — persisted in browser localStorage
- Right-click context menu on recent/bookmark entries: open, reveal in search, copy viewer link, bookmark/remove

---

## Troubleshooting

**Viewer immediately redirects back to search**
- The Redis frame cache has expired and the `SharedStudy` DB record is missing (e.g. fresh database).
- Fix: select the study again from the search page and reload it. Recent entries now auto-trigger a re-download via `/api/load/` when cache is expired.

**`TypeError: load_study_task() takes from N to M positional arguments`**
- The Celery worker is running stale code. Restart it: `sudo systemctl restart biplane-celery`.

**Progress bar hangs / WebSocket never connects**
- Check that Daphne (not gunicorn/uwsgi) is serving the app — only Daphne handles WebSocket.
- Verify Nginx passes `Upgrade` and `Connection` headers (see Nginx config above).
- Check Redis is running: `redis-cli ping` should return `PONG`.

**Frames decode but look black / fully transparent**
- Pixel normalisation is per-frame min-max. If a frame is entirely black (blank image) it will remain transparent — this is expected.
- Dark cutoff threshold is `DARK_CUTOFF = 15` in `viewer/frame_processor.py`.

**CSRF errors on POST requests**
- Set `CSRF_TRUSTED_ORIGINS` in `.env` to include the full origin (scheme + domain) of your server.
- Make sure `DEBUG=False` and the Nginx `X-Forwarded-Proto` header is set.

**Static files returning 404**
- Run `python manage.py collectstatic --noinput` and restart Daphne.
