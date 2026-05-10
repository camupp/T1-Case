# T1-Case

# Cloud Marketplace Aggregator (IT-1 Case)

Прототип агрегатора российских облачных провайдеров. Проект собирает сырые данные о публичных облачных услугах (описание, тарифы, регионы, сертификации), нормализует их в единую структуру и готовит датасет для дальнейшей рекомендательной логики или витрины маркетплейса.

В этом репозитории сейчас реализованы два первых этапа: **сбор сырых данных** (parsers) и **нормализация** (normalization). Дальнейшие слои — рекомендатель и пользовательский интерфейс — пока не реализованы.

---

## 1. Цель и контекст

Российский облачный рынок фрагментирован: у каждого провайдера свой каталог, своя структура тарифов, разные форматы документации, разная политика по 152-ФЗ и регионам. Покупателю облачных услуг (особенно в B2B-сегменте) сложно сравнить предложения по одному набору параметров.

Проект решает эту проблему в три шага:

1. **Сбор** — парсеры обходят публичные сайты провайдеров, скачивают документы, извлекают текст и формируют raw-датасет.
2. **Нормализация** — гибридный pipeline (LLM + Python) превращает грязный raw-JSON в строго типизированные сущности `Provider` / `Service` / `ServicePricingItem`.
3. **Витрина / рекомендатель** *(в работе)* — единая выдача услуг под пользовательскую задачу (бюджет, технологический стек, требования по compliance, регион).

Текущий датасет покрывает четыре провайдера: **T1 Cloud, Selectel, Cloud.ru, VK Cloud**.

---

## 2. Текущий статус

| Этап | Статус | Что есть |
|---|---|---|
| 1. Парсеры провайдеров | Готово | 4 парсера, ~6.6 тыс. строк кода, raw-JSON для всех провайдеров |
| 2. Нормализация | Готово (MVP) | Гибридный pipeline LLM + код, 7 модулей в `normalization/core/` |
| 3. Рекомендатель / API для конечного пользователя | Не начато | — |
| 4. UI / витрина | Не начато | — |

---

## 3. Архитектура (high-level)

```
                ┌──────────────────────┐
                │  публичные сайты     │
                │  провайдеров         │
                │  (HTML + PDF/DOCX)   │
                └──────────┬───────────┘
                           │
                           ▼
            ┌──────────────────────────────┐
            │         parsers/             │   Этап 1
            │   RawParserT1.py             │   HTTP + BeautifulSoup +
            │   RawParserSelectal.py       │   pypdf, скачивание
            │   RawParserCloud.py          │   документов, извлечение
            │   RawParserVKCloud.py        │   текста, формирование
            │   base.py (общий слой)       │   service_candidates / 
            └──────────────┬───────────────┘   pricing_items_raw
                           │
                           ▼
            ┌──────────────────────────────┐
            │   data/raw/                  │
            │     t1_cloud_raw.json        │  Сырые JSON
            │     selectel_raw.json        │  по 4 провайдерам
            │     cloud_ru_raw.json        │
            │     vk_cloud_raw.json        │
            │     downloads/               │  Скачанные PDF/DOCX
            └──────────────┬───────────────┘
                           │
                           ▼
            ┌──────────────────────────────┐
            │      normalization/          │   Этап 2
            │   core/                      │   LLM = смысл услуги +
            │     config.py                │       выбор цены
            │     llm_client.py            │   Код = тарифные позиции,
            │     schemas.py               │       compliance, regions,
            │     prompts.py               │       service_id, pipeline
            │     price_normalizer.py      │
            │     normalizer.py            │
            │     main.py (FastAPI)        │
            │   normalize_file.py (CLI)    │
            └──────────────┬───────────────┘
                           │
                           ▼
            ┌──────────────────────────────┐
            │   data/normalized/           │
            │     providers.json           │
            │     services.json            │  Готовый датасет
            │     service_pricing_items.json│
            │     user_task_templates.json │
            │     parse_log.json           │
            │     errors.json              │
            └──────────────────────────────┘
                           │
                           ▼
            ┌──────────────────────────────┐
            │   рекомендатель / витрина    │   Этап 3 (TODO)
            └──────────────────────────────┘
```

---

## 4. Структура репозитория

