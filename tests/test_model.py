import numpy as np
import pandas as pd
import torch
from pydantic import ValidationError
import pytest

from common.schemas import Measurement, TrainRequest
from ml_service.model import LSTMAutoencoder, endpoints, fit, frame_from_records, metric_values, score_model


def sample_frame():
    index = pd.date_range('2025-01-01', periods=1000, freq='10min', tz='UTC')
    return pd.DataFrame({'value': 23 + np.sin(np.arange(1000) / 20), 'label': 0}, index=index)


def test_windows_exclude_gaps_and_missing_values():
    frame = sample_frame().iloc[:40].copy()
    frame.iloc[10, 0] = np.nan
    result = endpoints(frame, np.ones(len(frame), dtype=bool), 6, 600)
    assert not set(range(10, 16)) & set(result)
    frame = sample_frame().iloc[:40].drop(sample_frame().index[10])
    result = endpoints(frame, np.ones(len(frame), dtype=bool), 6, 600)
    assert 14 not in result and 15 in result


def test_future_values_cannot_change_past_scores():
    torch.set_num_threads(2)
    torch.manual_seed(1)
    model = LSTMAutoencoder(1, 8, 4)
    original = sample_frame()
    changed = original.copy()
    changed.iloc[100:, 0] += 10000
    positions = np.arange(20, 100)
    a, _ = score_model(model, original, positions, 12, 23, 1)
    b, _ = score_model(model, changed, positions, 12, 23, 1)
    np.testing.assert_array_equal(a, b)


def test_test_period_does_not_affect_training_or_threshold():
    request = TrainRequest(dataset_id='test', sensor='s1', train_start='2025-01-01', train_end='2025-01-04',
                           calibration_start='2025-01-04', calibration_end='2025-01-06', window=12, epochs=2)
    config = {'torch_threads': 2, 'hidden_size': 8, 'latent_size': 4, 'batch_size': 128, 'learning_rate': .001}
    original = sample_frame()
    changed = original.copy()
    changed.loc[changed.index >= '2025-01-06', 'value'] += 5000
    model_a, a = fit(original, 600, request, config, lambda *_: None)
    model_b, b = fit(changed, 600, request, config, lambda *_: None)
    assert a['threshold'] == b['threshold']
    assert a['training_snapshot_sha256'] == b['training_snapshot_sha256']
    assert a['center'] == b['center'] and a['scale'] == b['scale']
    for key in model_a.state_dict():
        torch.testing.assert_close(model_a.state_dict()[key], model_b.state_dict()[key], rtol=0, atol=0)


def test_metrics_ignore_unlabeled_and_unscored_points():
    points = [{'label':1,'prediction':1,'score':2.}, {'label':0,'prediction':1,'score':1.},
              {'label':None,'prediction':1,'score':8.}, {'label':1,'prediction':None,'score':None}]
    metrics = metric_values(points)
    assert metrics['labeled_points'] == 2
    assert metrics['precision'] == .5 and metrics['recall'] == 1
    assert metric_values([{'label':None,'prediction':1,'score':2.}]) is None


def test_invalid_values_and_overlapping_periods_are_rejected():
    with pytest.raises(ValidationError):
        Measurement(timestamp='2025-01-01', sensor='s1', value=float('nan'))
    with pytest.raises(ValidationError):
        Measurement(timestamp='2025-01-01', sensor='s1', value=2, label=2)
    with pytest.raises(ValidationError):
        TrainRequest(dataset_id='test', sensor='s1', train_end='2025-06-25', calibration_start='2025-06-22')
