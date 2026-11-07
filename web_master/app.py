from __future__ import annotations

import csv
import html
import io
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from common.runtime import load_config, make_app, now, remote
from common.schemas import AnalyzeRequest, DatasetUpdate, ImportRequest, Measurement, TrainRequest

config = load_config('web_master')
app = make_app('web_master', config)


def storage(method, path, **kwargs):
    return remote(method, config['storage_url'], path, **kwargs)


@app.get('/api/sources')
def sources():
    return remote('GET', config['collector_url'], '/sources')


@app.get('/api/datasets')
def datasets():
    return storage('GET', '/datasets')


@app.patch('/api/datasets/{dataset_id}')
def rename_dataset(dataset_id: str, body: DatasetUpdate):
    return storage('PATCH', f'/datasets/{dataset_id}', json=body.model_dump())


@app.delete('/api/datasets/{dataset_id}')
def delete_dataset(dataset_id: str):
    return storage('DELETE', f'/datasets/{dataset_id}')


@app.get('/api/datasets/{dataset_id}/records')
def dataset_records(dataset_id: str, sensor: str | None = None, start: str | None = None, end: str | None = None):
    params = {k: v for k, v in {'sensor': sensor, 'start': start, 'end': end, 'limit': 100000}.items() if v is not None}
    return storage('GET', f'/datasets/{dataset_id}/records', params=params)


@app.post('/api/import')
def import_data(body: ImportRequest):
    available = {s['id']: s for s in sources()}
    if body.source_id not in available:
        raise HTTPException(404, 'Источник не найден')
    created = body.dataset_id is None
    dataset = storage('POST', '/datasets', json={'name': available[body.source_id]['name'], 'source_id': body.source_id}) \
        if created else storage('GET', f'/datasets/{body.dataset_id}')
    if dataset['source_id'] != body.source_id:
        raise HTTPException(422, 'Набор создан из другого источника')
    offset, loaded = dataset['source_offset'], 0
    try:
        while True:
            batch = remote('GET', config['collector_url'], f'/sources/{body.source_id}/batch',
                           params={'offset': offset, 'limit': config['batch_size']})
            if batch['records']:
                storage('POST', f"/datasets/{dataset['id']}/records",
                        json={'records': batch['records'], 'source_offset': batch['next_offset']})
                loaded += len(batch['records'])
            offset = batch['next_offset']
            if batch['done'] or not body.all_batches:
                break
    except HTTPException:
        if created:
            storage('DELETE', f"/datasets/{dataset['id']}")
        raise
    return {'dataset': storage('GET', f"/datasets/{dataset['id']}"), 'loaded': loaded, 'done': batch['done']}


@app.post('/api/upload', status_code=201)
async def upload(file: UploadFile = File(...)):
    raw = await file.read(config['max_upload_bytes'] + 1)
    await file.close()
    if len(raw) > config['max_upload_bytes']:
        raise HTTPException(413, 'Размер файла превышает 15 МБ')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise HTTPException(422, 'Сохраните CSV в кодировке UTF-8') from error
    reader = csv.DictReader(io.StringIO(text))
    required = {'timestamp', 'sensor', 'value'}
    if not required.issubset(reader.fieldnames or []):
        raise HTTPException(422, 'Нужны столбцы timestamp,sensor,value; label — необязательный столбец 0/1')
    records, keys = [], set()
    try:
        for row_number, row in enumerate(reader, 2):
            if len(records) >= 100000:
                raise HTTPException(413, 'Допускается до 100 000 строк')
            record = Measurement(timestamp=row['timestamp'], sensor=row['sensor'],
                                 value=float(row['value']) if row['value'] else None,
                                 label=int(row['label']) if row.get('label') else None)
            key = (record.sensor, record.timestamp)
            if key in keys:
                raise ValueError('Повтор датчика и временной отметки')
            keys.add(key)
            records.append(record.model_dump())
    except (ValueError, TypeError, ValidationError) as error:
        raise HTTPException(422, f'Ошибка CSV в строке {row_number}: {str(error)[:240]}') from error
    if not records:
        raise HTTPException(422, 'В файле нет измерений')
    dataset = storage('POST', '/datasets', json={'name': (file.filename or 'CSV')[:120]})
    try:
        for offset in range(0, len(records), 5000):
            storage('POST', f"/datasets/{dataset['id']}/records", json={'records': records[offset:offset + 5000]})
    except HTTPException:
        storage('DELETE', f"/datasets/{dataset['id']}")
        raise
    return storage('GET', f"/datasets/{dataset['id']}")