```
IT-1 Case/
├── data/
│   ├── raw/                              ← результат парсеров
│   │   ├── t1_cloud_raw.json
│   │   ├── selectel_raw.json
│   │   ├── cloud_ru_raw.json
│   │   ├── vk_cloud_raw.json
│   │   ├── *_parse_log.json
│   │   └── downloads/                    ← скачанные PDF/DOCX
│   ├── normalized/                       ← результат нормализатора
│   │   ├── providers.json
│   │   ├── services.json
│   │   ├── service_pricing_items.json
│   │   ├── user_task_templates.json
│   │   ├── parse_log.json
│   │   └── errors.json
│   └── normalized_test/                  ← результат subset-теста
│
├── parsers/
│   ├── base.py                           ← общий HTTP/PDF/JSON-слой
│   ├── RawParserT1.py
│   ├── RawParserSelectal.py
│   ├── RawParserCloud.py
│   └── RawParserVKCloud.py
│
├── normalization/
│   ├── core/
│   │   ├── config.py                     ← .env, настройки LLM
│   │   ├── llm_client.py                 ← OpenAI-совместимый клиент
│   │   ├── schemas.py                    ← Pydantic-модели raw + normalized
│   │   ├── prompts.py                    ← SERVICE_META + PRICE_SELECTION
│   │   ├── price_normalizer.py           ← парсинг и пересчёт цен
│   │   ├── normalizer.py                 ← основной pipeline
│   │   └── main.py                       ← FastAPI (опционально)
│   ├── normalize_file.py                 ← CLI-запуск нормализации
│   ├── test_normalize_subset.py          ← быстрый тест на 3 услугах
│   └── README_Normalizatonmd.md          ← подробная документация по нормализации
│
├── documents/
│   ├── Структура_нормализованных_JSON_данных.pdf
│   └── raw_data_structure_4_providers.docx
│
├── .env                                  ← LLM_API_KEY, LLM_MODEL и т.д.
└── README.md                             ← этот файл
```

---

## 5. Этап 1. Сбор сырых данных (parsers/)

### Что делают парсеры

Каждый парсер обходит публичный сайт одного провайдера: главную страницу, страницы услуг, тарифные страницы, документацию, страницы безопасности. Загружает HTML, скачивает PDF/DOCX/XLSX, извлекает текст и собирает структурированный raw-JSON. Логика провайдера лежит в отдельном файле, а общие функции (HTTP, парсинг HTML, извлечение текста из PDF, дедупликация ссылок, сохранение JSON, логирование) — в `parsers/base.py`.

Парсеры **не нормализуют** данные. Их задача — собрать всё подряд, что относится к описанию услуги, тарифам, регионам и compliance, и аккуратно положить в JSON. Чистка и интерпретация — задача нормализатора.

### Стек

`requests`, `beautifulsoup4`, `pypdf`, `python-docx`, `openpyxl`, стандартные `dataclasses` + `typing`. HTTP-вежливость: `User-Agent` обозначает учебный проект, между запросами стоит `REQUEST_DELAY_SECONDS = 1.0`.

### Структура raw JSON

Все 4 файла используют одинаковую схему (см. `normalization/core/schemas.py` → `RawProviderFile`):

```text
provider                    ← provider_id, name, base_url, ссылки
collected_at                ← дата сбора
source_pages                ← главные страницы, с которых пошёл обход
service_candidates          ← сырые кандидаты услуг
pricing_sources             ← источники тарифов (страницы, PDF)
pricing_items_raw           ← тарифные строки: vCPU, RAM, SSD, S3, traffic, license
docs_sources                ← техническая документация
api_sources                 ← API/SDK/Terraform/CLI
security_sources            ← страницы безопасности и сертификаций
compliance_evidence_raw     ← фрагменты по 152-ФЗ, ФСТЭК, PCI DSS, ISO
region_evidence_raw         ← фрагменты по регионам, ЦОДам, AZ
parse_log                   ← URL + статус + records_added + errors
```

### Размер собранных данных

| Провайдер | service_candidates | pricing_items_raw | compliance_evidence | region_evidence |
|---|---:|---:|---:|---:|
| T1 Cloud  |  23 |   568 |  31 |  28 |
| Selectel  |  31 | 2 465 | 241 | 149 |
| Cloud.ru  |  59 | 1 264 | 143 |  20 |
| VK Cloud  |  49 | 1 132 | 357 | 191 |
| **Итого** | **162** | **5 429** | **772** | **388** |

Объём pricing-строк (5.4 тыс.) показывает, почему отбор кандидатов под услугу делается отдельно от LLM: грузить весь массив в каждый запрос невозможно ни по стоимости, ни по контексту модели.

### Запуск парсеров

```bash
# из корня проекта
python parsers/RawParserT1.py
python parsers/RawParserSelectal.py
python parsers/RawParserCloud.py
python parsers/RawParserVKCloud.py
```

