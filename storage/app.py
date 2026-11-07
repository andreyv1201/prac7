from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import Body, HTTPException, Query

from common.runtime import load_config, make_app, now
from common.schemas import BulkRecords, DatasetCreate, DatasetUpdate, Measurement, timestamp

config = load_config('storage')
app = make_app('storage', config)
database = Path(config['state_dir']) / 'storage.sqlite3'


@contextmanager
def connection():
    db = sqlite3.connect(database, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


with connection() as db:
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS datasets (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, source_id TEXT,
            source_offset INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS measurements (
            dataset_id TEXT REFERENCES datasets(id) ON DELETE CASCADE,
            timestamp TEXT NOT NULL, sensor TEXT NOT NULL, value REAL, label INTEGER,
            PRIMARY KEY(dataset_id, sensor, timestamp));
        CREATE INDEX IF NOT EXISTS measurements_time ON measurements(dataset_id,timestamp);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, dataset_id TEXT REFERENCES datasets(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL, summary TEXT NOT NULL, payload TEXT NOT NULL);
    ''')


def require_dataset(db, dataset_id):
    row = db.execute('SELECT * FROM datasets WHERE id=?', (dataset_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Набор данных не найден')
    return dict(row)


def dataset_summary(db, dataset_id):
    result = require_dataset(db, dataset_id)
    result.update(dict(db.execute('''SELECT count(*) AS rows, min(timestamp) AS start, max(timestamp) AS end,
                                  sum(value IS NULL) AS missing_values,
                                  sum(label IS NOT NULL) AS labeled_rows
                                  FROM measurements WHERE dataset_id=?''', (dataset_id,)).fetchone()))
    result['sensors'] = [dict(r) for r in db.execute('''SELECT sensor,count(*) AS rows,min(timestamp) AS start,
                          max(timestamp) AS end FROM measurements WHERE dataset_id=? GROUP BY sensor''', (dataset_id,))]
    return result


@app.get('/datasets')
def datasets():
    with connection() as db:
        ids = [r[0] for r in db.execute('SELECT id FROM datasets ORDER BY created_at DESC')]
        return [dataset_summary(db, item) for item in ids]


@app.post('/datasets', status_code=201)
def create_dataset(body: DatasetCreate):
    dataset_id = uuid.uuid4().hex
    with connection() as db:
        db.execute('INSERT INTO datasets(id,name,source_id,created_at) VALUES(?,?,?,?)',
                   (dataset_id, body.name, body.source_id, now()))
        return dataset_summary(db, dataset_id)


@app.get('/datasets/{dataset_id}')
def dataset(dataset_id: str):
    with connection() as db:
        return dataset_summary(db, dataset_id)


@app.patch('/datasets/{dataset_id}')
def update_dataset(dataset_id: str, body: DatasetUpdate):
    with connection() as db:
        require_dataset(db, dataset_id)
        db.execute('UPDATE datasets SET name=? WHERE id=?', (body.name, dataset_id))
        return dataset_summary(db, dataset_id)


@app.delete('/datasets/{dataset_id}')
def delete_dataset(dataset_id: str):
    with connection() as db:
        require_dataset(db, dataset_id)
        db.execute('DELETE FROM datasets WHERE id=?', (dataset_id,))
    return {'deleted': dataset_id}


@app.post('/datasets/{dataset_id}/records')
def write_records(dataset_id: str, body: BulkRecords):
    with connection() as db:
        item = require_dataset(db, dataset_id)
        # Bound total size including new keys; replacements do not count twice.
        current = db.execute('SELECT count(*) FROM measurements WHERE dataset_id=?', (dataset_id,)).fetchone()[0]
        keys = {(r.sensor, r.timestamp) for r in body.records}
        existing = set((r['sensor'], r['timestamp']) for r in db.execute(
            'SELECT sensor,timestamp FROM measurements WHERE dataset_id=?', (dataset_id,)))
        if current + len(keys - existing) > config['max_records']:
            raise HTTPException(413, f"Допускается до {config['max_records']} записей в наборе")
        db.executemany('''INSERT INTO measurements VALUES(?,?,?,?,?) ON CONFLICT(dataset_id,sensor,timestamp)
                          DO UPDATE SET value=excluded.value,label=excluded.label''',
                       [(dataset_id, r.timestamp, r.sensor, r.value, r.label) for r in body.records])
        offset = item['source_offset'] if body.source_offset is None else body.source_offset
        db.execute('UPDATE datasets SET revision=revision+1,source_offset=? WHERE id=?', (offset, dataset_id))
        return {'written': len(body.records), 'revision': item['revision'] + 1, 'source_offset': offset}


@app.get('/datasets/{dataset_id}/records')
def read_records(dataset_id: str, sensor: str | None = None, start: str | None = None, end: str | None = None,
                 offset: int = Query(0, ge=0), limit: int = Query(5000, ge=1, le=100000)):
    clauses, args = ['dataset_id=?'], [dataset_id]
    if sensor:
        clauses.append('sensor=?')
        args.append(sensor)
    try:
        for bound, operator, value in [('start', '>=', start), ('end', '<', end)]:
            if value:
                clauses.append(f'timestamp{operator}?')
                args.append(timestamp(value))
    except ValueError as error:
        raise HTTPException(422, 'Некорректная дата') from error
    with connection() as db:
        require_dataset(db, dataset_id)
        where = ' AND '.join(clauses)
        total = db.execute(f'SELECT count(*) FROM measurements WHERE {where}', args).fetchone()[0]
        rows = db.execute(f'''SELECT timestamp,sensor,value,label FROM measurements WHERE {where}
                              ORDER BY timestamp,sensor LIMIT ? OFFSET ?''', [*args, limit, offset])
        return {'total': total, 'offset': offset, 'records': [dict(r) for r in rows]}


@app.put('/datasets/{dataset_id}/record')
def put_record(dataset_id: str, body: Measurement):
    return write_records(dataset_id, BulkRecords(records=[body]))


@app.delete('/datasets/{dataset_id}/record')
def delete_record(dataset_id: str, sensor: str, time: str):
    try:
        normalized = timestamp(time)
    except ValueError as error:
        raise HTTPException(422, 'Некорректная дата') from error
    with connection() as db:
        require_dataset(db, dataset_id)
        result = db.execute('DELETE FROM measurements WHERE dataset_id=? AND sensor=? AND timestamp=?',
                            (dataset_id, sensor, normalized))
        if not result.rowcount:
            raise HTTPException(404, 'Измерение не найдено')
        db.execute('UPDATE datasets SET revision=revision+1 WHERE id=?', (dataset_id,))
    return {'deleted': True}


@app.post('/runs', status_code=201)
def save_run(body: dict = Body(...)):
    try:
        run_id, dataset_id = body['id'], body['dataset_id']
        summary = {k: v for k, v in body.items() if k != 'points'}
        payload = json.dumps(body, ensure_ascii=False, allow_nan=False)
        encoded_summary = json.dumps(summary, ensure_ascii=False, allow_nan=False)
    except (KeyError, ValueError, TypeError) as error:
        raise HTTPException(422, 'Некорректный результат анализа') from error
    with connection() as db:
        require_dataset(db, dataset_id)
        try:
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?)', (run_id, dataset_id, now(), encoded_summary, payload))
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, 'Результат уже сохранён') from error
    return summary


@app.get('/runs')
def runs(dataset_id: str | None = None):
    with connection() as db:
        query = 'SELECT summary FROM runs'
        args = []
        if dataset_id:
            query += ' WHERE dataset_id=?'
            args.append(dataset_id)
        return [json.loads(r[0]) for r in db.execute(query + ' ORDER BY created_at DESC LIMIT 100', args)]


@app.get('/runs/{run_id}')
def run(run_id: str):
    with connection() as db:
        row = db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Запуск не найден')
        return json.loads(row[0])
