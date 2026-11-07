"""Run the four user scenarios through Web Master (no direct database access)."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def request(base, method, path, body=None):
    raw = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(base.rstrip('/') + '/api' + path, data=raw, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode('utf-8')) from error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8050')
    parser.add_argument('--epochs', default=120, type=int)
    parser.add_argument('--all-sensors', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    call = lambda method, path, body=None: request(args.base, method, path, body)
    systems = call('GET', '/system')
    assert all(s['status'] == 'ok' for s in systems), systems
    available = call('GET', '/datasets')
    dataset = next((d for d in available if d['source_id'] == 'iqds-demo' and d['rows'] == 38016), None)
    if dataset is None:
        print('1. Importing demo data through Collector -> Storage', flush=True)
        dataset = call('POST', '/import', {'source_id': 'iqds-demo', 'all_batches': True})['dataset']
    print(f"Dataset: {dataset['id']}, {dataset['rows']} records, {len(dataset['sensors'])} sensors", flush=True)
    results = []
    for item in dataset['sensors'] if args.all_sensors else dataset['sensors'][:1]:
        sensor = item['sensor']
        model = next((m for m in call('GET', '/models') if m['status'] == 'ready'
                      and m['request']['dataset_id'] == dataset['id'] and m['request']['sensor'] == sensor
                      and m['request']['epochs'] == args.epochs and m['dataset_revision'] == dataset['revision']), None)
        if model is None:
            print(f'2. Training LSTM for {sensor}', flush=True)
            model = call('POST', '/train', {'dataset_id': dataset['id'], 'sensor': sensor,
                                          'name': f'LSTM - {sensor[-2:]}', 'epochs': args.epochs})
            deadline, previous = time.monotonic() + 1200, -1
            while model['status'] in ('queued', 'training'):
                if time.monotonic() > deadline:
                    raise TimeoutError('Training timeout')
                time.sleep(2)
                model = call('GET', '/models/' + model['id'])
                if model['epoch'] != previous:
                    print(f"   {model['status']}: {model['epoch']}/{args.epochs}", flush=True)
                    previous = model['epoch']
            if model['status'] != 'ready':
                raise RuntimeError(model.get('error', model['status']))
        print(f"3. Analyze held-out period; model {model['version']}", flush=True)
        run = call('POST', '/analyze', {'dataset_id': dataset['id'], 'model_id': model['id'],
                                      'start': '2025-07-20', 'end': '2025-09-09'})
        assert run['scored_points'] > 0
        for fmt in ('report', 'csv'):
            url = args.base.rstrip('/') + '/api/runs/' + run['id'] + '/' + fmt
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read()
            assert len(content) > 100
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / f"{sensor[-2:]}-{run['id'][:8]}.{'html' if fmt == 'report' else 'csv'}").write_bytes(content)
        print('4. HTML report and CSV downloaded successfully', flush=True)
        result = {k: v for k, v in run.items() if k not in ('points', 'training')}
        results.append(result)
        print(json.dumps(result, ensure_ascii=True, indent=2), flush=True)
    if args.output:
        (args.output / 'verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print('All four scenarios passed.', flush=True)


if __name__ == '__main__':
    main()
