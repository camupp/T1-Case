from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from core.llm_client import ask_llm
from core.price_normalizer import (
    detect_billing_period,
    detect_item_type,
    normalize_price_record,
    parse_price,
    parse_structured_price,
)
from core.prompts import (
    PRICE_SELECTION_PROMPT,
    PRICE_SELECTION_USER_TEMPLATE,
    SERVICE_META_PROMPT,
    SERVICE_META_USER_TEMPLATE,
)
from core.schemas import (
    NormalizationError,
    ParseLogRecord,
    Provider,
    RawProviderFile,
    Service,
    ServicePricingItem,
)


logger = logging.getLogger(__name__)


# =============================================================================
# normalizer.py
# -----------------------------------------------------------------------------
# Упрощенная нормализация raw JSON провайдера.
#
# Главная идея:
#   1. LLM нормализует смысл услуги: name/category/description/tags.
#   2. Код грубо собирает тарифные строки-кандидаты.
#   3. Код парсит структурированные цены в этих строках.
#   4. LLM выбирает ОДНУ правильную цену для услуги из короткого списка.
#   5. Код записывает результат в services.json.
#
# ВАЖНО:
#   - Не пытаемся идеально матчить все тарифы сложной логикой.
#   - Не берем min() по всем найденным строкам.
#   - Не отдаём LLM весь pricing_items_raw.
#   - На цену одной услуги делается один LLM-запрос.
# =============================================================================


LLM_DELAY_SECONDS = 1.0
MAX_RAW_TEXT_FOR_LLM = 3500
MAX_PRICE_CANDIDATES_FOR_LLM = 25
MAX_RAW_TEXT_PER_PRICE_CANDIDATE = 450


# =============================================================================
# SMALL UTILS
# =============================================================================


def model_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(value_to_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key}: {value_to_text(val)}" for key, val in value.items())
    return str(value)


