from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi import HTTPException, Query

from common.runtime import ROOT, load_config, make_app, now
from common.schemas import Measurement

config = load_config('collector')
app = make_app('collector', config)
cache: dict[str, list[dict]] = {}


def source(source_id: str):
    if source_id not in config['sources']:
        raise HTTPException(404, 'Источник данных не найден')
    return config['sources'][source_id]


def records(source_id: str):
    item = source(source_id)
    if source_id not in cache:
        path = ROOT / item['path']
        if not path.is_file():
            raise HTTPException(503, 'Файл источника отсутствует')
        with path.open(encoding='utf-8', newline='') as stream:
            cache[source_id] = [Measurement(timestamp=r['timestamp'], sensor=r['sensor'],
                                           value=float(r['value']) if r['value'] else None,
                                           label=int(r['label']) if r.get('label') else None).model_dump()
                                for r in csv.DictReader(stream)]
    return cache[source_id]


@app.get('/sources')
def sources():
    result = []
    for source_id, item in config['sources'].items():
        manifest = json.loads((ROOT / item['manifest']).read_text(encoding='utf-8'))
        result.append({'id': source_id, 'name': item['name'], **manifest})
    return result


@app.get('/sources/{source_id}/batch')
def batch(source_id: str, offset: int = Query(0, ge=0), limit: int = Query(5000, ge=1, le=10000)):
    rows = records(source_id)
    if offset > len(rows):
        raise HTTPException(400, 'Смещение превышает размер источника')
    selected = rows[offset:offset + limit]
    app.state.logger.info(json.dumps({'time': now(), 'event': 'batch', 'source_id': source_id,
                                     'offset': offset, 'rows': len(selected)}))
    return {'source_id': source_id, 'offset': offset, 'next_offset': offset + len(selected),
            'total': len(rows), 'done': offset + len(selected) >= len(rows), 'records': selected}
