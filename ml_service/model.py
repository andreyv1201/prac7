"""Causal LSTM autoencoder adapted from the user's IQDS research implementation."""
from __future__ import annotations

import copy
import hashlib
import json

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, roc_auc_score
from torch import nn


class LSTMAutoencoder(nn.Module):
    def __init__(self, channels: int = 1, hidden_size: int = 32, latent_size: int = 8):
        super().__init__()
        self.encoder = nn.LSTM(channels, hidden_size, batch_first=True)
        self.to_latent = nn.Sequential(nn.Linear(hidden_size, latent_size), nn.Tanh())
        self.initial_hidden = nn.Linear(latent_size, hidden_size)
        self.initial_cell = nn.Linear(latent_size, hidden_size)
        self.decoder = nn.LSTM(latent_size + 3, hidden_size, batch_first=True)
        self.output = nn.Linear(hidden_size, channels)

    def forward(self, windows):
        _, (hidden, _) = self.encoder(windows)
        latent = self.to_latent(hidden[-1])
        repeated = latent[:, None, :].expand(-1, windows.shape[1], -1).contiguous()
        position = torch.linspace(-1, 1, windows.shape[1], device=windows.device, dtype=windows.dtype)
        coordinates = torch.stack((position, torch.sin(torch.pi * position), torch.cos(torch.pi * position)), dim=-1)
        coordinates = coordinates[None].expand(windows.shape[0], -1, -1)
        state = (self.initial_hidden(latent)[None].contiguous(), self.initial_cell(latent)[None].contiguous())
        decoded, _ = self.decoder(torch.cat((repeated, coordinates), dim=-1), state)
        return self.output(decoded)


def frame_from_records(records: list[dict]):
    if not records:
        raise ValueError('У выбранного датчика нет данных')
    frame = pd.DataFrame(records)
    frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True)
    frame = frame.sort_values('timestamp').set_index('timestamp')
    if frame.index.has_duplicates:
        raise ValueError('В ряду есть повторяющиеся временные отметки')
    frame['value'] = pd.to_numeric(frame.value, errors='coerce')
    frame['label'] = pd.to_numeric(frame.label, errors='coerce')
    if len(frame) < 30:
        raise ValueError('Для модели требуется не менее 30 измерений')
    differences = np.diff(frame.index.asi8) // 10**9
    positive = differences[differences > 0]
    if not len(positive):
        raise ValueError('Не удалось определить шаг времени')
    values, counts = np.unique(positive, return_counts=True)
    step = int(values[np.argmax(counts)])
    return frame, step


def endpoints(frame, mask, window, step):
    """Only complete past windows with a continuous sampling grid are eligible."""
    mask = np.asarray(mask, dtype=bool) & np.isfinite(frame.value.to_numpy(dtype=float))
    if len(frame) < window:
        return np.array([], dtype=int)
    ends = np.arange(window - 1, len(frame))
    bad = np.r_[0, np.cumsum(~mask)]
    eligible = (bad[ends + 1] - bad[ends - window + 1]) == 0
    gap_sum = np.r_[0, np.cumsum(np.diff(frame.index.asi8) != step * 10**9)]
    eligible &= (gap_sum[ends] - gap_sum[ends - window + 1]) == 0
    return ends[eligible]


def windows(frame, positions, length, center, scale):
    indices = positions[:, None] - length + 1 + np.arange(length)[None, :]
    return ((frame.value.to_numpy(dtype=float)[indices, None] - center) / scale).astype(np.float32)


def score_model(model, frame, positions, window, center, scale):
    scores, reconstructed = [], []
    model.eval()
    with torch.inference_mode():
        for batch in np.array_split(positions, max(1, int(np.ceil(len(positions) / 256)))):
            if not len(batch):
                continue
            x = windows(frame, batch, window, center, scale)
            output = model(torch.from_numpy(x)).numpy()
            scores.extend(np.square(output[:, -1, 0] - x[:, -1, 0]).astype(float))
            reconstructed.extend((output[:, -1, 0] * scale + center).astype(float))
    return np.asarray(scores), np.asarray(reconstructed)