def first_value(data: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def clean_json_response(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError(f"Cannot extract JSON from LLM response: {text[:500]}")

    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM returned JSON, but not an object")

    return parsed


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        if not value.strip():
            return []
        return [part.strip() for part in re.split(r"[,;|]", value) if part.strip()]
    return [str(value).strip()]


def normalize_tag(tag: str) -> str:
    result = tag.strip().lower()
    result = result.replace("_", "-")
    result = re.sub(r"\s+", "-", result)
    result = re.sub(r"-+", "-", result)
    return result.strip("-")


def normalize_tag_list(tags: Any) -> list[str]:
    result: list[str] = []
    for tag in as_list(tags):
        normalized = normalize_tag(tag)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def slugify(text: str, max_len: int = 60) -> str:
    slug = (text or "").lower().replace("ё", "е")
    slug = re.sub(r"[^a-zа-я0-9]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return (slug[:max_len].strip("-") or "item")


def stable_hash(*parts: Any, length: int = 8) -> str:
    raw = "|".join(value_to_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def build_service_id(provider_id: str, name: str) -> str:
    return f"{provider_id}-{slugify(name, 55)}"


def build_pricing_item_id(service_id: str, item_name: str, index: int) -> str:
    return f"{service_id}-{slugify(item_name, 35)}-{index}"


# =============================================================================
# COMPLIANCE / REGIONS
# =============================================================================


COMPLIANCE_PATTERNS: dict[str, list[str]] = {
    "152-FZ": [r"152[\s\-]*фз", r"фз[\s\-]*152", r"152[\s\-]*fz", r"персональн\w+\s+данн"],
    "FSTEC": [r"фстэк", r"fstec"],
    "ISPDN": [r"испдн", r"ispdn"],
    "ISPDN_UZ_1": [r"уз[\s\-_]*1\b", r"\bк1\b"],
    "ISPDN_UZ_2": [r"уз[\s\-_]*2\b", r"\bк2\b"],
    "ISPDN_UZ_3": [r"уз[\s\-_]*3\b", r"\bк3\b"],
    "ISPDN_UZ_4": [r"уз[\s\-_]*4\b", r"\bк4\b"],
    "PCI DSS": [r"pci[\s\-]*dss"],
    "ISO 27001": [r"iso[\s\-]*27001"],
    "GIS": [r"\bгис\b", r"\bgis\b"],
}


REGION_PATTERNS: dict[str, list[str]] = {
    "Russia": [r"росси", r"\bрф\b", r"russia", r"территори\w*\s+рф"],
    "Moscow": [r"москв", r"moscow"],
    "Saint-Petersburg": [r"санкт[\s\-]*петербург", r"с\.?\s*петербург", r"\bспб\b", r"saint[\s\-]*petersburg"],
}


def extract_evidence_texts(items: list[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        data = model_to_dict(item)
        text = first_value(data, ["evidence_text", "raw_text", "text", "description"], "")
        if text:
            result.append(value_to_text(text))
    return result


def extract_compliance_tags(texts: list[Any]) -> list[str]:
    combined = " ".join(value_to_text(text) for text in texts if text).lower()
    tags: set[str] = set()

    for tag, patterns in COMPLIANCE_PATTERNS.items():
        if any(re.search(pattern, combined, flags=re.IGNORECASE) for pattern in patterns):
            tags.add(tag)

    return sorted(tags)


def extract_regions(texts: list[Any], default_russia: bool = True) -> list[str]:
    combined = " ".join(value_to_text(text) for text in texts if text).lower()
    regions: set[str] = set()

    for region, patterns in REGION_PATTERNS.items():
        if any(re.search(pattern, combined, flags=re.IGNORECASE) for pattern in patterns):
            regions.add(region)

    if default_russia and not regions:
        regions.add("Russia")

    return sorted(regions)


# =============================================================================
# CATEGORY
# =============================================================================


VALID_CATEGORIES = {
    "Compute",
    "Storage",
    "Database",
    "Network",
    "Security",
    "AI/ML",
    "DevOps",
    "Backup",
    "CDN",
    "Other",
}


CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Compute", ["виртуальн", "virtual machine", "cloud server", "compute", "vdc", "vcpu"]),
    ("Storage", ["storage", "хранилищ", "s3", "object", "объектн", "bucket", "бакет"]),
    ("Database", ["postgresql", "mysql", "clickhouse", "opensearch", "mongodb", "documentdb", "redis", "kafka", "rabbitmq", "субд", "database"]),
    ("DevOps", ["kubernetes", "k8s", "docker", "gitlab", "container", "registry", "devops"]),
    ("Backup", ["backup", "резервн", "veeam", "киберпротект", "бэкап"]),
    ("Network", ["dns", "load balancer", "балансировщ", "vpn", "vpc", "network", "сеть"]),
    ("AI/ML", ["ml", "machine learning", "gpu", "jupyter", "notebook", "нейросет"]),
    ("Security", ["security", "безопасн", "waf", "firewall", "защит"]),
    ("CDN", ["cdn", "content delivery", "доставка контента"]),
]


CATEGORY_ALIASES = {
    "Cloud Compute": "Compute",
    "Managed Database": "Database",
    "Object Storage": "Storage",
    "Kubernetes": "DevOps",
    "Analytics": "AI/ML",
}


def fix_category(category: Any, text: str) -> str:
    if category:
        cat = str(category).strip()
        cat = CATEGORY_ALIASES.get(cat, cat)
        if cat in VALID_CATEGORIES and cat != "Other":
            return cat

    lower = (text or "").lower()
    for cat, keywords in CATEGORY_KEYWORDS:
        if any(keyword in lower for keyword in keywords):
            return cat

    return "Other"


# =============================================================================
# PROVIDER
# =============================================================================


def build_provider_record(raw_file: RawProviderFile) -> Provider:
    provider_meta = raw_file.provider
    provider_data = model_to_dict(provider_meta)

    provider_id = provider_meta.provider_id
    name = provider_meta.name or provider_meta.raw_name or provider_id

    compliance_texts = extract_evidence_texts(raw_file.compliance_evidence_raw)
    compliance_tags = extract_compliance_tags([value_to_text(provider_data)] + compliance_texts)

    region_texts = extract_evidence_texts(raw_file.region_evidence_raw)
    regions = extract_regions([value_to_text(provider_data)] + region_texts, default_russia=True)

    return Provider(
        provider_id=provider_id,
        name=name,
        base_platform=provider_meta.base_platform,
        is_152fz_compliant=("152-FZ" in compliance_tags),
        regions=regions,
        api_docs_url=provider_meta.api_docs_url,
        pricing_url=provider_meta.pricing_url,
        source_url=provider_meta.source_url or provider_meta.base_url,
        parsed_at=raw_file.collected_at or datetime.now(timezone.utc),
    )


# =============================================================================
# RAW GETTERS
# =============================================================================


def get_candidate_name(candidate: Any) -> str:
    data = model_to_dict(candidate)
    return str(first_value(data, ["raw_name", "name", "title", "service_name"], "service"))


def get_candidate_text(candidate: Any) -> str:
    data = model_to_dict(candidate)
    parts = [
        first_value(data, ["raw_name", "name", "title"], ""),
        first_value(data, ["raw_description", "description", "raw_text", "text"], ""),
    ]
    return "\n".join(value_to_text(part) for part in parts if part)


def get_candidate_source_url(candidate: Any) -> str | None:
    data = model_to_dict(candidate)
    return first_value(data, ["source_url", "service_url", "document_url", "url"], None)


def get_candidate_service_url(candidate: Any) -> str | None:
    data = model_to_dict(candidate)
    return first_value(data, ["service_url", "source_url", "document_url", "url"], None)


def get_candidate_parsed_at(candidate: Any, fallback: datetime | None = None) -> datetime:
    data = model_to_dict(candidate)
    value = first_value(data, ["parsed_at"], None)

    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass

    return fallback or datetime.now(timezone.utc)


def get_raw_item_text(raw_item: Any) -> str:
    data = model_to_dict(raw_item)
    parts = [
        first_value(data, ["service_name_hint", "service_name"], ""),
        first_value(data, ["item_name_raw", "item_name", "name", "title"], ""),
        first_value(data, ["raw_text", "text", "description"], ""),
        first_value(data, ["price_raw", "price_value_raw", "price_per_month_raw", "price_unit_raw"], ""),
    ]
    return " ".join(value_to_text(part) for part in parts if part)


def get_raw_item_name(raw_item: Any) -> str:
    data = model_to_dict(raw_item)
    return (
        first_value(data, ["item_name_raw", "item_name", "name", "title"], None)
        or detect_item_type(get_raw_item_text(raw_item))
        or "pricing-item"
    )


def get_raw_item_source_url(raw_item: Any) -> str | None:
    data = model_to_dict(raw_item)
    return first_value(data, ["source_url", "pricing_url", "url"], None)


def get_raw_item_region(raw_item: Any) -> str | None:
    data = model_to_dict(raw_item)
    return first_value(data, ["region_hint", "region", "availability_zone"], None)


def get_raw_item_parsed_at(raw_item: Any, fallback: datetime | None = None) -> datetime:
    data = model_to_dict(raw_item)
    value = first_value(data, ["parsed_at"], None)

    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass

    return fallback or datetime.now(timezone.utc)


# =============================================================================
# LLM SERVICE META
# =============================================================================


def normalize_service_meta_with_llm(
    *,
    provider_id: str,
    raw_name: str,
    raw_text: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return {
            "name": raw_name,
            "category": fix_category(None, f"{raw_name} {raw_text}"),
            "description": raw_text[:300],
            "tech_stack_tags": [],
            "use_case_tags": [],
            "pricing_model": "pay-as-you-go",
        }

    prompt = SERVICE_META_USER_TEMPLATE.format(
        provider_id=provider_id,
        raw_name=raw_name,
        raw_text=raw_text[:MAX_RAW_TEXT_FOR_LLM] if raw_text else "",
    )

    response = ask_llm(
        system_prompt=SERVICE_META_PROMPT,
        user_prompt=prompt,
    )
    return clean_json_response(response)


# =============================================================================
# PRICE CANDIDATES
# =============================================================================


def get_price_candidate_keywords(service_name: str, category: str, description: str = "") -> list[str]:
    text = f"{service_name} {category} {description}".lower().replace("ё", "е")

    if "veeam" in text:
        return ["veeam"]

    if "кибер" in text or "cyberprotect" in text or "cyber protect" in text:
        return ["кибер", "кибер бэкап", "киберпротект", "cyberprotect"]

    if "postgres" in text or "postgresql" in text:
        return ["postgresql", "postgres", "субд postgresql"]

    if "kubernetes" in text or "k8s" in text:
        return ["kubernetes", "k8s", "master", "worker", "мастер-нода", "кластер"]

    if "s3" in text or "object storage" in text or "объектн" in text:
        return ["s3", "object storage", "объектное хранилище", "bucket", "бакет"]

    if "gitlab" in text:
        return ["gitlab"]

    if "clickhouse" in text:
        return ["clickhouse"]

    if "opensearch" in text or "elastic" in text:
        return ["opensearch", "elasticsearch", "elastic"]

    if "mysql" in text:
        return ["mysql", "mariadb"]

    if "rabbit" in text:
        return ["rabbitmq", "rabbit"]

    if "kafka" in text:
        return ["kafka"]

    if "redis" in text or "inmemory" in text:
        return ["redis", "inmemory", "memcached"]

    words = re.findall(r"[a-zа-я0-9]{4,}", text)
    stop = {"managed", "service", "cloud", "облако", "сервис", "услуга", "для", "with", "from"}
    result: list[str] = []

    for word in words:
        if word not in stop and word not in result:
            result.append(word)

    return result[:8]


def collect_price_candidates_for_llm(
    *,
    raw_file: RawProviderFile,
    service_name: str,
    category: str,
    description: str,
    limit: int = 80,
) -> list[Any]:
    """
    Грубый отбор тарифных строк, чтобы не отправлять в LLM весь pricing_items_raw.
    Это не финальный выбор. Финально выбирает LLM.
    """
    keywords = get_price_candidate_keywords(service_name, category, description)
    selected: list[Any] = []
    seen: set[str] = set()

    def add_item(item: Any) -> None:
        key = stable_hash(value_to_text(model_to_dict(item)), length=16)
        if key not in seen:
            seen.add(key)
            selected.append(item)

    for item in raw_file.pricing_items_raw:
        text = get_raw_item_text(item).lower().replace("ё", "е")
        if any(keyword.lower().replace("ё", "е") in text for keyword in keywords):
            add_item(item)

    return selected[:limit]


def normalize_single_pricing_item(
    *,
    raw_item: Any,
    service_id: str,
    provider_id: str,
    index: int,
    fallback_parsed_at: datetime | None = None,
) -> ServicePricingItem:
    data = model_to_dict(raw_item)
    raw_text = get_raw_item_text(raw_item)
    item_name = get_raw_item_name(raw_item)

    structured_price = parse_structured_price(data)
    price_source = "structured" if structured_price is not None else None

    original_price = structured_price
    if original_price is None:
        original_price = parse_price(raw_text)
        if original_price is not None:
            price_source = "regex"

    billing_period = detect_billing_period(f"{item_name} {raw_text}")
    item_type = detect_item_type(raw_text, item_name=item_name)

    normalized = normalize_price_record(
        price_rub=original_price,
        text=raw_text,
        billing_period=billing_period,
        item_type=item_type,
        item_name=item_name,
        to_month=True,
    )

    price_rub = normalized["price_rub"]
    price_unit = normalized["price_unit"] if price_rub is not None else None
    normalized_billing_period = normalized["billing_period"] if price_rub is not None else billing_period

    return ServicePricingItem(
        pricing_item_id=build_pricing_item_id(service_id, item_name, index),
        service_id=service_id,
        provider_id=provider_id,
        item_name=item_name,
        item_type=normalized["item_type"],
        price_rub=price_rub,
        price_unit=price_unit,
        billing_period=normalized_billing_period,
        region=get_raw_item_region(raw_item),
        configuration_tags=[],
        source_url=get_raw_item_source_url(raw_item),
        raw_text=raw_text[:700] if raw_text else None,
        parsed_at=get_raw_item_parsed_at(raw_item, fallback_parsed_at),
        is_synthetic=False,
        price_source=price_source or "none",
        price_confidence=1.0 if price_source == "structured" else (0.45 if price_source == "regex" else None),
        price_evidence=raw_text[:400] if price_source else None,
    )


def deduplicate_pricing_items(items: list[ServicePricingItem]) -> list[ServicePricingItem]:
    result: list[ServicePricingItem] = []
    seen: set[tuple[Any, ...]] = set()

    for item in items:
        key = (
            item.service_id,
            item.item_name.lower().strip(),
            item.item_type,
            item.price_rub,
            item.price_unit,
            item.source_url,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)

    return result


def build_price_selection_candidates_json(
    pricing_items: list[ServicePricingItem],
) -> str:
    """
    Готовит короткий JSON для LLM.
    Сначала показываем строки, где уже есть цена.
    """
    priced = [item for item in pricing_items if item.price_rub is not None and item.price_rub > 0]
    unpriced = [item for item in pricing_items if item.price_rub is None]

    source_priority = {
        "structured": 0,
        "regex": 1,
        "none": 2,
    }

    priced.sort(
        key=lambda item: (
            source_priority.get(item.price_source or "none", 9),
            float(item.price_rub or 0),
        )
    )

    candidates = (priced + unpriced)[:MAX_PRICE_CANDIDATES_FOR_LLM]

    data: list[dict[str, Any]] = []
    for idx, item in enumerate(candidates):
        data.append(
            {
                "candidate_id": idx,
                "item_name": item.item_name,
                "item_type": item.item_type,
                "price_rub": item.price_rub,
                "price_unit": item.price_unit,
                "billing_period": item.billing_period,
                "price_source": item.price_source,
                "source_url": item.source_url,
                "raw_text": (item.raw_text or "")[:MAX_RAW_TEXT_PER_PRICE_CANDIDATE],
            }
        )

    return json.dumps(data, ensure_ascii=False, indent=2)


def get_price_candidate_by_llm_id(
    pricing_items: list[ServicePricingItem],
    selected_id: int,
) -> ServicePricingItem | None:
    priced = [item for item in pricing_items if item.price_rub is not None and item.price_rub > 0]
    unpriced = [item for item in pricing_items if item.price_rub is None]

    source_priority = {
        "structured": 0,
        "regex": 1,
        "none": 2,
    }

    priced.sort(
        key=lambda item: (
            source_priority.get(item.price_source or "none", 9),
            float(item.price_rub or 0),
        )
    )

    candidates = (priced + unpriced)[:MAX_PRICE_CANDIDATES_FOR_LLM]

    if selected_id < 0 or selected_id >= len(candidates):
        return None

    return candidates[selected_id]


def select_service_price_with_llm(
    *,
    provider_id: str,
    service_name: str,
    service_category: str,
    service_description: str,
    pricing_items: list[ServicePricingItem],
    dry_run: bool = False,
) -> tuple[float | None, str | None, str | None, float | None, str | None]:
    """
    Один LLM-запрос на цену одной услуги.
    LLM выбирает правильную строку из короткого списка.
    """
    if dry_run or not pricing_items:
        return None, None, "none", None, None

    candidates_json = build_price_selection_candidates_json(pricing_items)

    if not candidates_json.strip() or candidates_json.strip() == "[]":
        return None, None, "none", None, None

    prompt = PRICE_SELECTION_USER_TEMPLATE.format(
        provider_id=provider_id,
        service_name=service_name,
        service_category=service_category,
        service_description=(service_description or "")[:1200],
        pricing_candidates_json=candidates_json[:10000],
    )

    response = ask_llm(
        system_prompt=PRICE_SELECTION_PROMPT,
        user_prompt=prompt,
    )
    result = clean_json_response(response)

    selected_id = result.get("selected_candidate_id")
    confidence = result.get("confidence")
    reason = value_to_text(result.get("reason"))

    try:
        confidence_float = float(confidence or 0)
    except (TypeError, ValueError):
        confidence_float = 0.0

    if selected_id is None or confidence_float < 0.55:
        return None, None, "none", confidence_float, reason or None

    try:
        selected_index = int(selected_id)
    except (TypeError, ValueError):
        return None, None, "none", confidence_float, reason or None

    selected_item = get_price_candidate_by_llm_id(pricing_items, selected_index)
    if selected_item is None:
        return None, None, "none", confidence_float, reason or None

    # Предпочитаем цену из выбранного pricing item, потому что ее уже нормализовал код.
    price_from_rub = selected_item.price_rub
    price_unit = selected_item.price_unit

    # Если в item цены не было, но LLM явно вернула цену, можно взять ее.
    if price_from_rub is None:
        raw_llm_price = result.get("price_from_rub")
        try:
            price_from_rub = float(raw_llm_price) if raw_llm_price is not None else None
        except (TypeError, ValueError):
            price_from_rub = None

        price_unit = result.get("price_unit") or selected_item.price_unit

    if price_from_rub is None or price_from_rub <= 0 or not price_unit:
        return None, None, "none", confidence_float, selected_item.raw_text or reason or None

    evidence = selected_item.raw_text or selected_item.price_evidence or reason

    return (
        round(float(price_from_rub), 6),
        price_unit,
        "llm_selected",
        confidence_float,
        evidence[:700] if evidence else None,
    )


# =============================================================================
# SERVICE NORMALIZATION
# =============================================================================


def normalize_one_service(
    *,
    candidate: Any,
    provider: Provider,
    raw_file: RawProviderFile,
    dry_run: bool = False,
    use_llm_price_selection: bool = True,
) -> tuple[Service, list[ServicePricingItem]]:
    raw_name = get_candidate_name(candidate)
    raw_text = get_candidate_text(candidate)
    parsed_at = get_candidate_parsed_at(candidate, raw_file.collected_at)

    # 1. LLM нормализует смысл услуги.
    try:
        llm_meta = normalize_service_meta_with_llm(
            provider_id=provider.provider_id,
            raw_name=raw_name,
            raw_text=raw_text,
            dry_run=dry_run,
        )
        if not dry_run:
            time.sleep(LLM_DELAY_SECONDS)
    except Exception as error:
        logger.warning("LLM meta normalization failed for %s: %s", raw_name, error)
        llm_meta = {
            "name": raw_name,
            "category": fix_category(None, f"{raw_name} {raw_text}"),
            "description": raw_text[:300],
            "tech_stack_tags": [],
            "use_case_tags": [],
            "pricing_model": "pay-as-you-go",
        }

    name = str(llm_meta.get("name") or raw_name).strip()
    category = fix_category(llm_meta.get("category"), f"{name} {raw_text}")
    description = str(llm_meta.get("description") or raw_text[:300] or name).strip()
    service_id = build_service_id(provider.provider_id, name)

    # 2. Compliance / regions — просто из provider evidence + candidate evidence.
    provider_compliance_texts = extract_evidence_texts(raw_file.compliance_evidence_raw)
    provider_region_texts = extract_evidence_texts(raw_file.region_evidence_raw)

    candidate_data = model_to_dict(candidate)
    candidate_evidence = raw_text if (
        candidate_data.get("has_compliance_keywords") or candidate_data.get("has_region_keywords")
    ) else ""

    compliance_tags = extract_compliance_tags(provider_compliance_texts + [candidate_evidence])
    if provider.is_152fz_compliant and "152-FZ" not in compliance_tags:
        compliance_tags.append("152-FZ")
    compliance_tags = sorted(set(compliance_tags))

    regions = extract_regions(provider_region_texts + [candidate_evidence], default_russia=True)
    if not regions:
        regions = provider.regions or ["Russia"]

    # 3. Грубо собираем тарифные кандидаты.
    raw_price_candidates = collect_price_candidates_for_llm(
        raw_file=raw_file,
        service_name=name,
        category=category,
        description=description,
        limit=80,
    )

    # 4. Нормализуем кандидаты кодом.
    pricing_items: list[ServicePricingItem] = []
    for idx, raw_item in enumerate(raw_price_candidates):
        try:
            pricing_items.append(
                normalize_single_pricing_item(
                    raw_item=raw_item,
                    service_id=service_id,
                    provider_id=provider.provider_id,
                    index=idx,
                    fallback_parsed_at=parsed_at,
                )
            )
        except Exception as error:
            logger.warning("Pricing item normalization failed for %s: %s", raw_name, error)

    pricing_items = deduplicate_pricing_items(pricing_items)

    # 5. LLM выбирает правильную цену из короткого списка.
    if use_llm_price_selection and not dry_run:
        try:
            price_from_rub, price_unit, price_source, price_confidence, price_evidence = select_service_price_with_llm(
                provider_id=provider.provider_id,
                service_name=name,
                service_category=category,
                service_description=description,
                pricing_items=pricing_items,
                dry_run=dry_run,
            )
            time.sleep(LLM_DELAY_SECONDS)
        except Exception as error:
            logger.warning("LLM price selection failed for %s: %s", raw_name, error)
            price_from_rub, price_unit, price_source, price_confidence, price_evidence = (
                None,
                None,
                "none",
                None,
                None,
            )
    else:
        price_from_rub, price_unit, price_source, price_confidence, price_evidence = (
            None,
            None,
            "none",
            None,
            None,
        )

    service = Service(
        service_id=service_id,
        provider_id=provider.provider_id,
        name=name,
        category=category,
        description=description,
        tech_stack_tags=normalize_tag_list(llm_meta.get("tech_stack_tags")),
        use_case_tags=normalize_tag_list(llm_meta.get("use_case_tags")),
        compliance_tags=compliance_tags,
        regions=regions,
        pricing_model=llm_meta.get("pricing_model") or "pay-as-you-go",
        price_from_rub=price_from_rub,
        price_unit=price_unit,
        support_level=None,
        service_url=get_candidate_service_url(candidate),
        source_url=get_candidate_source_url(candidate),
        parsed_at=parsed_at,
        is_synthetic=False,
        price_source=price_source or "none",
        price_confidence=price_confidence,
        price_evidence=price_evidence,
    )

    return service, pricing_items


# =============================================================================
# PARSE LOG
# =============================================================================


def normalize_parse_log(raw_file: RawProviderFile, provider_id: str) -> list[ParseLogRecord]:
    result: list[ParseLogRecord] = []

    for raw_log in raw_file.parse_log:
        data = model_to_dict(raw_log)
        url = first_value(data, ["url", "source_url"], None)
        if not url:
            continue

        parsed_at = first_value(data, ["parsed_at"], None)
        if isinstance(parsed_at, str):
            try:
                parsed_at = datetime.fromisoformat(parsed_at.replace("Z", "+00:00"))
            except ValueError:
                parsed_at = datetime.now(timezone.utc)
        elif not isinstance(parsed_at, datetime):
            parsed_at = datetime.now(timezone.utc)

        result.append(
            ParseLogRecord(
                provider_id=first_value(data, ["provider_id"], provider_id) or provider_id,
                url=url,
                parsed_at=parsed_at,
                status=first_value(data, ["status"], "success") or "success",
                records_added=int(first_value(data, ["records_added"], 0) or 0),
                error=first_value(data, ["error"], None),
                normalization_step="completed",
            )
        )

    if not result:
        provider_source = raw_file.provider.source_url or raw_file.provider.base_url
        if provider_source:
            result.append(
                ParseLogRecord(
                    provider_id=provider_id,
                    url=provider_source,
                    parsed_at=raw_file.collected_at or datetime.now(timezone.utc),
                    status="success",
                    records_added=len(raw_file.service_candidates),
                    error=None,
                    normalization_step="completed",
                )
            )

    return result


# =============================================================================
# MAIN ENTRYPOINTS
# =============================================================================


def normalize_provider_file(
    raw_data: dict[str, Any] | RawProviderFile,
    *,
    dry_run: bool = False,
    use_llm_price_selection: bool = True,
) -> tuple[Provider, list[Service], list[ServicePricingItem], list[ParseLogRecord], list[NormalizationError]]:
    errors: list[NormalizationError] = []

    try:
        raw_file = raw_data if isinstance(raw_data, RawProviderFile) else RawProviderFile(**raw_data)
    except Exception as error:
        raise ValueError(f"Invalid raw provider file structure: {error}") from error

    provider_id = raw_file.provider.provider_id
    provider = build_provider_record(raw_file)

    services: list[Service] = []
    all_pricing_items: list[ServicePricingItem] = []

    for candidate in raw_file.service_candidates:
        try:
            service, pricing_items = normalize_one_service(
                candidate=candidate,
                provider=provider,
                raw_file=raw_file,
                dry_run=dry_run,
                use_llm_price_selection=use_llm_price_selection,
            )
            services.append(service)
            all_pricing_items.extend(pricing_items)
        except Exception as error:
            candidate_data = model_to_dict(candidate)
            errors.append(
                NormalizationError(
                    item_type="service",
                    provider_id=provider_id,
                    source_url=first_value(candidate_data, ["source_url", "service_url", "document_url"], None),
                    error=str(error),
                    raw_item=candidate_data,
                )
            )
            logger.exception("Service normalization failed: %s", error)

    parse_log = normalize_parse_log(raw_file, provider_id)

    return provider, services, all_pricing_items, parse_log, errors


def normalize_many_provider_files(
    raw_files: list[dict[str, Any] | RawProviderFile],
    *,
    dry_run: bool = False,
    use_llm_price_selection: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    providers: list[Provider] = []
    services: list[Service] = []
    pricing_items: list[ServicePricingItem] = []
    parse_log: list[ParseLogRecord] = []
    errors: list[NormalizationError] = []

    for raw_file in raw_files:
        try:
            provider, provider_services, provider_pricing, provider_log, provider_errors = normalize_provider_file(
                raw_file,
                dry_run=dry_run,
                use_llm_price_selection=use_llm_price_selection,
            )
            providers.append(provider)
            services.extend(provider_services)
            pricing_items.extend(provider_pricing)
            parse_log.extend(provider_log)
            errors.extend(provider_errors)
        except Exception as error:
            errors.append(
                NormalizationError(
                    item_type="provider",
                    provider_id=None,
                    source_url=None,
                    error=str(error),
                    raw_item=model_to_dict(raw_file),
                )
            )
            logger.exception("Provider file normalization failed: %s", error)

    return {
        "providers": [item.model_dump(mode="json") for item in providers],
        "services": [item.model_dump(mode="json") for item in services],
        "service_pricing_items": [item.model_dump(mode="json") for item in pricing_items],
        "parse_log": [item.model_dump(mode="json") for item in parse_log],
        "errors": [item.model_dump(mode="json") for item in errors],
    }
