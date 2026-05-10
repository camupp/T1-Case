"""
RawParserT1.py

Парсер публичных источников T1 Cloud.

Задача:
- собрать сырые данные по T1 Cloud;
- сохранить HTML-страницы, ссылки, PDF-документы и извлечённый текст;
- НЕ нормализовать данные;
- подготовить JSON для последующей LLM-нормализации.

Новая структура:
- service_candidates;
- pricing_sources;
- pricing_items_raw;
- api_sources;
- security_sources;
- compliance_evidence_raw;
- region_evidence_raw.

Запуск из корня проекта:
    python parsers/RawParserT1.py

Результат:
    data/raw/t1_cloud_raw.json
    data/raw/t1_cloud_parse_log.json
    data/raw/downloads/t1-cloud/...
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
    download_document,
    is_document_url,
    normalize_spaces,
    now_iso,
    print_dataset_summary,
    save_json,
)


PROVIDER_ID = "t1-cloud"
PROVIDER_NAME = "Т1 Облако"
BASE_URL = "https://t1-cloud.ru"

ALLOWED_DOMAINS = {
    "t1-cloud.ru",
    "www.t1-cloud.ru",
}

OUTPUT_DIR = Path("data/raw")
DOWNLOADS_OUTPUT_DIR = OUTPUT_DIR / "downloads"

RAW_OUTPUT_PATH = OUTPUT_DIR / "t1_cloud_raw.json"
LOG_OUTPUT_PATH = OUTPUT_DIR / "t1_cloud_parse_log.json"


START_PAGES = {
    "services_documents": "https://t1-cloud.ru/documents/services",
    "rates_documents": "https://t1-cloud.ru/documents/rates",
    "api_docs": "https://t1-cloud.ru/docs/article/api",
}


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
    "приказ фстэк",
    "приказ №17",
    "приказ № 17",
    "приказ №21",
    "приказ № 21",
    "аттестат",
    "аттестован",
    "аттестованное облако",
    "к1",
    "1г",
]

REGION_KEYWORDS = [
    "москва",
    "москов",
    "россия",
    "российская федерация",
    "территории россии",
    "территория россии",
    "дата-центры оператора находятся",
    "дата центры оператора находятся",
    "дата-центр",
    "дата центр",
    "зона доступности",
    "зоны доступности",
    "availability zone",
]

PRICING_KEYWORDS = [
    "тариф",
    "тарифы",
    "тарификация",
    "стоимость",
    "цена",
    "руб",
    "₽",
    "оплата",
    "pay as you go",
    "ежемесяч",
    "месяц",
    "минут",
    "час",
    "гб",
    "gb",
    "vcpu",
    "ram",
    "диск",
    "хранилище",
]


def clean_service_name(anchor: str) -> str:
    """
    Очищает название услуги от лишней юридической формулировки.
    """
    name = normalize_spaces(anchor)

    patterns = [
        r"^Общие условия оказания услуги\s+",
        r"^Общие условия оказания услуги\s+«",
        r"»$",
        r"^«",
    ]

    for pattern in patterns:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE).strip()

    return normalize_spaces(name)


def looks_like_service_link(link: dict[str, str]) -> bool:
    """
    Проверяет, похожа ли ссылка на документ/страницу услуги T1.
    """
    anchor = normalize_spaces(link.get("anchor", ""))

    if not anchor:
        return False

    lower = anchor.lower()

    service_markers = [
        "общие условия оказания услуги",
        "managed service",
        "kubernetes",
        "postgresql",
        "clickhouse",
        "rabbitmq",
        "kafka",
        "s3",
        "cdn",
        "clouddns",
        "gitlab",
        "openstack",
        "виртуальный дата-центр",
        "резервное копирование",
        "объектное хранилище",
        "network load balancer",
        "repository for containers",
        "ml hub",
    ]

    return any(marker in lower for marker in service_markers)


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


def extract_evidence_fragments(
    text: str,
    keywords: list[str],
    window: int = 700,
    max_fragments: int = 5,
) -> list[dict[str, Any]]:
    """
    Вырезает короткие фрагменты текста вокруг ключевых слов.

    Это нужно, чтобы не отдавать LLM весь большой документ,
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


def guess_region_hint(evidence_text: str) -> str | None:
    """
    Грубая подсказка по региону.
    Это не финальная нормализация.
    """
    lower = evidence_text.lower()

    if "моск" in lower:
        return "Moscow"

    if "росси" in lower:
        return "Russia"

    return None


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

    if "гис" in lower:
        tags.append("GIS")

    return sorted(set(tags))


