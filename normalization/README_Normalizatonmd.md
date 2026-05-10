# Cloud Marketplace Normalizer

Проект нормализует сырые JSON-данные облачных провайдеров для прототипа маркетплейса облачных услуг.

Главная идея:

- **LLM нормализует смысл услуги** (name / category / description / tech_stack_tags / use_case_tags / pricing_model).
- **LLM выбирает цену услуги** из короткого списка тарифных кандидатов, подготовленных кодом.
- **Код** строго отбирает тарифные строки-кандидаты, нормализует тарифные позиции, считает идентификаторы, тянет regions и compliance.

---

## 1. Что подается на вход

Raw JSON-файлы, собранные парсерами для четырех провайдеров:

```text
data/raw/
  t1_cloud_raw.json
  selectel_raw.json
  cloud_ru_raw.json
  vk_cloud_raw.json
```

Базовая структура raw-файла:

```text
provider
collected_at
source_pages
service_candidates
pricing_sources
pricing_items_raw
docs_sources
api_sources
security_sources
compliance_evidence_raw
region_evidence_raw
parse_log
```

| Поле | Что содержит |
|---|---|
| `provider` | Информация о провайдере: `provider_id`, `name`, `base_url`, ссылки |
| `collected_at` | Дата и время сбора raw-данных |
| `source_pages` | Стартовые страницы, с которых выполнялся сбор |
| `service_candidates` | Сырые кандидаты облачных сервисов |
| `pricing_sources` | Источники с тарифами |
| `pricing_items_raw` | Сырые тарифные позиции: CPU, RAM, SSD, storage, traffic и т.д. |
| `docs_sources` | Техническая документация |
| `api_sources` | API, SDK, Terraform, CLI-документация |
| `security_sources` | Страницы безопасности и сертификации |
| `compliance_evidence_raw` | Фрагменты по 152-ФЗ, ФСТЭК, PCI DSS, ISO и т.д. |
| `region_evidence_raw` | Фрагменты с регионами, ЦОДами и availability zones |
| `parse_log` | Логи парсинга URL |

---

## 2. Что создается на выходе

```text
data/normalized/
  providers.json
  services.json
  service_pricing_items.json
  user_task_templates.json
  parse_log.json
  errors.json
```

| Файл | Что хранит | Зачем нужен |
|---|---|---|
| `providers.json` | Провайдеры | Справочник |
| `services.json` | Нормализованные услуги | Витрина и ранжирование |
| `service_pricing_items.json` | Тарифные позиции (vCPU, RAM, storage, traffic) | Расчет бюджета |
| `user_task_templates.json` | Тестовые пользовательские задачи | Проверка рекомендаций |
| `parse_log.json` | URL, дата сбора, статус | Воспроизводимость |
| `errors.json` | Ошибки нормализации | Отладка пайплайна |

Главное правило:

```text
services.json хранит облачные услуги (одна услуга = одна карточка).
service_pricing_items.json хранит тарифные позиции (одна услуга может иметь много позиций).
```

Пример:

```text
services.json
  t1-cloud-managed-service-for-postgresql

service_pricing_items.json
  t1-cloud-managed-service-for-postgresql-cpu-0
  t1-cloud-managed-service-for-postgresql-ram-1
  t1-cloud-managed-service-for-postgresql-disk-2
```

---

## 3. Роль LLM и роль кода

### LLM делает только две вещи

1. Нормализует смысл услуги (`SERVICE_META_PROMPT`):

   ```text
   name
   category
   description
   tech_stack_tags
   use_case_tags
   pricing_model
   ```

2. Выбирает цену услуги (`PRICE_SELECTION_PROMPT`):
   - получает короткий список заранее отобранных тарифных строк;
   - возвращает `selected_candidate_id` + `confidence` + `reason`;
   - один LLM-запрос на цену одной услуги.

LLM не парсит цену из произвольного текста и не пересчитывает периоды.

### Код делает всё остальное

```text
provider record
service_id / pricing_item_id
отбор тарифных кандидатов по ключевым словам
нормализация одной тарифной позиции (price/unit/billing_period/item_type)
руб/год → руб/мес, руб/час → руб/мес и т.д.
regions
compliance_tags (152-FZ, FSTEC, ISPDN, PCI DSS, ISO 27001 и др.)
parse_log
errors
```

---

## 4. Структура проекта

