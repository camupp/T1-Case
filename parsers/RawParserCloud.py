"""
RawParserCloud.py

Парсер публичных источников Cloud.ru.

Задача:
- собрать сырые данные по Cloud.ru;
- собрать страницы услуг, документации, тарифов и безопасности;
- сохранить HTML-текст, ссылки, документы и извлечённый текст;
- НЕ нормализовать данные;
- подготовить JSON для последующей LLM-нормализации.

Новая структура:
- service_candidates;
- pricing_sources;
- pricing_items_raw;
- docs_sources;
- api_sources;
- security_sources;
- compliance_evidence_raw;
- region_evidence_raw.

Основные источники:
- https://cloud.ru/services
- https://cloud.ru/docs
- https://cloud.ru/documents/tariffs/index
- https://cloud.ru/security

Запуск из корня проекта:
    python parsers/RawParserCloud.py

Результат:
    data/raw/cloud_ru_raw.json
    data/raw/cloud_ru_parse_log.json
    data/raw/downloads/cloud-ru/...
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from base import (
    RawDataset,
    RawSource,
    collect_html_page,
    dataset_to_dict,
    deduplicate_links,
    normalize_spaces,
    now_iso,
    print_dataset_summary,
    save_json,
)


PROVIDER_ID = "cloud-ru"
PROVIDER_NAME = "Cloud.ru"

BASE_URL = "https://cloud.ru"

ALLOWED_DOMAINS = {
    "cloud.ru",
    "www.cloud.ru",
}

OUTPUT_DIR = Path("data/raw")
DOWNLOADS_OUTPUT_DIR = OUTPUT_DIR / "downloads"

RAW_OUTPUT_PATH = OUTPUT_DIR / "cloud_ru_raw.json"
LOG_OUTPUT_PATH = OUTPUT_DIR / "cloud_ru_parse_log.json"


START_PAGES = {
    "services": "https://cloud.ru/services",
    "docs": "https://cloud.ru/docs",
    "tariffs": "https://cloud.ru/documents/tariffs/index",
    "security": "https://cloud.ru/security",
}


SERVICE_URL_MARKERS = [
    "/products/",
    "/services/",
]


IMPORTANT_DOCS_MARKERS = [
    "virtual-machines",
    "compute",
    "s3",
    "s3e",
    "object-storage",
    "storage",
    "postgresql",
    "mysql",
    "redis",
    "clickhouse",
    "opensearch",
    "kafka",
    "managed-kubernetes",
    "kubernetes",
    "container",
    "containers",
    "artifact",
    "registry",
    "dns",
    "vpc",
    "load-balancer",
    "cdn",
    "backup",
    "monitoring",
    "logging",
    "audit",
    "security",
    "iam",
    "key-management",
    "secret-management",
    "waf",
    "ddos",
    "console_api",
    "api",
    "terraform",
    "cli",
    "sdk",
    "certificate",
    "certificates",
]


PRICING_KEYWORDS = [
    "цена",
    "цены",
    "стоимость",
    "тариф",
    "тарифы",
    "тарификация",
    "оплата",
    "руб",
    "₽",
    "месяц",
    "мес",
    "час",
    "pay as you go",
    "калькулятор",
    "free tier",
    "бесплатно",
    "vcpu",
    "cpu",
    "ram",
    "гб",
    "gb",
    "диск",
    "хранилище",
    "трафик",
    "запрос",
]


STRICT_COMPLIANCE_KEYWORDS = [
    "152-фз",
    "152 фз",
    "персональные данные",
    "персональных данных",
    "испдн",
    "уз-1",
    "уз 1",
    "уз-2",
    "уз 2",
    "уз-3",
    "уз 3",
    "уз-4",
    "уз 4",
    "фстэк",
    "фсб",
    "приказ фстэк",
    "аттестат",
    "аттестован",
    "аттестованное облако",
    "сертификат соответствия",
    "pci dss",
    "iso 27001",
    "iso 27017",
    "iso 27018",
]


SECURITY_KEYWORDS = [
    "безопасность",
    "защита",
    "сертификация",
    "сертификат",
    "аттестат",
    "152-фз",
    "152 фз",
    "персональные данные",
    "персональных данных",
    "испдн",
    "уз-1",
    "уз 1",
    "фстэк",
    "фсб",
    "pci dss",
    "iso 27001",
    "iso 27017",
    "iso 27018",
    "гост",
    "шифрование",
    "ключи",
    "key management",
    "secret management",
    "waf",
    "anti-ddos",
    "ddos",
    "compliance",
]


# Для строгого региона не используем просто "цод" или "дата-центр".
# Они сами по себе не доказывают конкретный регион.
STRICT_REGION_KEYWORDS = [
    "москва",
    "московская область",
    "санкт-петербург",
    "петербург",
    "ленинградская область",
    "новосибирск",
    "россия",
    "российская федерация",
    "территория рф",
    "территории рф",
    "территория россии",
    "территории россии",
    "зона доступности",
    "зоны доступности",
    "availability zone",
]


REGION_KEYWORDS = [
    "москва",
    "московская область",
    "санкт-петербург",
    "петербург",
    "ленинградская область",
    "новосибирск",
    "россия",
    "российская федерация",
    "территория рф",
    "территории рф",
    "территория россии",
    "территории россии",
    "зона доступности",
    "зоны доступности",
    "availability zone",
    "регион",
    "пул",
    "pool",
]


API_KEYWORDS = [
    "api",
    "swagger",
    "openapi",
    "terraform",
    "cli",
    "sdk",
    "rest api",
    "справочник api",
]


def is_good_cloud_ru_link(url: str) -> bool:
    """
    Отсекает явно мусорные ссылки.
    """
    if not url:
        return False

    bad_prefixes = [
        "mailto:",
        "tel:",
        "javascript:",
    ]

    if any(url.startswith(prefix) for prefix in bad_prefixes):
        return False

    bad_parts = [
        "/blog/",
        "/events/",
        "/career/",
        "/careers/",
        "/press/",
        "/news/",
        "/contacts/",
        "#",
    ]

    if any(part in url for part in bad_parts):
        return False

    return True


def canonicalize_url(url: str) -> str:
    """
    Убирает query-параметры и слэш в конце.
    Это помогает убрать дубли.
    """
    return url.split("?")[0].rstrip("/")


def deduplicate_links_by_canonical_url(
    links: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Убирает дубли ссылок по canonical URL.
    """
    seen = set()
    result = []

    for link in links:
        url = link.get("url", "")
        canonical = canonicalize_url(url)

        if not canonical or canonical in seen:
            continue

        seen.add(canonical)
        result.append(link)

    return result


