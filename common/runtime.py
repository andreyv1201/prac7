from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def load_config(service: str) -> dict:
    path = Path(os.environ.get('SERVICE_CONFIG', ROOT / 'configs' / f'{service}.json'))
    config = json.loads(path.read_text(encoding='utf-8'))
    config['state_dir'] = os.environ.get('STATE_DIR', config.get('state_dir', f'runtime/{service}'))
    for key in ('storage_url', 'collector_url', 'ml_url', 'master_url'):
        config[key] = os.environ.get(key.upper(), config.get(key, ''))
    return config


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def make_app(service: str, config: dict) -> FastAPI:
    state_dir = Path(config['state_dir'])
    state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f'kontur.{service}')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(state_dir / 'requests.jsonl', encoding='utf-8')
        logger.addHandler(handler)
    app = FastAPI(title=f'Контур / {service}', version='1.0.0')
    app.state.logger = logger

    @app.middleware('http')
    async def request_log(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception('Unhandled service error')
            return JSONResponse(status_code=500, content={'detail': 'Внутренняя ошибка сервиса. Подробности в журнале.'})
        logger.info(json.dumps({'time': now(), 'method': request.method, 'path': request.url.path,
                                'status': response.status_code,
                                'ms': round((time.perf_counter() - started) * 1000, 1)}, ensure_ascii=False))
        return response

    @app.get('/health', tags=['Состояние'])
    def health():
        return {'service': service, 'status': 'ok', 'time': now()}

    return app


def remote(method: str, base: str, path: str, **kwargs):
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=5), trust_env=False) as client:
            response = client.request(method, base.rstrip('/') + path, **kwargs)
    except httpx.RequestError as error:
        raise HTTPException(503, 'Сервис временно недоступен. Проверьте состояние системы.') from error
    if response.is_error:
        try:
            detail = response.json().get('detail', response.text)
        except ValueError:
            detail = response.text[:300]
        raise HTTPException(response.status_code, detail)
    return response.json()
