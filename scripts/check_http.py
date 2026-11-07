"""Integration checks against the five running containers; creates only disposable test data."""
import csv
import io
import json

import httpx


def main():
    with httpx.Client(timeout=120, trust_env=False) as client:
        ui = 'http://127.0.0.1:8050'
        storage = 'http://127.0.0.1:8102'
        collector = 'http://127.0.0.1:8101'
        for port in (8050, 8101, 8102, 8103, 8104):
            response = client.get(f'http://127.0.0.1:{port}/health')
            assert response.status_code == 200 and response.json()['status'] == 'ok'
        a = client.get(collector + '/sources/iqds-demo/batch', params={'offset':0,'limit':3}).json()
        b = client.get(collector + '/sources/iqds-demo/batch', params={'offset':a['next_offset'],'limit':3}).json()
        assert a['records'] != b['records'] and a['next_offset'] == 3 and b['next_offset'] == 6
        assert client.get(collector + '/sources/missing/batch').status_code == 404
        assert client.post(ui + '/api/upload', files={'file':('bad.csv',b'wrong,header\n1,2')}).status_code == 422
        assert client.post(ui + '/api/upload', files={'file':('bad.csv',b'timestamp,sensor,value,label\n2025-01-01,s1,nan,0')}).status_code == 422
        content = b'timestamp,sensor,value\n2025-01-01T00:00:00Z,probe,23\n2025-01-01T00:10:00Z,probe,24\n'
        response = client.post(ui + '/api/upload', files={'file':('http-check.csv',content)})
        assert response.status_code == 201, response.text
        dataset_id = response.json()['id']
        try:
            rows = client.get(ui + f'/api/datasets/{dataset_id}/records').json()
            assert rows['total'] == 2 and rows['records'][0]['label'] is None
            assert client.patch(ui + f'/api/datasets/{dataset_id}', json={'name':'HTTP check renamed'}).status_code == 200
            assert client.put(storage + f'/datasets/{dataset_id}/record', json={'timestamp':'2025-01-01','sensor':'probe','value':25}).status_code == 200
            rows = client.get(storage + f'/datasets/{dataset_id}/records').json()
            assert rows['total'] == 2 and rows['records'][0]['value'] == 25
            assert client.delete(storage + f'/datasets/{dataset_id}/record', params={'sensor':'probe','time':'2025-01-01'}).status_code == 200
        finally:
            assert client.delete(ui + f'/api/datasets/{dataset_id}').status_code == 200
        runs = client.get(ui + '/api/runs').json()
        assert runs, 'Run scripts/demo.py first'
        run = client.get(ui + '/api/runs/' + runs[0]['id']).json()
        report = client.get(ui + '/api/runs/' + run['id'] + '/report')
        assert report.status_code == 200 and '<svg' in report.text and 'Контур' in report.text
        result_csv = client.get(ui + '/api/runs/' + run['id'] + '/csv')
        rows = list(csv.DictReader(io.StringIO(result_csv.text.lstrip('\ufeff'))))
        assert len(rows) == run['total_points']
        assert sum(row['prediction']=='1' for row in rows) == run['anomaly_points']
        rejected = client.post(ui + '/api/analyze', json={'dataset_id':run['dataset_id'],'model_id':run['model_id'],
                                                        'start':'2025-06-20','end':'2025-07-01'})
        assert rejected.status_code == 422, rejected.text
        print(json.dumps({'health':5,'batch_sequence':'ok','upload_validation':'ok','crud':'ok',
                          'report':'ok','csv_rows':len(rows),'period_guard':'ok'}, indent=2))


if __name__ == '__main__':
    main()
