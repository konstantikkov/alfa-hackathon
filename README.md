# PII Security Proxy

Модуль безопасности персональных данных для интеграции с LLM: находит ПДн в
русскоязычном тексте, маскирует их (частично / токенами / синтетическими
значениями) и восстанавливает исходные значения в ответе модели.

```
SYSTEM -> [PII Security Proxy] -> безопасный текст -> LLM -> ответ -> [Proxy] -> SYSTEM
```

Две реализации одного контракта: `app/` — референс на Python (FastAPI, полная
морфология), `go-service/` — высокопроизводительное ядро на Go (stdlib).
Подробное описание устройства — в [ARCH.md](ARCH.md).

## Запуск локально

Нужны Docker и Docker Compose v2.

```bash
cp .env.example .env        # задать PII_REDIS_PASSWORD и PII_MONGO_PASSWORD
docker compose up --build   # приложение: http://localhost:8080
```

Проверка:

```bash
curl http://localhost:8080/health

# маскирование (первый вызов с новым payload_id)
curl -X POST http://localhost:8080/process \
  -H "Content-Type: application/json" \
  -d '{"payload":"Клиент Иванов Иван Иванович, паспорт 0000 000000","payload_id":"demo-1"}'

# демаскирование: тот же payload_id, на вход — результат первого вызова
curl -X POST http://localhost:8080/process \
  -H "Content-Type: application/json" \
  -d '{"payload":"<результат первого вызова>","payload_id":"demo-1"}'
```

`GET /` — одностраничный UI-инспектор пайплайна; `GET /metrics` — Prometheus.

Go-вариант (отдельный стек со своими Redis/Mongo, порт 8080):

```bash
cd go-service
cp ../.env.example .env     # те же переменные
docker compose up --build
```

## Запуск без Docker (для разработки)

```bash
pip install -r requirements.txt -r requirements-dev.txt
PII_REDIS_URL= PII_MONGO_URL= uvicorn app.main:app --port 8080   # in-memory store
```

Пустые `PII_REDIS_URL`/`PII_MONGO_URL` включают in-memory-хранилище — только
для локальной разработки в один процесс.

## Тесты

```bash
python -m pytest tests/ -q          # Python: unit / integration / failover / regression
cd go-service && go test ./...      # Go
```

## Конфигурация

Все параметры — переменные окружения (`app/config/settings.py`):
`PII_REDIS_URL`, `PII_MONGO_URL`, `PII_DEFAULT_MASKING_MODE`
(`partial|token|synthetic`), `PII_MAPPING_TTL_SECONDS`,
`PII_FINGERPRINT_SECRET` (HMAC-ключ отпечатков), `PII_MAX_BODY_BYTES`.
Политики потребителей — `app/config/consumers.yaml`.
