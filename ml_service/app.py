from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from fastapi import HTTPException

from common.runtime import atomic_json, load_config, make_app, now, remote
from common.schemas import PredictRequest, TrainRequest
from ml_service.model import LSTMAutoencoder, endpoints, fit, frame_from_records, metric_values, score_model

config = load_config('ml_service')
app = make_app('ml_service', config)
models_dir = Path(config['state_dir']) / 'models'
models_dir.mkdir(parents=True, exist_ok=True)
executor = ThreadPoolExecutor(max_workers=1)
job_lock = threading.Lock()
state_lock = threading.Lock()
torch.set_num_threads(config['torch_threads'])


def model_dir(model_id):
    if not re.fullmatch('[0-9a-f]{32}', model_id):
        raise HTTPException(404, 'Модель не найдена')
    path = models_dir / model_id
    if not (path / 'summary.json').is_file():
        raise HTTPException(404, 'Модель не найдена')
    return path


def summary(model_id):
    with state_lock:
        return json.loads((model_dir(model_id) / 'summary.json').read_text(encoding='utf-8'))


def update(model_id, **fields):
    with state_lock:
        path = model_dir(model_id) / 'summary.json'
        current = json.loads(path.read_text(encoding='utf-8'))
        current.update(fields)
        atomic_json(path, current)
        return current


for saved in models_dir.glob('*/summary.json'):
    previous = json.loads(saved.read_text(encoding='utf-8'))
    if previous['status'] in ('queued', 'training'):
        previous.update(status='interrupted', error='Обучение прервано перезапуском. Создайте новый запуск обучения.')
        atomic_json(saved, previous)


def get_frame(dataset_id, sensor):
    dataset = remote('GET', config['storage_url'], f'/datasets/{dataset_id}')
    payload = remote('GET', config['storage_url'], f'/datasets/{dataset_id}/records',
                     params={'sensor': sensor, 'limit': 100000})
    after = remote('GET', config['storage_url'], f'/datasets/{dataset_id}')
    if after['revision'] != dataset['revision']:
        raise ValueError('Данные изменились во время чтения. Повторите операцию.')
    frame, step = frame_from_records(payload['records'])
    return frame, step, dataset


def train_worker(model_id):
    started = time.perf_counter()
    try:
        state = summary(model_id)
        request = TrainRequest(**state['request'])
        update(model_id, status='training', started_at=now())
        frame, step, dataset = get_frame(request.dataset_id, request.sensor)
        model, metadata = fit(frame, step, request, config,
                              lambda epoch, history: update(model_id, epoch=epoch, history=history))
        path = model_dir(model_id)
        torch.save(model.state_dict(), path / 'weights.tmp')
        (path / 'weights.tmp').replace(path / 'weights.pt')
        update(model_id, status='ready', finished_at=now(), fit_seconds=round(time.perf_counter() - started, 2),
               dataset_revision=dataset['revision'], dataset_name=dataset['name'], **metadata)
    except Exception as error:
        app.state.logger.exception('Training failed')
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        update(model_id, status='failed', error=str(detail), finished_at=now())
    finally:
        job_lock.release()


@app.get('/models')
def list_models():
    return sorted([summary(p.parent.name) for p in models_dir.glob('*/summary.json')],
                  key=lambda item: item['created_at'], reverse=True)


@app.post('/models', status_code=201)
def create_model(body: TrainRequest):
    # Verify the dataset and sensor before creating an artifact.
    dataset = remote('GET', config['storage_url'], f'/datasets/{body.dataset_id}')
    if body.sensor not in [s['sensor'] for s in dataset['sensors']]:
        raise HTTPException(422, 'Датчик отсутствует в выбранном наборе')
    model_id = uuid.uuid4().hex
    state = {'id': model_id, 'name': body.name, 'algorithm': 'LSTM autoencoder', 'version': model_id[:8],
             'status': 'created', 'created_at': now(), 'epoch': 0, 'history': [], 'request': body.model_dump()}
    with state_lock:
        atomic_json(models_dir / model_id / 'summary.json', state)
    return state


