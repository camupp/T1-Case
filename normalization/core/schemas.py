from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# RAW SCHEMAS
# =============================================================================
# Эти схемы описывают НЕ итоговые нормализованные данные,
# а входные JSON-файлы, которые получаются после парсинга провайдеров.
#
# Ожидаемая структура raw-файла провайдера:
# provider
# collected_at
# source_pages
# service_candidates
# pricing_sources
# pricing_items_raw
# docs_sources
# api_sources
# security_sources
# compliance_evidence_raw
# region_evidence_raw
# parse_log
# =============================================================================


class RawProviderMeta(BaseModel):
    """
    Сырая информация о провайдере из raw JSON.

    Поля сделаны гибкими, потому что у разных парсеров структура provider
    может немного отличаться.
    """

    provider_id: str
    name: Optional[str] = None
    raw_name: Optional[str] = None
    base_url: Optional[str] = None

    api_docs_url: Optional[str] = None
    pricing_url: Optional[str] = None
    source_url: Optional[str] = None
    base_platform: Optional[str] = None

    extra: dict[str, Any] = Field(default_factory=dict)


class RawSourcePage(BaseModel):
    """
    Сырая страница-источник: главная страница, pricing page, docs page и т.д.
    """

    url: Optional[str] = None
    title: Optional[str] = None
    source_type: Optional[str] = None
    status: Optional[str] = None
    raw_text: Optional[str] = None
    parsed_at: Optional[datetime] = None

    extra: dict[str, Any] = Field(default_factory=dict)


class RawServiceCandidate(BaseModel):
    """
    Сырой кандидат облачной услуги.

    Именно эти объекты дальше превращаются в записи services.json.
    """

    raw_name: str
    raw_text: Optional[str] = None
    raw_description: Optional[str] = None

    source_url: Optional[str] = None
    service_url: Optional[str] = None
    document_url: Optional[str] = None
    title: Optional[str] = None

    has_compliance_keywords: bool = False
    has_region_keywords: bool = False

    parsed_at: Optional[datetime] = None

    extra: dict[str, Any] = Field(default_factory=dict)


class RawPricingItem(BaseModel):
    """
    Сырая тарифная позиция из pricing_items_raw.

    Эти объекты дальше превращаются в service_pricing_items.json.
    """

    item_name_raw: Optional[str] = None
    raw_text: Optional[str] = None

    price_raw: Any = None
    price_value_raw: Optional[Any] = None
    price_per_month_raw: Optional[Any] = None
    price_unit_raw: Optional[str] = None

    service_name_hint: Optional[str] = None
    region_hint: Optional[str] = None
    source_url: Optional[str] = None
    pricing_url: Optional[str] = None

    parsed_at: Optional[datetime] = None

    extra: dict[str, Any] = Field(default_factory=dict)


class RawEvidenceItem(BaseModel):
    """
    Сырой фрагмент evidence для compliance или регионов.
    """

    evidence_text: Optional[str] = None
    raw_text: Optional[str] = None
    source_url: Optional[str] = None
    evidence_type: Optional[str] = None
    parsed_at: Optional[datetime] = None

    extra: dict[str, Any] = Field(default_factory=dict)


class RawParseLogRecord(BaseModel):
    """
    Сырой лог парсинга из raw JSON.
    """

    provider_id: Optional[str] = None
    url: Optional[str] = None
    parsed_at: Optional[datetime] = None
    status: Optional[str] = None
    records_added: int = 0
    error: Optional[str] = None

    extra: dict[str, Any] = Field(default_factory=dict)