@app.get('/api/models')
def models():
    return remote('GET', config['ml_url'], '/models')


@app.get('/api/models/{model_id}')
def model(model_id: str):
    return remote('GET', config['ml_url'], f'/models/{model_id}')


@app.post('/api/train', status_code=202)
def train(body: TrainRequest):
    result = remote('POST', config['ml_url'], '/models', json=body.model_dump())
    return remote('POST', config['ml_url'], f"/models/{result['id']}/train")


@app.post('/api/analyze')
def analyze(body: AnalyzeRequest):
    return remote('POST', config['ml_url'], f'/models/{body.model_id}/predict',
                  json=body.model_dump(exclude={'model_id'}))


@app.get('/api/runs')
def runs(dataset_id: str | None = None):
    return storage('GET', '/runs', params={'dataset_id': dataset_id} if dataset_id else {})


@app.get('/api/runs/{run_id}')
def run(run_id: str):
    return storage('GET', f'/runs/{run_id}')


@app.get('/api/system')
def system():
    def check(item):
        name, url = item
        try:
            return remote('GET', url, '/health')
        except HTTPException:
            return {'service': name, 'status': 'unavailable'}
    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses = list(pool.map(check, [('collector', config['collector_url']),
                                       ('storage', config['storage_url']), ('ml_service', config['ml_url'])]))
    statuses.append({'service': 'web_master', 'status': 'ok', 'time': now()})
    return statuses


