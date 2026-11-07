"""Save/verify a snapshot across container recreation; optionally reapply saved weights."""
import argparse
import hashlib
import json
from pathlib import Path

import httpx


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['save', 'verify'])
    parser.add_argument('--path', type=Path, default=Path('runtime/persistence-check.json'))
    args = parser.parse_args()
    with httpx.Client(base_url='http://127.0.0.1:8050/api', timeout=120, trust_env=False) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()
        datasets, models, runs = get('/datasets'), get('/models'), get('/runs')
        assert datasets and models and runs
        latest = get('/runs/' + runs[0]['id'])
        snapshot = {'datasets':digest(datasets), 'models':digest(models), 'runs':digest(runs),
                    'dataset_count':len(datasets), 'model_count':len(models), 'run_count':len(runs),
                    'records':digest(get('/datasets/' + datasets[0]['id'] + '/records?limit=100000')),
                    'latest_run_id':latest['id'], 'latest_points':digest(latest['points'])}
        if args.mode == 'save':
            args.path.parent.mkdir(parents=True, exist_ok=True)
            args.path.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
            print('Persistence snapshot saved.')
        else:
            previous = json.loads(args.path.read_text(encoding='utf-8'))
            assert snapshot == previous, (snapshot, previous)
            response = client.post('/analyze', json={'dataset_id':latest['dataset_id'],'model_id':latest['model_id'],
                                                    'start':latest['start'],'end':latest['end']})
            response.raise_for_status()
            reapplied = response.json()
            assert digest(reapplied['points']) == previous['latest_points']
            assert reapplied['metrics'] == latest['metrics']
            print(json.dumps({'persistence':'ok','datasets':len(datasets),'models':len(models),
                              'saved_runs':len(runs),'weights_reload_identical_predictions':True},indent=2))


if __name__ == '__main__':
    main()