def contains_any_keyword(text: str, keywords: list[str]) -> bool:
    """
    Проверяет, содержит ли текст хотя бы одно ключевое слово.
    """
    lower = normalize_spaces(text).lower()
    return any(keyword.lower() in lower for keyword in keywords)


def find_matched_keywords(text: str, keywords: list[str]) -> list[str]:
    """
    Возвращает список найденных ключевых слов.
    """
    lower = normalize_spaces(text).lower()
    matched = []

    for keyword in keywords:
        if keyword.lower() in lower:
            matched.append(keyword)

    return matched


def looks_like_service_url(url: str) -> bool:
    """
    Проверяет, похожа ли ссылка на карточку продукта/услуги Cloud.ru.
    """
    if not is_good_cloud_ru_link(url):
        return False

    if canonicalize_url(url) == "https://cloud.ru/services":
        return False

    if "/products/" in url:
        return True

    # /services оставляем осторожно:
    # стартовую /services не считаем отдельной услугой,
    # но вложенные /services/... можем сохранить.
    if "/services/" in url:
        return True

    return False


def looks_like_docs_url(url: str) -> bool:
    """
    Проверяет, похожа ли ссылка на полезную страницу документации.
    """
    if not is_good_cloud_ru_link(url):
        return False

    if "/docs/" not in url:
        return False

    lower = url.lower()

    return any(marker in lower for marker in IMPORTANT_DOCS_MARKERS)


def extract_title_from_page(page: RawSource) -> str:
    """
    Получает нормальное имя страницы/услуги.
    """
    if page.headings:
        return page.headings[0]

    if page.title:
        return page.title

    return page.url


def detect_platform_hint(text: str, url: str) -> str | None:
    """
    Пытается определить платформу Cloud.ru по URL и тексту.
    Это не финальная нормализация, а только raw-подсказка.
    """
    source = f"{url} {text}".lower()

    if "evolution stack" in source:
        return "Evolution Stack"

    if "evolution" in source:
        return "Evolution"

    if "advanced" in source:
        return "Advanced"

    if "vmware" in source:
        return "VMware"

    return None


def extract_evidence_fragments(
    text: str,
    keywords: list[str],
    window: int = 700,
    max_fragments: int = 5,
) -> list[dict[str, Any]]:
    """
    Вырезает короткие фрагменты текста вокруг ключевых слов.

    Это нужно, чтобы не отдавать LLM весь большой текст,
    а передавать только доказательные фрагменты.
    """
    normalized = normalize_spaces(text)

    if not normalized:
        return []

    lower = normalized.lower()
    fragments = []
    used_ranges: list[tuple[int, int]] = []

    for keyword in keywords:
        keyword_lower = keyword.lower()
        start = 0

        while True:
            index = lower.find(keyword_lower, start)

            if index == -1:
                break

            fragment_start = max(0, index - window)
            fragment_end = min(len(normalized), index + len(keyword) + window)

            overlaps = False
            for old_start, old_end in used_ranges:
                if fragment_start <= old_end and fragment_end >= old_start:
                    overlaps = True
                    break

            if not overlaps:
                evidence_text = normalized[fragment_start:fragment_end]

                fragments.append(
                    {
                        "matched_keyword": keyword,
                        "evidence_text": evidence_text,
                        "start_index": fragment_start,
                        "end_index": fragment_end,
                    }
                )

                used_ranges.append((fragment_start, fragment_end))

            if len(fragments) >= max_fragments:
                return fragments

            start = index + len(keyword_lower)

    return fragments