@app.get('/models/{model_id}')
def get_model(model_id: str):
    return summary(model_id)


@app.post('/models/{model_id}/train', status_code=202)
def train(model_id: str):
    state = summary(model_id)
    if state['status'] != 'created':
        raise HTTPException(409, 'Этот запуск уже использован. Создайте новую версию модели.')
    if not job_lock.acquire(blocking=False):
        raise HTTPException(409, 'Сейчас обучается другая модель. Дождитесь завершения.')
    try:
        result = update(model_id, status='queued')
        executor.submit(train_worker, model_id)
    except Exception:
        job_lock.release()
        raise
    return result


@app.post('/models/{model_id}/predict')
def predict(model_id: str, body: PredictRequest):
    state = summary(model_id)
    if state['status'] != 'ready':
        raise HTTPException(409, 'Модель ещё не готова к анализу')
    request = TrainRequest(**state['request'])
    if body.start < request.calibration_end:
        raise HTTPException(422, 'Проверка должна начинаться после периода настройки порога')
    if not job_lock.acquire(blocking=False):
        raise HTTPException(409, 'Дождитесь завершения обучения перед анализом')
    try:
        frame, step, dataset = get_frame(body.dataset_id, request.sensor)
        if body.dataset_id == request.dataset_id and dataset['revision'] != state['dataset_revision']:
            raise ValueError('Данные изменены после обучения. Обучите новую версию модели.')
        if step != state['step_seconds']:
            raise ValueError('Шаг времени новых данных отличается от шага обучения')
        valid = endpoints(frame, np.ones(len(frame), dtype=bool), request.window, step)
        selected = (frame.index >= body.start) & (frame.index < body.end)
        valid = valid[selected[valid]]
        if not len(valid):
            raise ValueError('В выбранном периоде нет полных окон без пропусков')
        model = LSTMAutoencoder(1, state['hidden_size'], state['latent_size'])
        model.load_state_dict(torch.load(model_dir(model_id) / 'weights.pt', map_location='cpu', weights_only=True))
        scores, reconstructed = score_model(model, frame, valid, request.window, state['center'], state['scale'])
        mapped = {int(position): (float(score), float(value))
                  for position, score, value in zip(valid, scores, reconstructed)}
        points = []
        for i in np.flatnonzero(selected):
            row = frame.iloc[i]
            score, reconstructed_value = mapped.get(int(i), (None, None))
            points.append({'timestamp': frame.index[i].isoformat().replace('+00:00', 'Z'),
                           'value': float(row.value) if pd.notna(row.value) else None,
                           'label': int(row.label) if pd.notna(row.label) else None,
                           'score': score, 'reconstructed': reconstructed_value,
                           'prediction': int(score > state['threshold']) if score is not None else None})
        run = {'id': uuid.uuid4().hex, 'created_at': now(), 'dataset_id': body.dataset_id,
               'dataset_name': dataset['name'], 'dataset_revision': dataset['revision'],
               'model_id': model_id, 'model_name': state['name'], 'model_version': state['version'],
               'sensor': request.sensor, 'start': body.start, 'end': body.end,
               'threshold': state['threshold'], 'step_seconds': step, 'window': request.window,
               'total_points': len(points), 'scored_points': len(valid), 'skipped_points': len(points) - len(valid),
               'anomaly_points': sum(p['prediction'] == 1 for p in points),
               'metrics': metric_values(points), 'points': points,
               'evaluation_note': 'Демонстрационная проверка на ранее использованных исследовательских данных; '
                                  'не является независимой внешней валидацией.',
               'training': {k: state[k] for k in ('request', 'best_epoch', 'best_validation_mse',
                            'training_snapshot_sha256', 'fit_seconds', 'normal_period_assumption')}}
        remote('POST', config['storage_url'], '/runs', json=run)
        return run
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    finally:
        job_lock.release()