Результат каждого запуска: `data/raw/<provider>_raw.json`, `data/raw/<provider>_parse_log.json`, скачанные документы в `data/raw/downloads/<provider>/`.

---

## 6. Этап 2. Нормализация (normalization/)

### Главная идея

Гибрид LLM + Python с чёткими ролями:

- **LLM делает только то, в чём он силён**: смысловой анализ грязного текста (название, категория, описание, теги) и выбор одной правильной цены из короткого списка кандидатов.
- **Код делает всё остальное**: отбор тарифных кандидатов по ключевым словам, парсинг чисел, пересчёт периодов (`руб/год → руб/мес`, `руб/час → руб/мес`), идентификаторы (`service_id` / `pricing_item_id`), regions, compliance, склейка результата.

LLM не парсит цифры из произвольного текста и не пересчитывает периоды самостоятельно — это слишком ненадёжно. Цена услуги получается так: сначала код собирает короткий список кандидатов, затем LLM выбирает один из них (`PRICE_SELECTION_PROMPT`) с обоснованием и confidence.

Если LLM не уверен (`confidence < 0.55`) или не нашёл подходящую строку — `services.price_from_rub` остаётся `null`. Это намеренно: лучше показать честный «нет данных», чем подставить чужую цену.

### Pipeline для одной услуги

```
1. LLM нормализует мету (SERVICE_META_PROMPT):
   name / category / description / tech_stack_tags / use_case_tags / pricing_model

2. Код подбирает ключевые слова услуги (например, "veeam", "postgresql",
   "kubernetes/k8s/master node/worker node", "s3/object storage/бакет").

3. Код грубо отбирает тарифные строки из pricing_items_raw по этим ключам.

4. Код нормализует каждую отобранную строку:
   - parse_structured_price() → числа из price_per_month_raw / price_value_raw / price_raw
   - parse_price() (regex) → если структурное поле пустое
   - detect_billing_period() → year / month / day / hour / minute / request / one_time
   - detect_item_type() → cpu / ram / disk / storage / traffic / ip / backup /
                          backup_device / license / request / gpu / network / other
   - normalize_price_to_month() → пересчёт в месячную цену
   - build_price_unit() → "руб/vCPU/мес", "руб/ГБ/мес", "руб/шт/мес" и т.д.

5. Дедупликация (один и тот же тариф из разных источников).

6. LLM выбирает одну строку (PRICE_SELECTION_PROMPT). Возвращает:
   selected_candidate_id, confidence, reason.

7. price_rub / price_unit выбранной строки идут в services.json как
   price_from_rub / price_unit. Все отобранные строки (с ценами и без) сохраняются
   в service_pricing_items.json.

8. compliance_tags + regions достраиваются из evidence-фрагментов через regex.

9. Ошибки одной услуги не ломают весь прогон — пишутся в errors.json.
```

### Что лежит в `core/`

| Модуль | Назначение |
|---|---|
| `config.py` | Загрузка `.env`, настройки LLM. `.env` ищется относительно `core/config.py`, поэтому запуск работает с любого cwd. |
| `llm_client.py` | OpenAI-совместимый клиент. По умолчанию — Yandex AI Studio (DeepSeek), но base_url настраиваемый. |
| `schemas.py` | Pydantic-модели для raw (`RawProviderFile`, `RawServiceCandidate`, `RawPricingItem`...) и normalized (`Provider`, `Service`, `ServicePricingItem`, `ParseLogRecord`, `NormalizationError`, `UserTaskTemplate`). |
| `prompts.py` | `SERVICE_META_PROMPT` (мета услуги) и `PRICE_SELECTION_PROMPT` (выбор цены). |
| `price_normalizer.py` | Парсинг чисел, period detection, item_type detection, пересчёт `руб/год → руб/мес` и т.д., построение `price_unit`. |
| `normalizer.py` | Pipeline на уровне одной услуги и одного провайдера. Точки входа: `normalize_provider_file()`, `normalize_many_provider_files()`. |
| `main.py` | FastAPI. Endpoints: `GET /health`, `GET /test-llm`, `POST /normalize-provider-file`, `POST /normalize-provider-files`. |

### Output: что в data/normalized/

| Файл | Что хранит | Зачем нужен |
|---|---|---|
| `providers.json` | Провайдеры (id, name, регионы, 152-ФЗ) | Справочник для UI |
| `services.json` | Облачные услуги (одна услуга = одна карточка) | Витрина и ранжирование |
| `service_pricing_items.json` | Тарифные позиции (vCPU/RAM/storage/traffic/license) | Калькуляция бюджета |
| `user_task_templates.json` | Тестовые пользовательские задачи | Проверка рекомендательной логики |
| `parse_log.json` | URL + статус сбора | Воспроизводимость |
| `errors.json` | Ошибки одной услуги | Отладка пайплайна |