```text
IT-1 Case/
├── data/
│   ├── raw/              ← вход: <provider>_raw.json
│   ├── normalized/       ← выход: providers.json, services.json и т.д.
│   └── normalized_test/  ← выход test_normalize_subset.py
├── normalization/
│   ├── core/
│   │   ├── config.py
│   │   ├── llm_client.py
│   │   ├── schemas.py
│   │   ├── prompts.py
│   │   ├── price_normalizer.py
│   │   ├── normalizer.py
│   │   └── main.py            ← FastAPI
│   ├── normalize_file.py      ← CLI
│   ├── test_normalize_subset.py
│   └── README_Normalizatonmd.md
└── .env
```

---

## 5. Какой файл за что отвечает

### `schemas.py`

Pydantic-схемы raw и normalized данных:

```text
RawProviderFile
RawProviderMeta
RawServiceCandidate
RawPricingItem
RawEvidenceItem
Provider
Service
ServicePricingItem
ParseLogRecord
NormalizationError
UserTaskTemplate
```

### `prompts.py`

Содержит два промпта:

- `SERVICE_META_PROMPT` + `SERVICE_META_USER_TEMPLATE` — смысловая нормализация услуги.
- `PRICE_SELECTION_PROMPT` + `PRICE_SELECTION_USER_TEMPLATE` — выбор одной правильной цены из коротких кандидатов.

### `price_normalizer.py`

Нормализация одной тарифной позиции кодом:

```text
parse_price()
parse_structured_price()
detect_billing_period()
detect_item_type()
detect_resource_unit()
normalize_price_to_month()
build_price_unit()
normalize_price_record()
```

Здесь же делается пересчет периода в месяц (`руб/год → руб/мес`, `руб/час → руб/мес` и т.д.).

### `normalizer.py`

Главный pipeline. На уровне одной услуги выполняет:

1. `normalize_service_meta_with_llm()` — LLM делает мету.
2. `collect_price_candidates_for_llm()` — код грубо отбирает тарифные строки по ключевым словам услуги.
3. `normalize_single_pricing_item()` — код нормализует каждую отобранную тарифную позицию.
4. `deduplicate_pricing_items()` — убирает дубликаты.
5. `select_service_price_with_llm()` — LLM выбирает одну строку, ее `price_rub`/`price_unit` идут в `services.json`.

Точки входа:

```text
normalize_provider_file()
normalize_many_provider_files()
```

### `normalize_file.py`

CLI-запускатель. Читает raw из `data/raw/`, пишет результат в `data/normalized/`.

### `main.py`

FastAPI:

```text
GET  /health
GET  /test-llm
POST /normalize-provider-file
POST /normalize-provider-files
```

---

## 6. Установка зависимостей

```bash
python -m venv .venv
```

Windows PowerShell:

```bash
.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

Минимальный `requirements.txt`:

```text
fastapi
uvicorn[standard]
pydantic
pydantic-settings
openai
python-dotenv
```

---

## 7. Настройка `.env`

```env
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=https://llm.api.cloud.yandex.net/v1
LLM_MODEL=deepseek-v3
```

---

## 8. Как запустить нормализацию

Все скрипты запускаются из папки `normalization/`:

```bash
cd normalization
```

Полный прогон:

```bash
python normalize_file.py
```

Только один провайдер:

```bash
python normalize_file.py --provider t1-cloud
```

Допустимые провайдеры: `t1-cloud`, `selectel`, `cloud-ru`, `vk-cloud`.

Без LLM (фоллбек на детерминированные значения):

```bash
python normalize_file.py --dry-run
```

Отключить LLM-выбор цены (services.price_from_rub останется null):

```bash
python normalize_file.py --no-llm-price-selection
```

Пропускать отсутствующие raw-файлы:

```bash
python normalize_file.py --skip-missing
```

Подробные логи:

```bash
python normalize_file.py --verbose
```

Быстрый тест на трех услугах T1 Cloud (Veeam, PostgreSQL, Kubernetes):

```bash
python test_normalize_subset.py
```

Результат пишется в `data/normalized_test/`.

---

## 9. Как запустить API

Запускать из папки `normalization/`:

```bash
cd normalization
uvicorn core.main:app --reload
```

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

---

## 10. API endpoints

### `GET /health`

```json
{ "status": "ok" }
```

### `GET /test-llm`

```json
{ "llm_response": "ok" }
```

### `POST /normalize-provider-file`

Нормализует один raw JSON провайдера.

Query-параметры:

- `dry_run` (bool, default false) — отключает все вызовы LLM.
- `llm_price_selection` (bool, default true) — отключает LLM-выбор цены.

На вход:

```json
{
  "provider": {},
  "collected_at": "2026-05-09T17:57:03+00:00",
  "service_candidates": [],
  "pricing_items_raw": [],
  "compliance_evidence_raw": [],
  "region_evidence_raw": [],
  "parse_log": []
}
```

На выход:

```json
{
  "provider": {},
  "services": [],
  "service_pricing_items": [],
  "parse_log": [],
  "errors": []
}
```

### `POST /normalize-provider-files`

То же самое, но принимает список raw-файлов и возвращает агрегированный результат:

```json
{
  "providers": [],
  "services": [],
  "service_pricing_items": [],
  "parse_log": [],
  "errors": []
}
```

---

## 11. Как работает pipeline

```text
1. Загружается raw JSON провайдера.
2. Из provider создается запись для providers.json (regions + 152-FZ из evidence).
3. Для каждого service_candidate:
   3.1 LLM нормализует смысл услуги (SERVICE_META_PROMPT).
   3.2 Код подбирает ключевые слова услуги и грубо отбирает кандидатов из pricing_items_raw.
   3.3 Код нормализует каждую отобранную тарифную позицию (price_rub, unit, period, item_type).
   3.4 Дедупликация тарифных позиций.
   3.5 LLM выбирает одну тарифную строку (PRICE_SELECTION_PROMPT). Если confidence < 0.55 или не выбрано — цена остается null.
   3.6 price_rub / price_unit выбранной строки идут в services.json.