def fit(frame, step, request, config, progress):
    np.random.seed(request.seed)
    torch.manual_seed(request.seed)
    torch.set_num_threads(config['torch_threads'])
    torch.use_deterministic_algorithms(True)
    start, end = pd.Timestamp(request.train_start), pd.Timestamp(request.train_end)
    split = start + (end - start) * 0.8
    inside = (frame.index >= start) & (frame.index < end)
    normal = frame.label.eq(0).to_numpy() if frame.loc[inside, 'label'].notna().any() else np.ones(len(frame), dtype=bool)
    training_mask = inside & (frame.index < split) & normal
    validation_mask = inside & (frame.index >= split) & normal
    train_ends = endpoints(frame, training_mask, request.window, step)
    valid_ends = endpoints(frame, validation_mask, request.window, step)
    calibration_mask = (frame.index >= request.calibration_start) & (frame.index < request.calibration_end)
    if frame.loc[calibration_mask, 'label'].notna().any():
        calibration_mask &= frame.label.eq(0).to_numpy()
    calibration_ends = endpoints(frame, calibration_mask, request.window, step)
    if len(train_ends) < 30 or len(valid_ends) < 10 or len(calibration_ends) < 30:
        raise ValueError('Недостаточно полных нормальных окон. Расширьте периоды или уменьшите окно. '
                         f'Обучение: {len(train_ends)}, валидация: {len(valid_ends)}, порог: {len(calibration_ends)}.')
    observed = frame.value.to_numpy(dtype=float)[training_mask & np.isfinite(frame.value.to_numpy(dtype=float))]
    center = float(np.median(observed))
    scale = float(np.quantile(observed, 0.75) - np.quantile(observed, 0.25))
    if scale < 1e-6:
        scale = 1.0
    train_x = torch.from_numpy(windows(frame, train_ends, request.window, center, scale))
    valid_x = torch.from_numpy(windows(frame, valid_ends, request.window, center, scale))
    model = LSTMAutoencoder(1, config['hidden_size'], config['latent_size'])
    optimizer = torch.optim.Adam(model.parameters(), lr=config['learning_rate'], weight_decay=1e-5)
    generator = torch.Generator().manual_seed(request.seed)
    best_loss, best_state, best_epoch = float('inf'), None, 0
    history = []
    for epoch in range(1, request.epochs + 1):
        model.train()
        train_sum = 0.0
        for indices in torch.randperm(len(train_x), generator=generator).split(config['batch_size']):
            x = train_x[indices]
            optimizer.zero_grad(set_to_none=True)
            loss = nn.functional.mse_loss(model(x), x)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_sum += loss.item() * len(x)
        model.eval()
        with torch.inference_mode():
            valid_loss = sum(nn.functional.mse_loss(model(x), x, reduction='sum').item()
                             for x in valid_x.split(config['batch_size'])) / valid_x.numel()
        if valid_loss < best_loss:
            best_loss, best_state, best_epoch = valid_loss, copy.deepcopy(model.state_dict()), epoch
        history.append({'epoch': epoch, 'train_mse': train_sum / len(train_x), 'validation_mse': valid_loss})
        progress(epoch, history)
    model.load_state_dict(best_state)
    scores, _ = score_model(model, frame, calibration_ends, request.window, center, scale)
    threshold = max(float(np.quantile(scores, request.quantile)), 1e-12)
    snapshot = frame.loc[(frame.index >= start) & (frame.index < request.calibration_end)]
    digest = hashlib.sha256(snapshot.to_csv().encode('utf-8')).hexdigest()
    metadata = {
        'center': center, 'scale': scale, 'threshold': threshold, 'step_seconds': step,
        'hidden_size': config['hidden_size'], 'latent_size': config['latent_size'],
        'train_windows': len(train_ends), 'validation_windows': len(valid_ends),
        'calibration_windows': len(calibration_ends), 'validation_split': split.isoformat(),
        'best_epoch': best_epoch, 'best_validation_mse': best_loss,
        'parameters': sum(p.numel() for p in model.parameters()), 'history': history,
        'training_snapshot_sha256': digest,
        'normal_period_assumption': not bool(frame.loc[inside, 'label'].notna().any()),
        'architecture': 'LSTM encoder → latent vector → positional LSTM decoder; last-point reconstruction score',
        'torch_version': str(torch.__version__),
    }
    return model, metadata


def metric_values(points):
    labeled = [p for p in points if p['prediction'] is not None and p['label'] is not None]
    if not labeled:
        return None
    y = np.asarray([p['label'] for p in labeled], dtype=int)
    prediction = np.asarray([p['prediction'] for p in labeled], dtype=int)
    scores = np.asarray([p['score'] for p in labeled], dtype=float)
    tp = int(((y == 1) & (prediction == 1)).sum())
    fp = int(((y == 0) & (prediction == 1)).sum())
    fn = int(((y == 1) & (prediction == 0)).sum())
    tn = int(((y == 0) & (prediction == 0)).sum())
    return {'labeled_points': len(y), 'positive_points': int(y.sum()), 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            'f0_5': 1.25 * tp / (1.25 * tp + 0.25 * fn + fp) if 1.25 * tp + 0.25 * fn + fp else None,
            'f2': 5 * tp / (5 * tp + 4 * fn + fp) if 5 * tp + 4 * fn + fp else None,
            'average_precision': float(average_precision_score(y, scores)) if len(np.unique(y)) == 2 else None,
            'roc_auc': float(roc_auc_score(y, scores)) if len(np.unique(y)) == 2 else None}