Принципиальное разделение: `services.json` хранит **услугу как карточку** (одна строка цены — для индикативного отображения), `service_pricing_items.json` — **все тарифные позиции** услуги (для точного расчёта бюджета по конфигурации).

Подробная документация: [`normalization/README_Normalizatonmd.md`](normalization/README_Normalizatonmd.md).

### Запуск нормализации

```bash
cd normalization

# полный прогон по 4 провайдерам
python normalize_file.py

# только один провайдер
python normalize_file.py --provider t1-cloud

# без LLM (фоллбек на детерминированные значения)
python normalize_file.py --dry-run

# отключить LLM-выбор цены
python normalize_file.py --no-llm-price-selection

# быстрый тест на 3 услугах (Veeam, PostgreSQL, Kubernetes)
python test_normalize_subset.py

# FastAPI-обёртка
uvicorn core.main:app --reload
```

---

## 7. Пример результата нормализации

### `services.json`

```json
{
  "service_id": "t1-cloud-managed-kubernetes",
  "provider_id": "t1-cloud",
  "name": "Managed Kubernetes",
  "category": "DevOps",
  "description": "Управляемый сервис для развертывания, управления и масштабирования контейнеризированных приложений в отказоустойчивых кластерах Kubernetes.",
  "tech_stack_tags": ["kubernetes", "docker", "container-registry", "ingress-controller", "autoscale"],
  "use_case_tags": ["app-deployment", "container-orchestration", "high-availability", "auto-scaling"],
  "compliance_tags": ["152-FZ", "FSTEC", "ISPDN", "ISPDN_UZ_1"],
  "regions": ["Moscow", "Russia"],
  "pricing_model": "pay-as-you-go",
  "price_from_rub": 2070.0,
  "price_unit": "руб/шт/мес",
  "service_url": "https://t1-cloud.ru/.../Prilozhenie_6.8_Opisanie_Managed_Service_for_Kubernetes.pdf",
  "price_source": "llm_selected",
  "price_confidence": 0.9,
  "price_evidence": "Managed Service for Kubernetes, 1 мастер-нода, однозональный кластер, конфигурация Tiny шт 0,04791667 2070,00"
}
```

### `service_pricing_items.json` (фрагмент)

```json
{
  "pricing_item_id": "t1-cloud-managed-kubernetes-managed-service-for-kubernetes-1-master-0",
  "service_id": "t1-cloud-managed-kubernetes",
  "provider_id": "t1-cloud",
  "item_name": "Managed Service for Kubernetes, 1 мастер-нода, однозональный кластер, конфигурация Tiny",
  "item_type": "other",
  "price_rub": 2070.0,
  "price_unit": "руб/шт/мес",
  "billing_period": "month",
  "price_source": "structured",
  "price_confidence": 1.0
}
```

### Результат subset-теста (3 услуги T1 Cloud)

| Услуга | price_from_rub | price_unit | price_source | confidence |
|---|---:|---|---|---:|
| Резервное копирование Veeam | 1 582.44 | руб/шт/мес | `llm_selected` | 0.9 |
| Managed PostgreSQL | `null` | — | `none` | 0.0 |
| Managed Kubernetes | 2 070.00 | руб/шт/мес | `llm_selected` | 0.9 |

`null` для PostgreSQL — это **корректное поведение**: в собранных pricing-кандидатах не оказалось базовой тарификации самого PostgreSQL-сервиса (только Кибер Бэкап-строки), и LLM осознанно отказался подставлять чужую цену с пояснением: *«имеющиеся строки относятся к backup услугам, не к самой услуге Managed PostgreSQL»*. Это и есть главная ценность LLM-выбора — отказ от ложно-минимальной цены.

---

## 8. Технологии

| Слой | Технологии |
|---|---|
| Парсинг | Python 3.10+, `requests`, `beautifulsoup4`, `pypdf`, `python-docx`, `openpyxl` |
| Нормализация | `pydantic`, `pydantic-settings` |
| LLM | OpenAI-совместимый клиент. Текущая конфигурация — **Yandex AI Studio**, модель **DeepSeek-V3** (`gpt://.../deepseek-v32/latest`). `base_url` и модель настраиваются через `.env`. |
| API | `fastapi`, `uvicorn` (опционально) |
| Хранение | JSON-файлы в `data/`. Под маркетплейс далее напрашивается переезд в Postgres / поисковый индекс. |