def guess_compliance_tags(evidence_text: str) -> list[str]:
    """
    Грубые compliance-подсказки по фрагменту.
    Это не финальная нормализация.
    """
    lower = evidence_text.lower()
    tags = []

    if "152" in lower:
        tags.append("152-FZ")

    if "фстэк" in lower:
        tags.append("FSTEC")

    if "фсб" in lower:
        tags.append("FSB")

    if "испдн" in lower:
        tags.append("ISPDN")

    if "уз-1" in lower or "уз 1" in lower:
        tags.append("ISPDN_UZ_1")

    if "уз-2" in lower or "уз 2" in lower:
        tags.append("ISPDN_UZ_2")

    if "уз-3" in lower or "уз 3" in lower:
        tags.append("ISPDN_UZ_3")

    if "уз-4" in lower or "уз 4" in lower:
        tags.append("ISPDN_UZ_4")

    if "pci dss" in lower:
        tags.append("PCI_DSS")

    if "iso 27001" in lower:
        tags.append("ISO_27001")

    return sorted(set(tags))


def guess_region_hint(evidence_text: str) -> str | None:
    """
    Грубая подсказка по региону.
    Это не финальная нормализация.
    """
    lower = evidence_text.lower()

    if "моск" in lower:
        return "Moscow"

    if "санкт-петербург" in lower or "петербург" in lower:
        return "Saint Petersburg"

    if "новосибирск" in lower:
        return "Novosibirsk"

    if "росси" in lower or "рф" in lower:
        return "Russia"

    return None


def guess_evidence_scope(source_kind: str) -> str:
    """
    Определяет уровень доказательства.

    Для строгой фильтрации подходят:
    - service_level;
    - document_level;
    - pricing_level.

    provider_level и platform_level — только справочно.
    """
    if source_kind in {
        "service_detail_page",
        "security_or_region_mention_in_service_page",
    }:
        return "service_level"

    if source_kind in {
        "docs_detail_page",
        "security_or_region_mention_in_docs",
        "api_or_automation_docs",
    }:
        return "document_level"

    if source_kind in {
        "tariffs_page",
        "tariffs_document",
        "pricing_page",
        "pricing_document",
        "pricing_mention_in_service_page",
        "pricing_mention_in_docs",
    }:
        return "pricing_level"

    if source_kind in {
        "security_page",
        "security_document",
    }:
        return "provider_level"

    return "provider_level"


def is_strict_scope(scope: str) -> bool:
    """
    Проверяет, можно ли использовать evidence для строгого фильтра.
    """
    return scope in {
        "service_level",
        "document_level",
        "pricing_level",
    }


