from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from common.runtime import ROOT, load_config, make_app

config = load_config('visualization')
app = make_app('visualization', config)
static = ROOT / 'visualization' / 'static'
app.mount('/assets', StaticFiles(directory=static), name='assets')


@app.get('/')
def index():
    return FileResponse(static / 'index.html', headers={'Cache-Control': 'no-cache'})


@app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PATCH', 'DELETE', 'PUT'])
async def proxy(path: str, request: Request):
    if request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > 15500000:
        return JSONResponse(status_code=413, content={'detail': 'Размер файла превышает 15 МБ'})
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 15500000:
            return JSONResponse(status_code=413, content={'detail': 'Размер файла превышает 15 МБ'})
        chunks.append(chunk)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5), trust_env=False) as client:
            response = await client.request(request.method, config['master_url'] + '/api/' + path,
                                            params=request.query_params, content=b''.join(chunks),
                                            headers={'content-type': request.headers.get('content-type', 'application/json')})
    except httpx.RequestError:
        return JSONResponse(status_code=503, content={'detail': 'Главный сервис недоступен. Откройте состояние системы.'})
    headers = {key: response.headers[key] for key in ('content-type', 'content-disposition') if key in response.headers}
    return Response(response.content, status_code=response.status_code, headers=headers)
