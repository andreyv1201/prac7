"""Create a causal 10-minute demonstration copy from the local IQDS files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def prepare(source: Path):
    values_path, labels_path = source / 'mv_cond_temperature.csv', source / 'labled_mv_cond.csv'
    values, labels = pd.read_csv(values_path), pd.read_csv(labels_path)
    values['timestamp'] = pd.to_datetime(values.event_timestamp, unit='s', utc=True)
    labels['timestamp'] = pd.to_datetime(labels.event_timestamp, unit='s', utc=True)
    frames = []
    sensors = {}
    for i, sensor in enumerate(sorted(values.io_id.unique()), 1):
        sensor_values = values[values.io_id.eq(sensor)].set_index('timestamp').value.sort_index()
        sensor_labels = labels[labels.io_id.eq(sensor)].set_index('timestamp').anomaly.sort_index()
        # Timestamp is the RIGHT edge: every included reading is already observed.
        v = sensor_values.resample('10min', closed='right', label='right').mean()
        l = sensor_labels.resample('10min', closed='right', label='right').max().reindex(v.index)
        frame = pd.DataFrame({'timestamp': v.index, 'sensor': sensor, 'value': v.values, 'label': l.values})
        frame = frame[(frame.timestamp >= '2025-06-13') & (frame.timestamp < '2025-09-09')]
        frames.append(frame)
        sensors[sensor] = f'Температура · контур {i}'
    demo = pd.concat(frames).sort_values(['timestamp', 'sensor'])
    demo['timestamp'] = demo.timestamp.dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    demo['value'] = demo.value.round(6)
    demo['label'] = demo.label.astype('Int64')
    target = ROOT / 'data' / 'iqds_demo.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    demo.to_csv(target, index=False)
    manifest = {
        'source': 'Local IQDS research copy: mv_cond_temperature.csv + labled_mv_cond.csv',
        'original_measurement_rows': len(values), 'original_label_rows': len(labels),
        'original_source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in (values_path, labels_path)},
        'rows': len(demo), 'sensors': sensors, 'unit': '°C', 'timezone': 'UTC',
        'start': demo.timestamp.min(), 'end': demo.timestamp.max(), 'step_seconds': 600,
        'aggregation': 'Mean values and max labels in (t-10min, t], timestamp at right edge.',
        'missing_values': int(demo.value.isna().sum()),
        'missing_labels': int(demo.label.isna().sum()),
        'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'defaults': {'train_start': '2025-06-13', 'train_end': '2025-06-22',
                     'calibration_end': '2025-06-29', 'test_start': '2025-07-20', 'test_end': '2025-09-09'},
        'evaluation_note': 'Demonstration on previously used research periods; not independent external validation.'
    }
    (target.parent / 'demo_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    prepare(parser.parse_args().source)