4. compliance_evidence_raw + region_evidence_raw добавляют compliance_tags и regions.
5. parse_log переносится в parse_log.json.
6. Ошибки сохраняются в errors.json (не ломают весь прогон).
```

---

## 12. Пример services.json

```json
{
  "service_id": "t1-cloud-managed-service-for-postgresql",
  "provider_id": "t1-cloud",
  "name": "Managed Service for PostgreSQL",
  "category": "Database",
  "description": "Сервис управления объектно-реляционными базами данных PostgreSQL.",
  "tech_stack_tags": ["postgresql", "database", "cluster"],
  "use_case_tags": ["database-management", "web-hosting"],
  "compliance_tags": ["152-FZ", "FSTEC"],
  "regions": ["Russia", "Moscow"],
  "pricing_model": "pay-as-you-go",
  "price_from_rub": 1823.5,
  "price_unit": "руб/ГБ/мес",
  "support_level": null,
  "service_url": "https://example.com/postgresql",
  "source_url": "https://example.com/postgresql",
  "parsed_at": "2026-05-09T17:57:03+00:00",
  "is_synthetic": false,
  "price_source": "llm_selected",
  "price_confidence": 0.9,
  "price_evidence": "PostgreSQL storage 1 ГБ — 1 823,50 руб/мес"
}
```

---

## 13. Пример service_pricing_items.json

```json
{
  "pricing_item_id": "t1-cloud-managed-service-for-postgresql-storage-0",
  "service_id": "t1-cloud-managed-service-for-postgresql",
  "provider_id": "t1-cloud",
  "item_name": "PostgreSQL storage",
  "item_type": "storage",
  "price_rub": 1823.5,
  "price_unit": "руб/ГБ/мес",
  "billing_period": "month",
  "region": "Russia",
  "configuration_tags": [],
  "source_url": "https://example.com/rates",
  "raw_text": "Исходный фрагмент тарифной строки",
  "parsed_at": "2026-05-09T17:57:03+00:00",
  "is_synthetic": false,
  "price_source": "structured",
  "price_confidence": 1.0,
  "price_evidence": "PostgreSQL storage 1 ГБ — 1 823,50 руб/мес"
}
```

---

## 14. Что важно помнить

1. В LLM не передается весь raw JSON. Только короткий контекст услуги или короткий список кандидатов на цену.
2. На цену одной услуги делается ровно один LLM-запрос.
3. `services.price_from_rub` берется из выбранной LLM строки. Минимум по всем тарифам не считается.
4. Если LLM не уверен (confidence < 0.55) — `price_from_rub` остается `null`.
5. `service_pricing_items.json` хранит ВСЕ отобранные тарифные позиции, даже без цены.
6. Ошибки одной услуги не должны ломать весь прогон — пишутся в `errors.json`.

---

## 15. Поля для отладки

В `Service` и `ServicePricingItem` есть служебные поля, чтобы понимать откуда взялась цена:

```text
price_source: structured | regex | llm_selected | none
price_confidence: 0.0–1.0
price_evidence: исходный фрагмент текста
```

Это удобно при ручной проверке итоговых JSON.