@app.get('/api/runs/{run_id}/csv')
def download_csv(run_id: str):
    result = run(run_id)
    stream = io.StringIO(newline='')
    fields = ['timestamp', 'value', 'label', 'reconstructed', 'score', 'prediction']
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(result['points'])
    return Response('\ufeff' + stream.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="anomalies-{result["id"][:8]}.csv"'})


def svg_chart(points, field, color, threshold=None):
    finite = [p[field] for p in points if p.get(field) is not None]
    if not finite:
        return '<p>Нет значений для графика.</p>'
    minimum, maximum = min(finite), max(finite)
    if threshold is not None:
        maximum = max(maximum, threshold)
        minimum = min(minimum, threshold)
    span = max(maximum - minimum, 1e-8)
    x = lambda i: 60 + i / max(len(points) - 1, 1) * 980
    y = lambda v: 235 - (v - minimum) / span * 190
    segments, current, dots = [], [], []
    for i, p in enumerate(points):
        value = p.get(field)
        if value is None:
            if current:
                segments.append(' '.join(current))
                current = []
        else:
            current.append(f'{x(i):.1f},{y(value):.1f}')
            if p['prediction'] == 1:
                dots.append(f'<circle cx="{x(i):.1f}" cy="{y(value):.1f}" r="1.8" fill="#dc554e"/>')
    if current:
        segments.append(' '.join(current))
    lines = ''.join(f'<polyline points="{s}" fill="none" stroke="{color}" stroke-width="1.2"/>' for s in segments)
    threshold_line = '' if threshold is None else f'<path d="M60 {y(threshold):.1f} H1040" stroke="#dc554e" stroke-dasharray="6 4"/>'
    return f'''<svg viewBox="0 0 1100 280" role="img" aria-label="График {field}">
    <rect width="1100" height="280" fill="#fafbf9"/><path d="M60 35V240H1040" fill="none" stroke="#cbd3cd"/>
    <text x="8" y="48" font-size="12">{maximum:.3f}</text><text x="8" y="235" font-size="12">{minimum:.3f}</text>
    {lines}{threshold_line}{''.join(dots)}<text x="60" y="265" font-size="12">{html.escape(points[0]['timestamp'][:10])}</text>
    <text x="960" y="265" font-size="12">{html.escape(points[-1]['timestamp'][:10])}</text></svg>'''


@app.get('/api/runs/{run_id}/report', response_class=HTMLResponse)
def report(run_id: str):
    result = run(run_id)
    escape = lambda value: html.escape(str(value))
    metrics = result['metrics'] or {}
    names = {'precision': 'Precision', 'recall': 'Recall', 'f1': 'F1', 'f0_5': 'F0.5', 'f2': 'F2',
             'average_precision': 'Average precision', 'roc_auc': 'ROC AUC', 'tp': 'TP', 'fp': 'FP', 'fn': 'FN', 'tn': 'TN'}
    metric_rows = ''.join(f'<tr><td>{label}</td><td>{"—" if metrics.get(key) is None else escape(round(metrics[key], 4))}</td></tr>'
                          for key, label in names.items())
    train = result['training']['request']
    page = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Контур — отчёт {escape(result['id'][:8])}</title><style>
    body{{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:45px auto;padding:0 24px;color:#23332e}}
    h1{{font-size:34px}}h2{{margin-top:35px}}.tag{{color:#33816f;font-weight:600}}table{{border-collapse:collapse;width:100%;max-width:600px}}
    td{{padding:7px 12px;border-bottom:1px solid #dce3df}}svg{{width:100%;height:auto}}.note{{background:#f2f5f1;padding:18px;border-radius:10px}}
    @media print{{body{{margin:0}}h2{{break-after:avoid}}svg{{break-inside:avoid}}}}
    </style><p class="tag">КОНТУР / АНАЛИЗ ОБОРУДОВАНИЯ</p><h1>Отчёт об обнаружении аномалий</h1>
    <p>{escape(result['dataset_name'])} · датчик {escape(result['sensor'])}</p>
    <p>Создан: {escape(result['created_at'])}. Все даты и время — UTC. Конец каждого периода не включается.</p>
    <h2>Данные и модель</h2><table>
    <tr><td>Модель / версия</td><td>{escape(result['model_name'])} / {escape(result['model_version'])}</td></tr>
    <tr><td>Обучение</td><td>{escape(train['train_start'])} — {escape(train['train_end'])}</td></tr>
    <tr><td>Настройка порога</td><td>{escape(train['calibration_start'])} — {escape(train['calibration_end'])}</td></tr>
    <tr><td>Проверка</td><td>{escape(result['start'])} — {escape(result['end'])}</td></tr>
    <tr><td>Окно</td><td>{result['window']} точек, шаг {result['step_seconds']} с</td></tr>
    <tr><td>Порог ошибки</td><td>{result['threshold']:.6g}</td></tr>
    <tr><td>Проверено / пропущено</td><td>{result['scored_points']} / {result['skipped_points']}</td></tr>
    <tr><td>Срабатывания (точки)</td><td>{result['anomaly_points']}</td></tr></table>
    <h2>Температура, °C</h2>{svg_chart(result['points'], 'value', '#278878')}
    <p>Красные точки — срабатывания модели.</p><h2>Оценка аномальности</h2>
    {svg_chart(result['points'], 'score', '#6371ad', result['threshold'])}
    <h2>Качество относительно разметки</h2><table>{metric_rows}</table>
    <p>Метрики рассчитаны только для точек с разметкой и доступным прогнозом. «—» означает, что метрика не определена.</p>
    <h2>Методика и ограничения</h2><p>Модель восстанавливает прошлое окно. Оценка — квадрат ошибки последней точки после нормализации.
    Параметры нормализации определены на обучающей части; лучшая эпоха выбрана на последующей части обучающего периода.
    Порог настроен по квантилю ошибок отдельного периода. Окна с пропусками и разрывами шага исключены.
    Контекст до начала проверки используется только из прошлого. Измеряется обнаружение аномалий, а не заблаговременность предупреждения.</p>
    <p class="note">{escape(result['evaluation_note'])}</p>
    <p>Период обучения без разметки считается нормальным: {escape(result['training']['normal_period_assumption'])}.</p>
    <p>SHA-256 обучающего снимка: <small>{escape(result['training']['training_snapshot_sha256'])}</small></p></html>'''
    return HTMLResponse(page, headers={'Content-Disposition': f'attachment; filename="report-{result["id"][:8]}.html"'})
