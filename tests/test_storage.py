import importlib

from fastapi.testclient import TestClient


def test_crud_revision_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    module = importlib.import_module('storage.app')
    client = TestClient(module.app)
    created = client.post('/datasets', json={'name': 'Тестовый набор'})
    assert created.status_code == 201
    dataset_id = created.json()['id']
    record = {'timestamp':'2025-01-01T00:00:00Z','sensor':'s1','value':23.,'label':0}
    assert client.put(f'/datasets/{dataset_id}/record', json=record).status_code == 200
    record['value'] = 25.
    assert client.put(f'/datasets/{dataset_id}/record', json=record).json()['revision'] == 2
    rows = client.get(f'/datasets/{dataset_id}/records').json()
    assert rows['total'] == 1 and rows['records'][0]['value'] == 25
    assert client.patch(f'/datasets/{dataset_id}', json={'name':'Переименован'}).status_code == 200
    # A fresh app instance opens the same persisted SQLite database.
    module = importlib.reload(module)
    client = TestClient(module.app)
    assert client.get(f'/datasets/{dataset_id}').json()['name'] == 'Переименован'
    assert client.delete(f'/datasets/{dataset_id}/record', params={'sensor':'s1','time':record['timestamp']}).status_code == 200
    assert client.get(f'/datasets/{dataset_id}/records').json()['total'] == 0
    assert client.delete(f'/datasets/{dataset_id}').status_code == 200
    assert client.get(f'/datasets/{dataset_id}').status_code == 404
