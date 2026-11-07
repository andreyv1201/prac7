# API и конфигурации

Все запросы JSON, кроме загрузки файла (`multipart/form-data`) и выгрузки HTML/CSV. У всех сервисов есть `GET /health`, интерактивное описание `GET /docs` и схема `GET /openapi.json`.

| Сервис | Метод и путь | Назначение |
| --- | --- | --- |
| Collector | GET `/sources` | Характеристики доступных источников |
| Collector | GET `/sources/{id}/batch?offset=0&limit=5000` | Пакет записей, следующий offset и признак конца |
| Storage | POST `/datasets` | Создать набор: name, source_id (опционально) |
| Storage | GET `/datasets`, GET `/datasets/{id}` | Список и summary набора |
| Storage | PATCH `/datasets/{id}` | Изменить название |
| Storage | DELETE `/datasets/{id}` | Удалить набор, измерения и его результаты |
| Storage | POST `/datasets/{id}/records` | Пакетная запись/обновление измерений |
| Storage | GET `/datasets/{id}/records` | Фильтры sensor/start/end, пагинация offset/limit |
| Storage | PUT `/datasets/{id}/record` | Создать или изменить одно измерение |
| Storage | DELETE `/datasets/{id}/record?sensor=...&time=...` | Удалить одно измерение |
| Storage | POST `/runs` | Сохранить результат анализа |
| Storage | GET `/runs`, GET `/runs/{id}` | История и полный результат |
| ML Service | POST `/models` | Создать конфигурацию новой версии модели |
| ML Service | POST `/models/{id}/train` | Запустить обучение, HTTP 202 |
| ML Service | GET `/models`, GET `/models/{id}` | Состояние, параметры, история обучения |
| ML Service | POST `/models/{id}/predict` | Применить модель и сохранить запуск |
| Web Master | GET `/api/sources`, `/api/datasets`, `/api/models`, `/api/runs` | Списки для интерфейса |
| Web Master | POST `/api/import` | Полный импорт или следующий пакет из Collector |
| Web Master | POST `/api/upload` | Импорт своего CSV, поле file |
| Web Master | POST `/api/train` | Создать версию и запустить обучение |
| Web Master | POST `/api/analyze` | Проанализировать выбранный период |
| Web Master | GET `/api/models/{id}` | Проверить ход обучения |
| Web Master | GET `/api/runs/{id}` | Открыть сохранённый запуск |
| Web Master | GET `/api/runs/{id}/report` | Автономный HTML-отчёт |
| Web Master | GET `/api/runs/{id}/csv` | Все точки, метки, оценки и решения |
| Web Master | GET `/api/system` | Доступность трёх зависимостей и самого мастера |
| Visualization | GET `/` | Веб-интерфейс |
| Visualization | `/api/*` | Прокси в Web Master с ограничением размера запроса |

## Обучение

`POST http://127.0.0.1:8104/api/train`:

```json
{
  "dataset_id": "идентификатор загруженного набора",
  "sensor": "66584fddd2a6d39f779af0a3",
  "name": "LSTM — контур 1",
  "train_start": "2025-06-13",
  "train_end": "2025-06-22",
  "calibration_start": "2025-06-22",
  "calibration_end": "2025-06-29",
  "window": 144,
  "epochs": 120,
  "quantile": 0.99,
  "seed": 42
}
```

Ответ содержит id модели и статус. Опрос `GET /api/models/{id}` возвращает `created → queued → training → ready` либо `failed/interrupted`, epoch, history и диагностическое сообщение. Можно обучать только одну модель одновременно. Чтобы переобучить, создайте новую версию; готовые артефакты не перезаписываются.

## Анализ

```json
{
  "dataset_id": "идентификатор набора",
  "model_id": "идентификатор готовой модели",
  "start": "2025-07-20",
  "end": "2025-09-09"
}
```

`POST /api/analyze` возвращает идентификатор запуска, метрики и массив points. Точка содержит `timestamp,value,label,reconstructed,score,prediction`. Недоступный score/prediction — JSON null, а не нулевой прогноз. Конец периода исключён. Число срабатываний — число точек, а не число независимых событий неисправности.

## Ошибки

- 400 — недопустимое смещение источника;
- 404 — источник, набор, модель или запуск не найдены;
- 409 — модель ещё не готова или исполнитель занят;
- 413 — превышен размер файла/набора;
- 422 — некорректная дата, метка, CSV, пересечение периодов, другой шаг ряда, изменение обучающего набора;
- 503 — зависимый сервис недоступен.

Ошибка содержит поле `detail`. Ошибки фонового обучения записываются в summary модели со статусом failed. Журналы HTTP-запросов и выдачи пакетов сохраняются в собственном томе сервиса. Неожиданные ошибки не раскрывают traceback пользователю.

## Конфигурации и сохранение

Файлы `configs/{collector,storage,ml_service,web_master,visualization}.json` задают источники, адреса, ограничения, папки состояния и параметры сети. `SERVICE_CONFIG` выбирает JSON; `STATE_DIR`, `STORAGE_URL`, `COLLECTOR_URL`, `ML_URL`, `MASTER_URL` переопределяют среду развёртывания.

В Compose у каждого сервиса свой постоянный том. Удаление контейнера не удаляет том. Обычный `stop.cmd` использует `docker compose down` с сохранением данных.