def guess_evidence_scope(source_kind: str) -> str:
    """
    Определяет уровень доказательства.

    Для строгой фильтрации подходят:
    - service_level;
    - document_level;
    - pricing_level.

    provider_level и platform_level — только справочно.
    """
    if source_kind in {"service_document", "service_candidate"}:
        return "document_level"

    if source_kind in {"pricing_document", "pricing_page"}:
        return "pricing_level"

    if source_kind in {"service_page"}:
        return "service_level"

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


def build_service_candidates(services_source: RawSource | None) -> list[dict[str, Any]]:
    """
    Формирует список кандидатов на услуги из страницы documents/services.

    На выходе не нормализованные services, а сырьё для LLM:
    - raw_name;
    - source_url;
    - raw_text;
    - downloaded_document;
    - parsed_at.
    """
    if services_source is None:
        return []

    downloaded_by_url = {
        doc.get("url"): doc
        for doc in services_source.downloaded_documents
        if doc.get("url")
    }

    candidates = []

    for link in services_source.links:
        if not looks_like_service_link(link):
            continue

        service_name = clean_service_name(link["anchor"])

        raw_doc_text = ""
        downloaded_doc = None

        if is_document_url(link["url"]):
            downloaded_doc = downloaded_by_url.get(link["url"])

            if downloaded_doc is None:
                downloaded_doc = download_document(
                    url=link["url"],
                    provider_id=PROVIDER_ID,
                    output_dir=DOWNLOADS_OUTPUT_DIR,
                )

            raw_doc_text = downloaded_doc.get("extracted_text", "")

        else:
            page, _ = collect_html_page(
                provider_id=PROVIDER_ID,
                source_type="service_detail_candidate",
                url=link["url"],
                allowed_domains=ALLOWED_DOMAINS,
                downloads_output_dir=DOWNLOADS_OUTPUT_DIR,
                download_documents=True,
            )
            raw_doc_text = page.text if page else ""

        candidate = {
            "provider_id": PROVIDER_ID,
            "raw_name": service_name,
            "raw_anchor": link["anchor"],
            "source_url": link["url"],
            "parent_source_url": services_source.url,
            "raw_text": raw_doc_text,
            "downloaded_document": downloaded_doc,
            "has_pricing_keywords": contains_any_keyword(raw_doc_text, PRICING_KEYWORDS),
            "has_compliance_keywords": contains_any_keyword(raw_doc_text, STRICT_COMPLIANCE_KEYWORDS),
            "has_region_keywords": contains_any_keyword(raw_doc_text, REGION_KEYWORDS),
            "parsed_at": now_iso(),
            "normalization_status": "not_normalized",
        }

        candidates.append(candidate)

    return candidates