class RawProviderFile(BaseModel):
    """
    Полный raw JSON одного провайдера.

    Именно этот объект должен быть входом для новой нормализации.
    """

    provider: RawProviderMeta
    collected_at: Optional[datetime] = None

    source_pages: list[RawSourcePage] = Field(default_factory=list)
    service_candidates: list[RawServiceCandidate] = Field(default_factory=list)

    pricing_sources: list[RawSourcePage] = Field(default_factory=list)
    pricing_items_raw: list[RawPricingItem] = Field(default_factory=list)

    docs_sources: list[RawSourcePage] = Field(default_factory=list)
    api_sources: list[RawSourcePage] = Field(default_factory=list)
    security_sources: list[RawSourcePage] = Field(default_factory=list)

    compliance_evidence_raw: list[RawEvidenceItem] = Field(default_factory=list)
    region_evidence_raw: list[RawEvidenceItem] = Field(default_factory=list)

    parse_log: list[RawParseLogRecord] = Field(default_factory=list)


# =============================================================================
# NORMALIZED SCHEMAS
# =============================================================================
# Эти схемы соответствуют итоговым JSON-файлам:
# providers.json
# services.json
# service_pricing_items.json
# user_task_templates.json
# parse_log.json
# errors.json
# =============================================================================


class Provider(BaseModel):
    """
    Нормализованная запись провайдера для providers.json.
    """

    provider_id: str
    name: str
    base_platform: Optional[str] = None

    is_152fz_compliant: bool = False
    regions: list[str] = Field(default_factory=list)

    api_docs_url: Optional[str] = None
    pricing_url: Optional[str] = None
    source_url: Optional[str] = None

    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Service(BaseModel):
    """
    Нормализованная облачная услуга для services.json.

    Важно: здесь хранится именно услуга, а не отдельная тарифная строка.
    """

    service_id: str
    provider_id: str

    name: str
    category: str
    description: str

    tech_stack_tags: list[str] = Field(default_factory=list)
    use_case_tags: list[str] = Field(default_factory=list)
    compliance_tags: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)

    pricing_model: Optional[str] = None
    price_from_rub: Optional[float] = None
    price_unit: Optional[str] = None
    support_level: Optional[str] = None

    service_url: Optional[str] = None
    source_url: Optional[str] = None

    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_synthetic: bool = False

    # Технические поля для отладки нормализации.
    # Их можно оставить в MVP, чтобы понимать откуда взялась цена.
    price_source: Optional[str] = None  # structured | llm | regex | none
    price_confidence: Optional[float] = None
    price_evidence: Optional[str] = None


class ServicePricingItem(BaseModel):
    """
    Нормализованная тарифная позиция для service_pricing_items.json.

    Примеры: vCPU, RAM, SSD, S3 GB, traffic, request, backup storage.
    """

    pricing_item_id: str
    service_id: str
    provider_id: str

    item_name: str
    item_type: str  # cpu, ram, disk, storage, traffic, request, gpu, backup, other

    price_rub: Optional[float] = None
    price_unit: Optional[str] = None
    billing_period: Optional[str] = None  # hour, month, year, request, one_time

    region: Optional[str] = None
    configuration_tags: list[str] = Field(default_factory=list)

    source_url: Optional[str] = None
    raw_text: Optional[str] = None

    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_synthetic: bool = False

    # Технические поля для контроля качества.
    price_source: Optional[str] = None  # structured | llm | regex | none
    price_confidence: Optional[float] = None
    price_evidence: Optional[str] = None


class UserTaskTemplate(BaseModel):
    """
    Тестовая пользовательская задача для проверки рекомендательной логики.
    """

    id: int
    task_category: str
    tech_stack: list[str] = Field(default_factory=list)
    use_case_tags: list[str] = Field(default_factory=list)
    budget_range_rub: Optional[str] = None
    compliance_required: bool = False
    region: Optional[str] = None
    created_for_testing: bool = True


class ParseLogRecord(BaseModel):
    """
    Нормализованный лог обработки URL для parse_log.json.
    """

    provider_id: str
    url: str
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str
    records_added: int = 0
    error: Optional[str] = None
    normalization_step: Optional[str] = None


class NormalizationError(BaseModel):
    """
    Ошибка нормализации для errors.json.
    """

    item_type: str  # provider | service | pricing_item | parse_log
    provider_id: Optional[str] = None
    source_url: Optional[str] = None
    error: str
    raw_item: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
