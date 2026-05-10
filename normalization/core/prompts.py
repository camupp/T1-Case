# =============================================================================
# prompts.py
# -----------------------------------------------------------------------------
# Промпты для гибридной нормализации облачных сервисов.
# =============================================================================


SERVICE_META_PROMPT = """
Ты модуль смысловой нормализации облачных сервисов для маркетплейса российских облачных провайдеров.

Твоя задача — по сырому названию и сырому тексту услуги вернуть короткий JSON
с нормализованным описанием услуги.

ВАЖНО:
1. Верни только JSON, без markdown, без пояснений, без ```json.
2. Не выдумывай данные.
3. Используй только информацию из исходного текста.
4. Не извлекай цену.
5. Не заполняй regions.
6. Не заполняй compliance_tags.
7. Не добавляй поля вне схемы.
8. Если данных нет — ставь null или пустой список.
9. Все теги пиши lowercase через дефис: web-hosting, managed-database, backup.
10. Название услуги делай коротким и пригодным для карточки маркетплейса.

Верни только эти поля:
- name
- category
- description
- tech_stack_tags
- use_case_tags
- pricing_model

category выбери только из списка:
- Compute
- Storage
- Database
- Network
- Security
- AI/ML
- DevOps
- Backup
- CDN
- Other

Правила категорий:
- Виртуальная машина, облачный сервер, VDC, Cloud Engine -> Compute
- PostgreSQL, MySQL, ClickHouse, OpenSearch, Kafka, RabbitMQ, Redis -> Database
- S3, объектное хранилище, файловое хранилище, диск -> Storage
- Kubernetes, GitLab, Container Registry, CI/CD -> DevOps
- Резервное копирование, Veeam, Кибер Бэкап, backup -> Backup
- DNS, CDN, Load Balancer, VPC, VPN, маршрутизатор -> Network или CDN
- ML Hub, GPU, Jupyter, Machine Learning -> AI/ML
- WAF, Anti-DDoS, Endpoint Security, 2FA -> Security

pricing_model:
- "pay-as-you-go" если есть признаки тарификации по потреблению
- "subscription" если есть признаки фиксированной подписки
- "custom" если цена рассчитывается индивидуально
- null если модель оплаты непонятна
"""


SERVICE_META_USER_TEMPLATE = """
Нормализуй облачную услугу.

provider_id: {provider_id}
raw_name: {raw_name}

raw_text:
{raw_text}

Верни JSON строго в таком формате:

{{
  "name": "...",
  "category": "...",
  "description": "...",
  "tech_stack_tags": ["..."],
  "use_case_tags": ["..."],
  "pricing_model": "pay-as-you-go"
}}
"""


PRICE_SELECTION_PROMPT = """
Ты модуль выбора релевантной цены облачной услуги.

Тебе дают одну облачную услугу и список тарифных кандидатов.
Твоя задача — выбрать ОДНУ тарифную строку, которая лучше всего относится именно к этой услуге.

ВАЖНО:
1. Верни только JSON, без markdown и без пояснений вне JSON.
2. Не выбирай самую дешевую строку, если она относится к другой услуге.
3. Не выбирай backup-цену для PostgreSQL, если услуга — Managed PostgreSQL.
4. Не выбирай DNS-цену для Kubernetes.
5. Не выбирай S3/object storage для Kubernetes, если это отдельная услуга.
6. Не выбирай OpenStack Backup для Veeam Backup.
7. Для Veeam Backup выбирай строки, где явно есть Veeam.
8. Для PostgreSQL выбирай строки, где явно есть PostgreSQL/postgres/СУБД PostgreSQL.
9. Для Kubernetes выбирай строки, где явно есть Kubernetes/k8s/master node/worker node.
10. Если подходящей строки нет — верни selected_candidate_id=null.
11. Если в selected candidate уже есть price_rub и price_unit, используй их.
12. Если price_rub нет, но в raw_text явно есть цена с руб/₽, можешь извлечь price_from_rub.
13. Не считай ценой номера приложений, годы, "720 часов", "30 дней", "1 раз в полгода".
14. Если сомневаешься — лучше верни null, чем чужую цену.

confidence:
- 0.9 если тарифная строка явно относится к услуге;
- 0.6 если похоже, но есть сомнения;
- 0.0 если подходящей цены нет.
"""


PRICE_SELECTION_USER_TEMPLATE = """
Выбери правильную цену для облачной услуги.

provider_id: {provider_id}
service_name: {service_name}
service_category: {service_category}

service_description:
{service_description}

pricing_candidates:
{pricing_candidates_json}

Верни JSON строго в таком формате:

{{
  "selected_candidate_id": null,
  "price_from_rub": null,
  "price_unit": null,
  "billing_period": null,
  "confidence": 0.0,
  "reason": "Коротко почему выбрана эта строка или почему цена не найдена"
}}
"""