def build_service_candidates(
    services_source: RawSource | None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """
    Собирает service_candidates из https://cloud.ru/services.

    Логика:
    - берём ссылки /products/... и вложенные /services/...
    - заходим на карточку продукта;
    - сохраняем сырой текст, заголовки, ссылки и документы.
    """
    if services_source is None:
        return []

    service_links = []

    for link in services_source.links:
        url = link.get("url", "")

        if looks_like_service_url(url):
            service_links.append(link)

    service_links = deduplicate_links_by_canonical_url(service_links)
    service_links = service_links[:limit]

    candidates = []

    for link in service_links:
        page, _ = collect_html_page(
            provider_id=PROVIDER_ID,
            source_type="service_detail_page",
            url=link["url"],
            allowed_domains=ALLOWED_DOMAINS,
            downloads_output_dir=DOWNLOADS_OUTPUT_DIR,
            download_documents=True,
        )

        if page is None:
            continue

        text_for_platform = " ".join(
            [
                page.title or "",
                " ".join(page.headings),
                page.text[:3000],
            ]
        )

        candidate = {
            "provider_id": PROVIDER_ID,
            "raw_name": extract_title_from_page(page),
            "raw_anchor": link.get("anchor", ""),
            "source_url": page.url,
            "final_url": page.final_url,
            "parent_source_url": services_source.url,
            "raw_title": page.title,
            "raw_headings": page.headings,
            "raw_text": page.text,
            "raw_links": page.links,
            "document_links": page.document_links,
            "downloaded_documents": page.downloaded_documents,
            "raw_platform_hint": detect_platform_hint(text_for_platform, page.url),
            "has_pricing_keywords": contains_any_keyword(page.text, PRICING_KEYWORDS),
            "has_compliance_keywords": contains_any_keyword(page.text, STRICT_COMPLIANCE_KEYWORDS),
            "has_security_keywords": contains_any_keyword(page.text, SECURITY_KEYWORDS),
            "has_region_keywords": contains_any_keyword(page.text, REGION_KEYWORDS),
            "parsed_at": page.parsed_at,
            "normalization_status": "not_normalized",
        }

        candidates.append(candidate)

    return candidates


def build_docs_sources(
    docs_source: RawSource | None,
    service_candidates: list[dict[str, Any]],
    limit: int = 60,
) -> list[dict[str, Any]]:
    """
    Собирает полезные страницы документации Cloud.ru.

    Берём:
    - ссылки из главной страницы /docs;
    - ссылки на docs из карточек продуктов.
    """
    if docs_source is None:
        return []

    docs_sources = [
        {
            "provider_id": PROVIDER_ID,
            "source_url": docs_source.url,
            "source_kind": "docs_index_page",
            "title": docs_source.title,
            "headings": docs_source.headings,
            "text": docs_source.text,
            "links": docs_source.links,
            "parsed_at": docs_source.parsed_at,
        }
    ]

    docs_links = []

    for link in docs_source.links:
        url = link.get("url", "")

        if looks_like_docs_url(url):
            docs_links.append(link)

    for candidate in service_candidates:
        for link in candidate.get("raw_links", []):
            url = link.get("url", "")

            if looks_like_docs_url(url):
                docs_links.append(link)

    docs_links = deduplicate_links_by_canonical_url(docs_links)
    docs_links = docs_links[:limit]

    for link in docs_links:
        page, _ = collect_html_page(
            provider_id=PROVIDER_ID,
            source_type="docs_detail_page",
            url=link["url"],
            allowed_domains=ALLOWED_DOMAINS,
            downloads_output_dir=DOWNLOADS_OUTPUT_DIR,
            download_documents=True,
        )

        if page is None:
            continue

        text_for_platform = " ".join(
            [
                page.title or "",
                " ".join(page.headings),
                page.text[:3000],
            ]
        )

        docs_sources.append(
            {
                "provider_id": PROVIDER_ID,
                "source_url": page.url,
                "final_url": page.final_url,
                "source_kind": "docs_detail_page",
                "anchor": link.get("anchor", ""),
                "title": page.title,
                "headings": page.headings,
                "text": page.text,
                "links": page.links,
                "document_links": page.document_links,
                "downloaded_documents": page.downloaded_documents,
                "raw_platform_hint": detect_platform_hint(text_for_platform, page.url),
                "has_pricing_keywords": contains_any_keyword(page.text, PRICING_KEYWORDS),
                "has_compliance_keywords": contains_any_keyword(page.text, STRICT_COMPLIANCE_KEYWORDS),
                "has_security_keywords": contains_any_keyword(page.text, SECURITY_KEYWORDS),
                "has_region_keywords": contains_any_keyword(page.text, REGION_KEYWORDS),
                "parsed_at": page.parsed_at,
            }
        )

    return docs_sources


def build_pricing_sources(
    tariffs_source: RawSource | None,
    service_candidates: list[dict[str, Any]],
    docs_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Собирает сырьё по тарифам.

    Источники:
    - /documents/tariffs/index;
    - карточки продуктов с ценовыми словами;
    - документация с ценовыми словами.
    """
    pricing_sources = []

    if tariffs_source is not None:
        pricing_sources.append(
            {
                "provider_id": PROVIDER_ID,
                "source_url": tariffs_source.url,
                "source_kind": "tariffs_page",
                "title": tariffs_source.title,
                "headings": tariffs_source.headings,
                "text": tariffs_source.text,
                "links": tariffs_source.links,
                "document_links": tariffs_source.document_links,
                "downloaded_documents": tariffs_source.downloaded_documents,
                "parsed_at": tariffs_source.parsed_at,
                "note": (
                    "Некоторые цены могут подгружаться динамически через JavaScript. "
                    "Raw parser сохраняет HTML-текст, ссылки и документы, доступные публично."
                ),
            }
        )

        for doc in tariffs_source.downloaded_documents:
            pricing_sources.append(
                {
                    "provider_id": PROVIDER_ID,
                    "source_url": doc.get("url"),
                    "source_kind": "tariffs_document",
                    "anchor": doc.get("anchor", ""),
                    "local_path": doc.get("local_path"),
                    "content_type": doc.get("content_type"),
                    "text": doc.get("extracted_text", ""),
                    "status": doc.get("status"),
                    "error": doc.get("error"),
                    "parsed_at": now_iso(),
                }
            )

    for candidate in service_candidates:
        raw_text = candidate.get("raw_text", "")

        if contains_any_keyword(raw_text, PRICING_KEYWORDS):
            pricing_sources.append(
                {
                    "provider_id": PROVIDER_ID,
                    "source_url": candidate.get("source_url"),
                    "source_kind": "pricing_mention_in_service_page",
                    "raw_name": candidate.get("raw_name"),
                    "raw_platform_hint": candidate.get("raw_platform_hint"),
                    "text": raw_text,
                    "matched_keywords_group": "pricing",
                    "parsed_at": now_iso(),
                }
            )

    for source in docs_sources:
        text = source.get("text", "")

        if contains_any_keyword(text, PRICING_KEYWORDS):
            pricing_sources.append(
                {
                    "provider_id": PROVIDER_ID,
                    "source_url": source.get("source_url"),
                    "source_kind": "pricing_mention_in_docs",
                    "title": source.get("title"),
                    "headings": source.get("headings", []),
                    "raw_platform_hint": source.get("raw_platform_hint"),
                    "text": text,
                    "matched_keywords_group": "pricing",
                    "parsed_at": source.get("parsed_at", now_iso()),
                }
            )

    return pricing_sources


def extract_pricing_lines_from_text(
    text: str,
    service_name_hint: str | None,
    source_url: str,
    source_kind: str,
    parsed_at: str | None = None,
) -> list[dict[str, Any]]:
    """
    Вытаскивает сырые тарифные фрагменты из текста.

    Логика:
    - сначала ищем цену целиком: 1 290 ₽, 15 500 ₽, 100 рублей;
    - потом берём фрагмент вокруг цены;
    - если цен нет, сохраняем тарифные фрагменты по ключевым словам.
    """
    normalized = normalize_spaces(text)

    if not normalized:
        return []

    pricing_items = []

    price_pattern = r"\d[\d\s]*(?:[.,]\d+)?\s*(?:₽|руб\.?|рублей)"

    matches = list(
        re.finditer(
            price_pattern,
            normalized,
            flags=re.IGNORECASE,
        )
    )

    for match in matches[:500]:
        start = max(0, match.start() - 500)
        end = min(len(normalized), match.end() + 700)

        item_text = normalize_spaces(normalized[start:end])
        price_raw = match.group(0)

        unit_matches = []
        unit_patterns = [
            "час",
            "месяц",
            "мес",
            "гб",
            "gb",
            "vcpu",
            "cpu",
            "ram",
            "шт",
            "запрос",
            "трафик",
            "free tier",
        ]

        lower = item_text.lower()
        for unit in unit_patterns:
            if unit in lower:
                unit_matches.append(unit)

        pricing_items.append(
            {
                "provider_id": PROVIDER_ID,
                "service_name_hint": service_name_hint,
                "source_url": source_url,
                "source_kind": source_kind,
                "item_name_raw": None,
                "price_raw": [price_raw],
                "unit_raw": sorted(set(unit_matches)),
                "region_hint": guess_region_hint(item_text),
                "raw_text": item_text,
                "parsed_at": parsed_at or now_iso(),
                "normalization_status": "not_normalized",
            }
        )

    if pricing_items:
        return pricing_items

    # fallback: если явных цен не нашли, всё равно сохраняем тарифные фрагменты
    split_pattern = (
        r"(?i)"
        r"(?=цена|стоимость|тариф|оплата|vcpu|cpu|ram|гб|gb|"
        r"диск|хранилище|трафик|запрос|час|месяц|мес|free tier)"
    )

    parts = re.split(split_pattern, normalized)

    for part in parts:
        part = normalize_spaces(part)

        if len(part) < 20:
            continue

        if not contains_any_keyword(part, PRICING_KEYWORDS):
            continue

        item_text = part[:1200]

        pricing_items.append(
            {
                "provider_id": PROVIDER_ID,
                "service_name_hint": service_name_hint,
                "source_url": source_url,
                "source_kind": source_kind,
                "item_name_raw": None,
                "price_raw": [],
                "unit_raw": [],
                "region_hint": guess_region_hint(item_text),
                "raw_text": item_text,
                "parsed_at": parsed_at or now_iso(),
                "normalization_status": "not_normalized",
            }
        )

        if len(pricing_items) >= 400:
            break

    return pricing_items

def build_pricing_items_raw(
    pricing_sources: list[dict[str, Any]],
    service_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Создаёт pricing_items_raw.

    Берём:
    - страницу тарифов;
    - тарифные документы;
    - страницы услуг с ценовыми признаками.
    """
    pricing_items_raw = []

    for source in pricing_sources:
        text = source.get("text", "")
        source_kind = source.get("source_kind", "pricing_source")
        source_url = source.get("source_url", "")
        parsed_at = source.get("parsed_at", now_iso())

        pricing_items_raw.extend(
            extract_pricing_lines_from_text(
                text=text,
                service_name_hint=(
                    source.get("raw_name")
                    or source.get("anchor")
                    or source.get("title")
                ),
                source_url=source_url,
                source_kind=source_kind,
                parsed_at=parsed_at,
            )
        )

    for candidate in service_candidates:
        text = candidate.get("raw_text", "")

        if not contains_any_keyword(text, PRICING_KEYWORDS):
            continue

        pricing_items_raw.extend(
            extract_pricing_lines_from_text(
                text=text,
                service_name_hint=candidate.get("raw_name"),
                source_url=candidate.get("source_url", ""),
                source_kind="service_candidate_pricing_fragment",
                parsed_at=candidate.get("parsed_at", now_iso()),
            )
        )

    return pricing_items_raw


def build_api_sources(docs_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Из docs_sources выделяет страницы, связанные с API, Terraform, CLI, SDK.
    """
    api_sources = []

    for source in docs_sources:
        text = " ".join(
            [
                str(source.get("source_url", "")),
                str(source.get("title", "")),
                " ".join(source.get("headings", [])),
                str(source.get("text", "")),
            ]
        ).lower()

        if any(keyword in text for keyword in API_KEYWORDS):
            api_sources.append(
                {
                    "provider_id": PROVIDER_ID,
                    "source_url": source.get("source_url"),
                    "source_kind": "api_or_automation_docs",
                    "title": source.get("title"),
                    "headings": source.get("headings", []),
                    "raw_platform_hint": source.get("raw_platform_hint"),
                    "text": source.get("text", ""),
                    "parsed_at": source.get("parsed_at", now_iso()),
                    "note": (
                        "Сохранена только публичная документация. "
                        "Закрытые API-запросы с токеном не выполнялись."
                    ),
                }
            )

    return api_sources


def build_security_sources(
    security_source: RawSource | None,
    service_candidates: list[dict[str, Any]],
    docs_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Собирает сырьё по безопасности, compliance, 152-ФЗ и регионам.

    Это широкий слой.
    Для строгой фильтрации используются:
    - compliance_evidence_raw;
    - region_evidence_raw.
    """
    security_sources = []

    if security_source is not None:
        security_sources.append(
            {
                "provider_id": PROVIDER_ID,
                "source_url": security_source.url,
                "source_kind": "security_page",
                "title": security_source.title,
                "headings": security_source.headings,
                "text": security_source.text,
                "links": security_source.links,
                "document_links": security_source.document_links,
                "downloaded_documents": security_source.downloaded_documents,
                "has_security_keywords": contains_any_keyword(security_source.text, SECURITY_KEYWORDS),
                "has_compliance_keywords": contains_any_keyword(security_source.text, STRICT_COMPLIANCE_KEYWORDS),
                "has_region_keywords": contains_any_keyword(security_source.text, REGION_KEYWORDS),
                "parsed_at": security_source.parsed_at,
            }
        )

        for doc in security_source.downloaded_documents:
            security_sources.append(
                {
                    "provider_id": PROVIDER_ID,
                    "source_url": doc.get("url"),
                    "source_kind": "security_document",
                    "anchor": doc.get("anchor", ""),
                    "local_path": doc.get("local_path"),
                    "content_type": doc.get("content_type"),
                    "text": doc.get("extracted_text", ""),
                    "status": doc.get("status"),
                    "error": doc.get("error"),
                    "parsed_at": now_iso(),
                }
            )

    for candidate in service_candidates:
        text = candidate.get("raw_text", "")

        if contains_any_keyword(text, SECURITY_KEYWORDS) or contains_any_keyword(text, REGION_KEYWORDS):
            security_sources.append(
                {
                    "provider_id": PROVIDER_ID,
                    "source_url": candidate.get("source_url"),
                    "source_kind": "security_or_region_mention_in_service_page",
                    "raw_name": candidate.get("raw_name"),
                    "raw_platform_hint": candidate.get("raw_platform_hint"),
                    "text": text,
                    "has_security_keywords": contains_any_keyword(text, SECURITY_KEYWORDS),
                    "has_compliance_keywords": contains_any_keyword(text, STRICT_COMPLIANCE_KEYWORDS),
                    "has_region_keywords": contains_any_keyword(text, REGION_KEYWORDS),
                    "parsed_at": candidate.get("parsed_at", now_iso()),
                }
            )

    for source in docs_sources:
        text = source.get("text", "")

        if contains_any_keyword(text, SECURITY_KEYWORDS) or contains_any_keyword(text, REGION_KEYWORDS):
            security_sources.append(
                {
                    "provider_id": PROVIDER_ID,
                    "source_url": source.get("source_url"),
                    "source_kind": "security_or_region_mention_in_docs",
                    "title": source.get("title"),
                    "headings": source.get("headings", []),
                    "raw_platform_hint": source.get("raw_platform_hint"),
                    "text": text,
                    "has_security_keywords": contains_any_keyword(text, SECURITY_KEYWORDS),
                    "has_compliance_keywords": contains_any_keyword(text, STRICT_COMPLIANCE_KEYWORDS),
                    "has_region_keywords": contains_any_keyword(text, REGION_KEYWORDS),
                    "parsed_at": source.get("parsed_at", now_iso()),
                }
            )

    return security_sources


def build_compliance_evidence_raw(
    service_candidates: list[dict[str, Any]],
    docs_sources: list[dict[str, Any]],
    security_sources: list[dict[str, Any]],
    source_pages: list[RawSource],
) -> list[dict[str, Any]]:
    """
    Создаёт evidence-фрагменты по 152-ФЗ / ФСТЭК / ИСПДн / УЗ.

    Важно:
    - provider_level не считается строгим подтверждением услуги;
    - service_level и document_level подходят для строгой фильтрации.
    """
    evidence_items = []

    # 1. Evidence со страниц конкретных услуг.
    for candidate in service_candidates:
        text = candidate.get("raw_text", "")

        if not contains_any_keyword(text, STRICT_COMPLIANCE_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_COMPLIANCE_KEYWORDS,
            window=700,
            max_fragments=5,
        )

        for fragment in fragments:
            scope = guess_evidence_scope("service_detail_page")

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": candidate.get("raw_name"),
                    "service_url": candidate.get("source_url"),
                    "source_url": candidate.get("source_url"),
                    "source_kind": "service_detail_page",
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_COMPLIANCE_KEYWORDS,
                    ),
                    "compliance_tags_hint": guess_compliance_tags(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_compliance_evidence": is_strict_scope(scope),
                    "raw_platform_hint": candidate.get("raw_platform_hint"),
                    "parsed_at": candidate.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 2. Evidence из документации.
    for source in docs_sources:
        text = source.get("text", "")

        if not contains_any_keyword(text, STRICT_COMPLIANCE_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_COMPLIANCE_KEYWORDS,
            window=700,
            max_fragments=5,
        )

        for fragment in fragments:
            scope = guess_evidence_scope(source.get("source_kind", "docs_detail_page"))

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": source.get("title"),
                    "service_url": source.get("source_url"),
                    "source_url": source.get("source_url"),
                    "source_kind": source.get("source_kind", "docs_detail_page"),
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_COMPLIANCE_KEYWORDS,
                    ),
                    "compliance_tags_hint": guess_compliance_tags(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_compliance_evidence": is_strict_scope(scope),
                    "raw_platform_hint": source.get("raw_platform_hint"),
                    "parsed_at": source.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 3. Evidence из security sources.
    for source in security_sources:
        text = source.get("text", "")

        if not contains_any_keyword(text, STRICT_COMPLIANCE_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_COMPLIANCE_KEYWORDS,
            window=700,
            max_fragments=5,
        )

        for fragment in fragments:
            scope = guess_evidence_scope(source.get("source_kind", "security_source"))

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": source.get("raw_name") or source.get("title"),
                    "service_url": source.get("source_url") if is_strict_scope(scope) else None,
                    "source_url": source.get("source_url"),
                    "source_kind": source.get("source_kind", "security_source"),
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_COMPLIANCE_KEYWORDS,
                    ),
                    "compliance_tags_hint": guess_compliance_tags(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_compliance_evidence": is_strict_scope(scope),
                    "raw_platform_hint": source.get("raw_platform_hint"),
                    "parsed_at": source.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 4. Evidence со стартовых страниц — provider_level.
    for page in source_pages:
        text = page.text

        if not contains_any_keyword(text, STRICT_COMPLIANCE_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_COMPLIANCE_KEYWORDS,
            window=700,
            max_fragments=3,
        )

        for fragment in fragments:
            scope = "provider_level"

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": None,
                    "service_url": None,
                    "source_url": page.url,
                    "source_kind": page.source_type,
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_COMPLIANCE_KEYWORDS,
                    ),
                    "compliance_tags_hint": guess_compliance_tags(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_compliance_evidence": False,
                    "raw_platform_hint": detect_platform_hint(fragment["evidence_text"], page.url),
                    "parsed_at": page.parsed_at,
                    "normalization_status": "not_normalized",
                }
            )

    return evidence_items


def build_region_evidence_raw(
    service_candidates: list[dict[str, Any]],
    docs_sources: list[dict[str, Any]],
    pricing_sources: list[dict[str, Any]],
    security_sources: list[dict[str, Any]],
    source_pages: list[RawSource],
) -> list[dict[str, Any]]:
    """
    Создаёт evidence-фрагменты по регионам.

    Для жёсткого фильтра подходят:
    - service_level;
    - document_level;
    - pricing_level.

    Provider-level используется только как справка.
    """
    evidence_items = []

    # 1. Регионы со страниц услуг.
    for candidate in service_candidates:
        text = candidate.get("raw_text", "")

        if not contains_any_keyword(text, STRICT_REGION_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_REGION_KEYWORDS,
            window=700,
            max_fragments=5,
        )

        for fragment in fragments:
            scope = guess_evidence_scope("service_detail_page")

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": candidate.get("raw_name"),
                    "service_url": candidate.get("source_url"),
                    "source_url": candidate.get("source_url"),
                    "source_kind": "service_detail_page",
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_REGION_KEYWORDS,
                    ),
                    "region_hint": guess_region_hint(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_region_evidence": is_strict_scope(scope),
                    "raw_platform_hint": candidate.get("raw_platform_hint"),
                    "parsed_at": candidate.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 2. Регионы из документации.
    for source in docs_sources:
        text = source.get("text", "")

        if not contains_any_keyword(text, STRICT_REGION_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_REGION_KEYWORDS,
            window=700,
            max_fragments=5,
        )

        for fragment in fragments:
            scope = guess_evidence_scope(source.get("source_kind", "docs_detail_page"))

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": source.get("title"),
                    "service_url": source.get("source_url"),
                    "source_url": source.get("source_url"),
                    "source_kind": source.get("source_kind", "docs_detail_page"),
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_REGION_KEYWORDS,
                    ),
                    "region_hint": guess_region_hint(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_region_evidence": is_strict_scope(scope),
                    "raw_platform_hint": source.get("raw_platform_hint"),
                    "parsed_at": source.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 3. Регионы из тарифных источников.
    for source in pricing_sources:
        text = source.get("text", "")

        if not contains_any_keyword(text, STRICT_REGION_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_REGION_KEYWORDS,
            window=700,
            max_fragments=5,
        )

        for fragment in fragments:
            scope = guess_evidence_scope(source.get("source_kind", "pricing_source"))

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": source.get("raw_name") or source.get("title") or source.get("anchor"),
                    "service_url": source.get("source_url") if is_strict_scope(scope) else None,
                    "source_url": source.get("source_url"),
                    "source_kind": source.get("source_kind", "pricing_source"),
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_REGION_KEYWORDS,
                    ),
                    "region_hint": guess_region_hint(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_region_evidence": is_strict_scope(scope),
                    "raw_platform_hint": source.get("raw_platform_hint"),
                    "parsed_at": source.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 4. Регионы из security sources.
    for source in security_sources:
        text = source.get("text", "")

        if not contains_any_keyword(text, STRICT_REGION_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_REGION_KEYWORDS,
            window=700,
            max_fragments=3,
        )

        for fragment in fragments:
            scope = guess_evidence_scope(source.get("source_kind", "security_source"))

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": source.get("raw_name") or source.get("title"),
                    "service_url": source.get("source_url") if is_strict_scope(scope) else None,
                    "source_url": source.get("source_url"),
                    "source_kind": source.get("source_kind", "security_source"),
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_REGION_KEYWORDS,
                    ),
                    "region_hint": guess_region_hint(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_region_evidence": is_strict_scope(scope),
                    "raw_platform_hint": source.get("raw_platform_hint"),
                    "parsed_at": source.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 5. Стартовые страницы — provider_level.
    for page in source_pages:
        text = page.text

        if not contains_any_keyword(text, STRICT_REGION_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=STRICT_REGION_KEYWORDS,
            window=700,
            max_fragments=3,
        )

        for fragment in fragments:
            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": None,
                    "service_url": None,
                    "source_url": page.url,
                    "source_kind": page.source_type,
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_REGION_KEYWORDS,
                    ),
                    "region_hint": guess_region_hint(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": "provider_level",
                    "is_strict_region_evidence": False,
                    "raw_platform_hint": detect_platform_hint(fragment["evidence_text"], page.url),
                    "parsed_at": page.parsed_at,
                    "normalization_status": "not_normalized",
                }
            )

    return evidence_items