def build_pricing_sources(rates_source: RawSource | None) -> list[dict[str, Any]]:
    """
    Формирует сырой список источников по тарифам.
    """
    if rates_source is None:
        return []

    pricing_sources = []

    pricing_sources.append(
        {
            "provider_id": PROVIDER_ID,
            "source_url": rates_source.url,
            "source_kind": "pricing_page",
            "title": rates_source.title,
            "headings": rates_source.headings,
            "text": rates_source.text,
            "links": rates_source.links,
            "document_links": rates_source.document_links,
            "downloaded_documents": rates_source.downloaded_documents,
            "parsed_at": rates_source.parsed_at,
        }
    )

    for doc in rates_source.downloaded_documents:
        pricing_sources.append(
            {
                "provider_id": PROVIDER_ID,
                "source_url": doc.get("url"),
                "source_kind": "pricing_document",
                "anchor": doc.get("anchor", ""),
                "local_path": doc.get("local_path"),
                "content_type": doc.get("content_type"),
                "text": doc.get("extracted_text", ""),
                "status": doc.get("status"),
                "error": doc.get("error"),
                "parsed_at": now_iso(),
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

    Для T1 важно:
    - в тарифном PDF цены часто идут без символа ₽;
    - валюта указана в заголовке таблицы: "руб.";
    - строки выглядят примерно так:
      vCPU a1 ... шт 0,01705278 736,68
      RAM ГБ 0,00412755 178,31
    """
    normalized = normalize_spaces(text)

    if not normalized:
        return []

    pricing_items = []

    # 1. Сначала ищем обычные цены с ₽ / руб / рублей.
    price_pattern_with_currency = r"\d[\d\s]*(?:[.,]\d+)?\s*(?:₽|руб\.?|рублей)"

    matches = list(
        re.finditer(
            price_pattern_with_currency,
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
            "минут",
            "гб",
            "gb",
            "vcpu",
            "ram",
            "шт",
            "запрос",
            "трафик",
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

    # 2. Специальный режим для тарифного PDF T1.
    # Ловим строки, где есть единица измерения и две цены:
    # цена за минуту + цена за месяц.
    if "стоимость за единицу в минуту" in normalized.lower() and "руб" in normalized.lower():
        t1_table_pattern = (
            r"(?P<row_number>\d+)\.\s+"
            r"(?P<item_name>[^.]{3,160}?)\s+"
            r"(?P<unit>шт|ГБ|GB|Мбит/с|IP|запрос(?:ов)?|объект(?:ов)?|правило|домен(?:ов)?|бакет(?:ов)?)\s+"
            r"(?P<price_per_minute>\d+[.,]\d+)\s+"
            r"(?P<price_per_month>\d[\d\s]*(?:[.,]\d+)?)"
        )

        for match in re.finditer(t1_table_pattern, normalized, flags=re.IGNORECASE):
            start = max(0, match.start() - 300)
            end = min(len(normalized), match.end() + 300)

            item_text = normalize_spaces(normalized[start:end])

            item_name = normalize_spaces(match.group("item_name"))
            unit_raw = normalize_spaces(match.group("unit"))
            price_per_minute = normalize_spaces(match.group("price_per_minute"))
            price_per_month = normalize_spaces(match.group("price_per_month"))

            pricing_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": service_name_hint,
                    "source_url": source_url,
                    "source_kind": source_kind,
                    "item_name_raw": item_name,
                    "price_raw": [
                        {
                            "price_per_minute_raw": price_per_minute,
                            "price_per_month_raw": price_per_month,
                            "currency_hint": "RUB",
                            "vat_hint": "without_vat",
                        }
                    ],
                    "unit_raw": [unit_raw],
                    "region_hint": guess_region_hint(item_text),
                    "raw_text": item_text,
                    "parsed_at": parsed_at or now_iso(),
                    "normalization_status": "not_normalized",
                }
            )

            if len(pricing_items) >= 800:
                break

    if pricing_items:
        return pricing_items

    # 3. Fallback: если явных цен не нашли,
    # сохраняем тарифные фрагменты по ключевым словам.
    split_pattern = (
        r"(?i)"
        r"(?=тариф|стоимость|цена|оплата|pay as you go|vcpu|ram|гб|gb|"
        r"диск|хранилище|трафик|запрос|час|месяц|мес|минут)"
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
    service_candidates: list[dict[str, Any]],
    pricing_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Создаёт pricing_items_raw.

    Берём:
    - тарифные документы;
    - тарифную страницу;
    - короткие тарифные фрагменты из service_candidates.
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
                service_name_hint=source.get("anchor") or source.get("title"),
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


def build_api_sources(api_source: RawSource | None) -> list[dict[str, Any]]:
    """
    Формирует сырой список API-источников.

    Берём:
    - главную страницу API;
    - внутренние ссылки вида /docs/article/api...
    """
    if api_source is None:
        return []

    api_sources = [
        {
            "provider_id": PROVIDER_ID,
            "source_url": api_source.url,
            "source_kind": "api_index_page",
            "title": api_source.title,
            "headings": api_source.headings,
            "text": api_source.text,
            "parsed_at": api_source.parsed_at,
            "note": (
                "Сохранена только публичная API-документация. "
                "Закрытые API-запросы с токеном не выполнялись."
            ),
        }
    ]

    api_links = []

    for link in api_source.links:
        anchor = normalize_spaces(link.get("anchor", ""))
        url = link.get("url", "")

        if "/docs/article/api" in url and anchor:
            api_links.append(link)

    api_links = deduplicate_links(api_links)

    for link in api_links:
        page, _ = collect_html_page(
            provider_id=PROVIDER_ID,
            source_type="api_detail_page",
            url=link["url"],
            allowed_domains=ALLOWED_DOMAINS,
            downloads_output_dir=DOWNLOADS_OUTPUT_DIR,
            download_documents=True,
        )

        api_sources.append(
            {
                "provider_id": PROVIDER_ID,
                "source_url": link["url"],
                "source_kind": "api_detail_page",
                "anchor": link.get("anchor", ""),
                "title": page.title if page else None,
                "headings": page.headings if page else [],
                "text": page.text if page else "",
                "parsed_at": page.parsed_at if page else now_iso(),
                "note": (
                    "Сохранена только публичная API-документация. "
                    "Закрытые API-запросы с токеном не выполнялись."
                ),
            }
        )

    return api_sources


def build_security_sources(source_pages: list[RawSource]) -> list[dict[str, Any]]:
    """
    Вытаскивает сырьё по безопасности и персональным данным.

    Это широкий слой security_sources.
    Для строгой проверки 152-ФЗ используется compliance_evidence_raw.
    """
    security_keywords = [
        "персональных данных",
        "политика обработки персональных данных",
        "пользовательское соглашение",
        "152-фз",
        "безопасность",
        "защита",
        "сертификат",
        "compliance",
    ]

    security_sources = []

    for page in source_pages:
        for link in page.links:
            anchor = normalize_spaces(link.get("anchor", ""))
            lower_anchor = anchor.lower()
            url = link.get("url", "")

            if not any(keyword in lower_anchor for keyword in security_keywords):
                continue

            item = {
                "provider_id": PROVIDER_ID,
                "source_url": url,
                "source_kind": "security_or_legal_source",
                "anchor": anchor,
                "parent_source_url": page.url,
                "text": "",
                "local_path": None,
                "parsed_at": now_iso(),
            }

            if is_document_url(url):
                downloaded = download_document(
                    url=url,
                    provider_id=PROVIDER_ID,
                    output_dir=DOWNLOADS_OUTPUT_DIR,
                )

                item["text"] = downloaded.get("extracted_text", "")
                item["local_path"] = downloaded.get("local_path")
                item["downloaded_document"] = downloaded
            else:
                detail_page, _ = collect_html_page(
                    provider_id=PROVIDER_ID,
                    source_type="security_detail_page",
                    url=url,
                    allowed_domains=ALLOWED_DOMAINS,
                    downloads_output_dir=DOWNLOADS_OUTPUT_DIR,
                    download_documents=True,
                )

                item["text"] = detail_page.text if detail_page else ""

            security_sources.append(item)

    return security_sources


def build_compliance_evidence_raw(
    service_candidates: list[dict[str, Any]],
    security_sources: list[dict[str, Any]],
    source_pages: list[RawSource],
) -> list[dict[str, Any]]:
    """
    Создаёт строгие evidence-фрагменты по 152-ФЗ / ФСТЭК / ИСПДн / УЗ.

    Важно:
    - provider_level не считается строгим подтверждением услуги;
    - document_level и service_level подходят для строгой фильтрации.
    """
    evidence_items = []

    # 1. Evidence из документов конкретных услуг.
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
            scope = guess_evidence_scope("service_candidate")

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": candidate.get("raw_name"),
                    "service_url": candidate.get("source_url"),
                    "source_url": candidate.get("source_url"),
                    "source_kind": "service_candidate",
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        STRICT_COMPLIANCE_KEYWORDS,
                    ),
                    "compliance_tags_hint": guess_compliance_tags(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_compliance_evidence": is_strict_scope(scope),
                    "parsed_at": candidate.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 2. Evidence из security/legal sources.
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
            # security source чаще provider-level, если он не привязан к конкретной услуге.
            scope = "provider_level"

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": None,
                    "service_url": None,
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
                    "parsed_at": source.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 3. Evidence со стартовых страниц — только provider/platform-level.
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
                    "is_strict_compliance_evidence": is_strict_scope(scope),
                    "parsed_at": page.parsed_at,
                    "normalization_status": "not_normalized",
                }
            )

    return evidence_items


def build_region_evidence_raw(
    service_candidates: list[dict[str, Any]],
    pricing_sources: list[dict[str, Any]],
    source_pages: list[RawSource],
) -> list[dict[str, Any]]:
    """
    Создаёт строгие evidence-фрагменты по регионам.

    Для жёсткого фильтра подходят:
    - document_level;
    - service_level;
    - pricing_level.

    Provider-level используется только как справка.
    """
    evidence_items = []

    # 1. Регионы из документов конкретных услуг.
    for candidate in service_candidates:
        text = candidate.get("raw_text", "")

        if not contains_any_keyword(text, REGION_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=REGION_KEYWORDS,
            window=700,
            max_fragments=5,
        )

        for fragment in fragments:
            scope = guess_evidence_scope("service_candidate")

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": candidate.get("raw_name"),
                    "service_url": candidate.get("source_url"),
                    "source_url": candidate.get("source_url"),
                    "source_kind": "service_candidate",
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        REGION_KEYWORDS,
                    ),
                    "region_hint": guess_region_hint(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_region_evidence": is_strict_scope(scope),
                    "parsed_at": candidate.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 2. Регионы из тарифных источников.
    for source in pricing_sources:
        text = source.get("text", "")

        if not contains_any_keyword(text, REGION_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=REGION_KEYWORDS,
            window=700,
            max_fragments=5,
        )

        for fragment in fragments:
            scope = guess_evidence_scope(source.get("source_kind", "pricing_source"))

            evidence_items.append(
                {
                    "provider_id": PROVIDER_ID,
                    "service_name_hint": source.get("anchor") or source.get("title"),
                    "service_url": None,
                    "source_url": source.get("source_url"),
                    "source_kind": source.get("source_kind", "pricing_source"),
                    "matched_keywords": find_matched_keywords(
                        fragment["evidence_text"],
                        REGION_KEYWORDS,
                    ),
                    "region_hint": guess_region_hint(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_region_evidence": is_strict_scope(scope),
                    "parsed_at": source.get("parsed_at", now_iso()),
                    "normalization_status": "not_normalized",
                }
            )

    # 3. Регионы со стартовых страниц — provider-level.
    for page in source_pages:
        text = page.text

        if not contains_any_keyword(text, REGION_KEYWORDS):
            continue

        fragments = extract_evidence_fragments(
            text=text,
            keywords=REGION_KEYWORDS,
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
                        REGION_KEYWORDS,
                    ),
                    "region_hint": guess_region_hint(fragment["evidence_text"]),
                    "evidence_text": fragment["evidence_text"],
                    "evidence_scope": scope,
                    "is_strict_region_evidence": is_strict_scope(scope),
                    "parsed_at": page.parsed_at,
                    "normalization_status": "not_normalized",
                }
            )

    return evidence_items


def collect_t1_cloud_raw_data() -> RawDataset:
    """
    Главная функция сбора данных по T1 Cloud.
    """
    dataset = RawDataset(
        provider={
            "provider_id": PROVIDER_ID,
            "name": PROVIDER_NAME,
            "base_url": BASE_URL,
            "raw_known_fields": {
                "services_url": START_PAGES["services_documents"],
                "pricing_url": START_PAGES["rates_documents"],
                "api_docs_url": START_PAGES["api_docs"],
            },
            "notes": [
                "Собраны только публичные страницы без авторизации.",
                "Закрытые API-запросы с токеном не используются.",
                "Данные не нормализованы.",
                "JSON предназначен для следующего шага: LLM-normalization.",
                "Оригинальные PDF сохраняются локально в data/raw/downloads/t1-cloud/.",
                "pricing_items_raw содержит сырые тарифные фрагменты, а не финальные цены.",
                "compliance_evidence_raw содержит короткие доказательные фрагменты по 152-ФЗ / ФСТЭК / ИСПДн.",
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

    services_source = collected_by_type.get("services_documents")
    rates_source = collected_by_type.get("rates_documents")
    api_source = collected_by_type.get("api_docs")

    dataset.service_candidates = build_service_candidates(services_source)

    dataset.pricing_sources = build_pricing_sources(rates_source)

    dataset.pricing_items_raw = build_pricing_items_raw(
        service_candidates=dataset.service_candidates,
        pricing_sources=dataset.pricing_sources,
    )

    dataset.api_sources = build_api_sources(api_source)

    dataset.security_sources = build_security_sources(dataset.source_pages)

    dataset.compliance_evidence_raw = build_compliance_evidence_raw(
        service_candidates=dataset.service_candidates,
        security_sources=dataset.security_sources,
        source_pages=dataset.source_pages,
    )

    dataset.region_evidence_raw = build_region_evidence_raw(
        service_candidates=dataset.service_candidates,
        pricing_sources=dataset.pricing_sources,
        source_pages=dataset.source_pages,
    )

    return dataset


def main() -> None:
    dataset = collect_t1_cloud_raw_data()

    save_json(RAW_OUTPUT_PATH, dataset_to_dict(dataset))
    save_json(LOG_OUTPUT_PATH, dataset.parse_log)

    print_dataset_summary(
        dataset=dataset,
        raw_path=RAW_OUTPUT_PATH,
        log_path=LOG_OUTPUT_PATH,
    )


if __name__ == "__main__":
    main()