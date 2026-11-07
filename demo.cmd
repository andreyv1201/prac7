@echo off
cd /d "%~dp0"
docker compose exec -T master python scripts/demo.py --base http://visualization:8000 --all-sensors
pause