### .env

```env
LLM_API_KEY=...
LLM_BASE_URL=https://llm.api.cloud.yandex.net/v1
LLM_MODEL=gpt://<folder>/deepseek-v32/latest
```

---

## 9. Ключевые проектные решения

1. **Разделить парсинг и нормализацию.** Парсер не пытается «понимать», что собирает. Это упрощает добавление нового провайдера и делает повторяемой нормализацию по одному и тому же raw-JSON без повторного похода в сеть.

2. **Гибрид LLM + код вместо чистого LLM-pipeline.** Единое место для парсинга чисел и пересчёта периодов в Python даёт стабильность. LLM хорошо работает с текстом, плохо — с математикой и единицами измерения.

3. **Один LLM-запрос на цену одной услуги.** Раньше была попытка отдавать в LLM весь `pricing_items_raw` или брать `min()` по всем тарифам — оба подхода дают мусор (LLM теряется в шуме, min() цепляет цену backup'а вместо базовой). Сейчас: код сужает до короткого списка кандидатов по ключевым словам услуги, LLM выбирает один с обоснованием.

4. **`null` лучше неправды.** Если LLM не уверен в выборе цены — `price_from_rub = null`. Это сразу видно в выдаче и не вводит пользователя в заблуждение.

5. **Двухуровневая модель цены.** `services.json` хранит индикативную цену для карточки, `service_pricing_items.json` — все тарифные позиции для расчёта бюджета по конкретной конфигурации. Это снимает конфликт «одна цифра в карточке vs точный калькулятор».

6. **Pydantic-схемы как контракт.** Все промежуточные данные — типизированные модели (`RawProviderFile`, `Service`, `ServicePricingItem`). Это ловит проблемы данных на границе модулей, а не в середине pipeline.

7. **Errors не ломают прогон.** Падение нормализации одной услуги пишется в `errors.json` и не прерывает обработку остальных. Pipeline остаётся стабильным даже при «грязных» raw-данных.

---

## 10. Что дальше (Roadmap)

| Этап | Задача |
|---|---|
| **3.0 — Качество данных** | Дофильтровать пустые pricing-items (без `item_name_raw`), привести `service_id` к latin-only slug, доработать `detect_item_type` под master/worker nodes |
| **3.1 — Хранилище** | Перенос нормализованного датасета в Postgres + полнотекстовый индекс (Meilisearch / OpenSearch) |
| **3.2 — Рекомендатель** | Подбор подходящих услуг под `UserTaskTemplate` (бюджет, tech_stack, compliance, регион), explain-выдача |
| **3.3 — Витрина** | Карточки услуг, фильтры (категория / провайдер / 152-ФЗ / регион / ценовая вилка), калькулятор стоимости по конфигурации |
| **3.4 — Расширение** | Новые провайдеры (Yandex Cloud, MTS Cloud, MWS, СберClouds) — добавление сводится к новому файлу в `parsers/` |

---

## 11. Что важно помнить (для презентаций / отчётов)

- В нормализатор **не передаётся** весь raw JSON. LLM видит только короткий контекст услуги или короткий список тарифных кандидатов.
- На цену одной услуги делается **один** LLM-запрос.
- `services.price_from_rub` — это **выбранная** LLM строка с обоснованием, а не `min()` по всему датасету.
- `service_pricing_items.json` хранит **все** отобранные тарифные позиции — даже без цены.
- При неуверенности LLM (confidence < 0.55) цена в `services.json` остаётся `null`.
- Ошибки одной услуги не ломают прогон.

---

## 12. Краткая выжимка (одним абзацем для слайда)

> Прототип агрегатора облачных провайдеров, собирающий публичные данные по T1 Cloud, Selectel, Cloud.ru и VK Cloud (162 кандидата услуг и 5 400+ тарифных строк) и нормализующий их в единый датасет через гибридный pipeline LLM + Python. LLM (DeepSeek-V3 через Yandex AI Studio) отвечает за смысловую нормализацию услуги и выбор одной правильной цены из коротких кандидатов; код — за отбор кандидатов, парсинг чисел, пересчёт периодов и управление pipeline'ом. Все данные строго типизированы через Pydantic-схемы. Ошибки изолированы в `errors.json`, а отсутствие надёжной цены даёт честный `null` вместо ложно-минимального значения. Готовый датасет (`providers.json`, `services.json`, `service_pricing_items.json`) — основа для рекомендательного движка и витрины маркетплейса.