def collect_cloud_ru_raw_data() -> RawDataset:
    """
    Главная функция сбора данных по Cloud.ru.
    """
    dataset = RawDataset(
        provider={
            "provider_id": PROVIDER_ID,
            "name": PROVIDER_NAME,
            "base_url": BASE_URL,
            "raw_known_fields": {
                "services_url": START_PAGES["services"],
                "docs_url": START_PAGES["docs"],
                "tariffs_url": START_PAGES["tariffs"],
                "security_url": START_PAGES["security"],
            },
            "notes": [
                "Собраны только публичные страницы без авторизации.",
                "Закрытые API-запросы с токеном не используются.",
                "Данные не нормализованы.",
                "JSON предназначен для следующего шага: LLM-normalization.",
                "Cloud.ru парсится как HTML-first/docs-first источник.",
                "Для Cloud.ru сохраняется raw_platform_hint: Evolution, Advanced, VMware или Evolution Stack.",
                "pricing_items_raw содержит сырые тарифные фрагменты, а не финальные цены.",
                "compliance_evidence_raw содержит короткие доказательные фрагменты по 152-ФЗ / ФСТЭК / ИСПДн / ISO / PCI DSS.",
                "region_evidence_raw содержит короткие доказательные фрагменты по регионам.",
                "Для строгой фильтрации использовать только evidence с is_strict_* = true.",
            ],
        },
        collected_at=now_iso(),
    )

    collected_by_type: dict[str, RawSource | None] = {}

    for source_type, url in START_PAGES.items():
        page, log = collect_html_page(
            provider_id=PROVIDER_ID,
            source_type=source_type,
            url=url,
            allowed_domains=ALLOWED_DOMAINS,
            downloads_output_dir=DOWNLOADS_OUTPUT_DIR,
            download_documents=True,
        )

        dataset.parse_log.append(log)
        collected_by_type[source_type] = page

        if page:
            dataset.source_pages.append(page)

    services_source = collected_by_type.get("services")
    docs_source = collected_by_type.get("docs")
    tariffs_source = collected_by_type.get("tariffs")
    security_source = collected_by_type.get("security")

    dataset.service_candidates = build_service_candidates(
        services_source=services_source,
        limit=60,
    )

    dataset.docs_sources = build_docs_sources(
        docs_source=docs_source,
        service_candidates=dataset.service_candidates,
        limit=60,
    )

    dataset.api_sources = build_api_sources(dataset.docs_sources)

    dataset.pricing_sources = build_pricing_sources(
        tariffs_source=tariffs_source,
        service_candidates=dataset.service_candidates,
        docs_sources=dataset.docs_sources,
    )

    dataset.pricing_items_raw = build_pricing_items_raw(
        pricing_sources=dataset.pricing_sources,
        service_candidates=dataset.service_candidates,
    )

    dataset.security_sources = build_security_sources(
        security_source=security_source,
        service_candidates=dataset.service_candidates,
        docs_sources=dataset.docs_sources,
    )

    dataset.compliance_evidence_raw = build_compliance_evidence_raw(
        service_candidates=dataset.service_candidates,
        docs_sources=dataset.docs_sources,
        security_sources=dataset.security_sources,
        source_pages=dataset.source_pages,
    )

    dataset.region_evidence_raw = build_region_evidence_raw(
        service_candidates=dataset.service_candidates,
        docs_sources=dataset.docs_sources,
        pricing_sources=dataset.pricing_sources,
        security_sources=dataset.security_sources,
        source_pages=dataset.source_pages,
    )

    return dataset


def main() -> None:
    dataset = collect_cloud_ru_raw_data()

    save_json(RAW_OUTPUT_PATH, dataset_to_dict(dataset))
    save_json(LOG_OUTPUT_PATH, dataset.parse_log)

    print_dataset_summary(
        dataset=dataset,
        raw_path=RAW_OUTPUT_PATH,
        log_path=LOG_OUTPUT_PATH,
    )

    print(f"Pricing items raw: {len(dataset.pricing_items_raw)}")
    print(f"Compliance evidence raw: {len(dataset.compliance_evidence_raw)}")
    print(f"Region evidence raw: {len(dataset.region_evidence_raw)}")


if __name__ == "__main__":
    main()