@echo off
echo Starting Biplane Web Viewer dev environment...
echo.

REM 1. Start Redis via Docker
echo [1/3] Starting Redis...
docker start redis 2>nul || docker run -d --name redis -p 6379:6379 redis:7-alpine
echo Redis started.
echo.

REM 2. Start Celery in a new window
echo [2/3] Starting Celery worker...
start "Celery Worker" cmd /k "cd /d D:\Vasolab\3d_viewer\biplane_web && celery -A biplane_web worker --concurrency=4 --loglevel=info -P threads"
echo.

REM 3. Start Django in a new window
echo [3/3] Starting Django...
start "Django Server" cmd /k "cd /d D:\Vasolab\3d_viewer\biplane_web && python manage.py runserver"
echo.

echo All services started!
echo Open http://127.0.0.1:8000 in your browser.
pause
