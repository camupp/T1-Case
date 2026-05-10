from __future__ import annotations

import re
from typing import Any, Optional


# =============================================================================
# price_normalizer.py
# -----------------------------------------------------------------------------
# Модуль отвечает только за строгую нормализацию цен кодом.
# LLM может помочь найти цену в грязном тексте, но пересчет и финальная единица
# измерения должны делаться здесь, а не внутри LLM.
# =============================================================================


# Во многих облачных тарифах месячный расчет для hourly-тарифов делают как 720 ч.
HOURS_IN_MONTH = 24 * 30
MINUTES_IN_MONTH = HOURS_IN_MONTH * 60
DAYS_IN_MONTH = 30
MONTHS_IN_YEAR = 12


ITEM_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("cpu", [r"\bvcpu\b", r"\bcpu\b", r"процессор", r"ядр", r"core"]),
    ("ram", [r"\bram\b", r"оперативн", r"памят", r"memory", r"\bгб ram\b"]),
    ("disk", [r"диск", r"\bssd\b", r"\bhdd\b", r"\bnvme\b", r"volume", r"блочн"]),
    ("storage", [r"хранилищ", r"storage", r"\bs3\b", r"объектн", r"бакет", r"bucket"]),
    ("traffic", [r"трафик", r"traffic", r"исходящ", r"входящ", r"egress", r"ingress"]),
    ("ip", [r"\bip[\s\-]?адрес", r"ip address", r"\bip\b"]),
    ("backup", [r"резервн", r"backup", r"бэкап", r"veeam", r"киберпротект", r"kiber"]),
    ("license", [r"лиценз", r"license"]),
    ("request", [r"запрос", r"request", r"api calls?", r"operation"]),
    ("gpu", [r"\bgpu\b", r"видеокарт", r"graphics?", r"accelerator"]),
    ("network", [r"балансировщ", r"load balancer", r"\bvpn\b", r"маршрут", r"router", r"dns", r"cdn"]),
]


BILLING_PERIOD_PATTERNS: list[tuple[str, list[str]]] = [
    ("year", [r"год", r"year", r"annual", r"/\s*год", r"в\s+год"]),
    ("month", [r"месяц", r"мес", r"month", r"monthly", r"/\s*мес", r"в\s+месяц"]),
    ("day", [r"сутк", r"день", r"day", r"daily", r"/\s*день", r"/\s*сут"]),
    ("hour", [r"час", r"hour", r"hourly", r"/\s*час", r"в\s+час"]),
    ("minute", [r"минут", r"minute", r"/\s*мин"]),
    ("request", [r"запрос", r"request", r"operation", r"api call"]),
    ("one_time", [r"единоврем", r"разово", r"one[\s\-]?time", r"setup fee"]),
]


RESOURCE_UNIT_PATTERNS: list[tuple[str, list[str]]] = [
    # Важно: "шт", "за устройство", "экземпляр" должны идти раньше ГБ/vCPU.
    ("шт", [
        r"\bшт\b",
        r"штук",
        r"за\s+устройств",
        r"устройств[ао]",
        r"экземпляр",
        r"instance",
        r"инстанс",
    ]),
    ("vCPU", [r"\bvcpu\b", r"\bcpu\b", r"ядр", r"core"]),
    ("GPU", [r"\bgpu\b", r"видеокарт"]),
    ("ГБ", [r"\bgb\b", r"\bгб\b", r"гигабайт", r"gib"]),
    ("ТБ", [r"\btb\b", r"\bтб\b", r"терабайт", r"tib"]),
    ("запрос", [r"запрос", r"request", r"operation", r"api call"]),
    ("объект", [r"объект", r"object"]),
    ("пользователь", [r"пользовател", r"user"]),
    ("инстанс", [r"инстанс", r"instance", r"node", r"нода"]),
]


# Метки для типовых item_type, если resource_unit явно не найден.
DEFAULT_RESOURCE_BY_ITEM_TYPE: dict[str, Optional[str]] = {
    "cpu": "vCPU",
    "ram": "ГБ",
    "disk": "ГБ",
    "storage": "ГБ",
    "traffic": "ГБ",
    "gpu": "GPU",
    "request": "запрос",
    "ip": None,
    # Backup бывает и за устройство, и за ГБ. Поэтому не ставим ГБ по умолчанию.
    "backup": None,
    "license": None,
    "network": None,
    "other": None,
}


PERIOD_RU: dict[str, str] = {
    "year": "год",
    "month": "мес",
    "day": "день",
    "hour": "час",
    "minute": "мин",
    "request": "запрос",
    "one_time": "разово",
}


PRICE_UNIT_LABELS: dict[tuple[str, str], str] = {
    ("cpu", "hour"): "руб/vCPU/час",
    ("cpu", "month"): "руб/vCPU/мес",
    ("ram", "hour"): "руб/ГБ/час",
    ("ram", "month"): "руб/ГБ/мес",
    ("disk", "hour"): "руб/ГБ/час",
    ("disk", "month"): "руб/ГБ/мес",
    ("storage", "hour"): "руб/ГБ/час",
    ("storage", "month"): "руб/ГБ/мес",
    ("traffic", "month"): "руб/ГБ",
    ("traffic", "hour"): "руб/ГБ",
    ("backup", "month"): "руб/ГБ/мес",
    ("backup_device", "month"): "руб/шт/мес",
    ("backup_device", "hour"): "руб/шт/час",
    ("request", "request"): "руб/запрос",
    ("gpu", "hour"): "руб/GPU/час",
    ("gpu", "month"): "руб/GPU/мес",
    ("ip", "hour"): "руб/час",
    ("ip", "month"): "руб/мес",
    ("network", "hour"): "руб/час",
    ("network", "month"): "руб/мес",
    ("license", "month"): "руб/мес",
    ("other", "month"): "руб/мес",
    ("other", "hour"): "руб/час",
}


def _to_text(value: Any) -> str:
    """Безопасно превращает любое значение в строку для regex-поиска."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _parse_number(value: Any) -> Optional[float]:
    """Парсит число из строки/числа: '1 290,50' -> 1290.5."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        number = float(value)
        return round(number, 6) if number >= 0 else None

    text = str(value).strip()
    if not text:
        return None

    # Берем первое похожее число.
    match = re.search(r"\d[\d\s]*(?:[.,]\d+)?", text)
    if not match:
        return None

    raw = match.group(0).replace(" ", "").replace(",", ".")
    try:
        number = float(raw)
    except ValueError:
        return None

    if number < 0:
        return None
    return round(number, 6)


def parse_price(text: Any) -> Optional[float]:
    """
    Извлекает первую вероятную цену из текста.

    Поддерживает примеры:
    - 'от 445 руб/час' -> 445.0
    - '1 290,50 ₽ в месяц' -> 1290.5
    - '0,00412755 руб/мин' -> 0.004128
    - '120000 руб/год' -> 120000.0

    Важно: функция не пересчитывает период, она только достает исходное число.
    """
    raw_text = _to_text(text)
    if not raw_text.strip():
        return None

    # Приоритет: число рядом с валютой.
    currency_patterns = [
        r"(?:от\s*)?(\d[\d\s]*(?:[.,]\d+)?)\s*(?:₽|руб\.?|р\.?|rub\b|rur\b)",
        r"(?:₽|руб\.?|р\.?|rub\b|rur\b)\s*(\d[\d\s]*(?:[.,]\d+)?)",
    ]

    for pattern in currency_patterns:
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            return _parse_number(match.group(1))

    # Если валюты нет, но это явно тарифная строка с периодом/ресурсом.
    has_price_context = bool(
        detect_billing_period(raw_text)
        or detect_resource_unit(raw_text)
        or re.search(r"\bprice\b|цена|стоим", raw_text, flags=re.IGNORECASE)
    )
    if not has_price_context:
        return None

    return _parse_number(raw_text)


def parse_structured_price(raw_item: Any) -> Optional[float]:
    """
    Достает цену из структурированных полей pricing_items_raw.

    Поддерживает разные варианты, которые могут встретиться у провайдеров:
    - price_per_month_raw
    - price_value_raw
    - price_raw как число/строка
    - price_raw как list[dict], где есть price_per_month_raw или value
    """
    if not isinstance(raw_item, dict):
        # Pydantic model или объект с атрибутами.
        raw_item = getattr(raw_item, "model_dump", lambda: raw_item)()

    if not isinstance(raw_item, dict):
        return None

    for key in ("price_per_month_raw", "price_value_raw", "price", "value"):
        value = raw_item.get(key)
        parsed = _parse_number(value)
        if parsed is not None and parsed > 0:
            return parsed

    price_raw = raw_item.get("price_raw")

    if isinstance(price_raw, list):
        for item in price_raw:
            if isinstance(item, dict):
                for key in ("price_per_month_raw", "price_value_raw", "price", "value"):
                    parsed = _parse_number(item.get(key))
                    if parsed is not None and parsed > 0:
                        return parsed
            else:
                parsed = _parse_number(item)
                if parsed is not None and parsed > 0:
                    return parsed

    parsed = _parse_number(price_raw)
    if parsed is not None and parsed > 0:
        return parsed

    return None


def detect_billing_period(text: Any) -> Optional[str]:
    """
    Определяет период тарификации.

    Возвращает одно из:
    year | month | day | hour | minute | request | one_time | None
    """
    raw_text = _to_text(text).lower()
    if not raw_text.strip():
        return None

    for period, patterns in BILLING_PERIOD_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, raw_text, flags=re.IGNORECASE):
                return period

    return None


def has_device_unit(text: Any) -> bool:
    """
    True, если тариф явно указан за штуку/устройство/экземпляр.

    Примеры:
    - "1 шт"
    - "за устройство"
    - "за экземпляр"
    - "ВМ (за устройство)"
    """
    raw_text = _to_text(text).lower().replace("ё", "е")
    patterns = [
        r"\bшт\b",
        r"\d+\s*шт\b",
        r"за\s+устройств",
        r"устройств[ао]",
        r"за\s+экземпляр",
        r"экземпляр",
        r"instance",
        r"инстанс",
    ]
    return any(re.search(pattern, raw_text, flags=re.IGNORECASE) for pattern in patterns)


def has_storage_unit(text: Any) -> bool:
    """
    True, если тариф явно указан за объем хранения/трафика.
    """
    raw_text = _to_text(text).lower().replace("ё", "е")
    patterns = [
        r"\bгб\b",
        r"\bgb\b",
        r"\bтб\b",
        r"\btb\b",
        r"гигабайт",
        r"терабайт",
        r"хранилищ",
        r"пространство\s+для\s+хранения",
        r"storage",
        r"repository",
        r"репозитор",
    ]
    return any(re.search(pattern, raw_text, flags=re.IGNORECASE) for pattern in patterns)


def detect_item_type(text: Any, item_name: Any = None) -> str:
    """
    Определяет тип тарифной позиции.

    Возвращает:
    cpu | ram | disk | storage | traffic | ip | backup | backup_device |
    license | request | gpu | network | other

    item_name имеет приоритет, потому что в raw_text часто рядом лежат чужие строки
    из PDF-таблицы.
    """
    combined_text = f"{_to_text(item_name)} {_to_text(text)}".lower().replace("ё", "е")
    name_text = _to_text(item_name).lower().replace("ё", "е")

    if not combined_text.strip():
        return "other"

    # 1. Backup за устройство/экземпляр — отдельный тип.
    if (
        re.search(r"backup|бэкап|резервн|veeam|кибер", combined_text, flags=re.IGNORECASE)
        and has_device_unit(combined_text)
    ):
        return "backup_device"

    # 2. Backup storage — оставляем backup, но unit потом должен быть ГБ.
    if (
        re.search(r"backup|бэкап|резервн|veeam|кибер", combined_text, flags=re.IGNORECASE)
        and has_storage_unit(combined_text)
    ):
        return "backup"

    # 3. Если явно штуки/устройства, но это не backup — other с unit шт.
    if has_device_unit(name_text or combined_text):
        return "other"

    # 4. RAM должна идти раньше CPU из-за фраз типа "vRAM".
    if re.search(r"\bvram\b|\bram\b|оперативн|памят|memory", combined_text, flags=re.IGNORECASE):
        return "ram"

    for item_type, patterns in ITEM_TYPE_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, combined_text, flags=re.IGNORECASE):
                return item_type

    return "other"


def detect_resource_unit(
    text: Any,
    item_type: Optional[str] = None,
    item_name: Any = None,
) -> Optional[str]:
    """
    Определяет ресурсную единицу: шт, vCPU, ГБ, GPU, запрос и т.д.

    Приоритет:
    1. item_name/text с "шт/за устройство/экземпляр"
    2. явные единицы в item_name + text
    3. дефолт по item_type
    """
    combined_text = f"{_to_text(item_name)} {_to_text(text)}".lower().replace("ё", "е")

    if has_device_unit(combined_text):
        return "шт"

    for unit, patterns in RESOURCE_UNIT_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, combined_text, flags=re.IGNORECASE):
                return unit

    if item_type == "backup_device":
        return "шт"

    if item_type:
        return DEFAULT_RESOURCE_BY_ITEM_TYPE.get(item_type)

    return None


def normalize_price_to_month(
    price_rub: Optional[float],
    billing_period: Optional[str],
) -> Optional[float]:
    """
    Приводит цену к месячной цене там, где это имеет смысл.

    - year -> / 12
    - month -> без изменений
    - day -> * 30
    - hour -> * 720
    - minute -> * 43200
    - request / one_time -> не пересчитываем, возвращаем как есть

    Пример:
    120000 руб/год -> 10000 руб/мес
    """
    if price_rub is None:
        return None

    try:
        price = float(price_rub)
    except (TypeError, ValueError):
        return None

    if price < 0:
        return None

    period = billing_period or "month"

    if period == "year":
        return round(price / MONTHS_IN_YEAR, 6)
    if period == "month":
        return round(price, 6)
    if period == "day":
        return round(price * DAYS_IN_MONTH, 6)
    if period == "hour":
        return round(price * HOURS_IN_MONTH, 6)
    if period == "minute":
        return round(price * MINUTES_IN_MONTH, 6)
    if period in {"request", "one_time"}:
        return round(price, 6)

    return round(price, 6)


def normalize_billing_period_for_month(billing_period: Optional[str]) -> Optional[str]:
    """
    Возвращает период после месячной нормализации.

    year/day/hour/minute становятся month, потому что цена пересчитана на месяц.
    request и one_time остаются как есть.
    """
    if billing_period in {"year", "day", "hour", "minute"}:
        return "month"
    return billing_period or "month"


def build_price_unit(
    item_type: str = "other",
    billing_period: Optional[str] = "month",
    resource_unit: Optional[str] = None,
    normalized_to_month: bool = False,
) -> str:
    """
    Формирует человекочитаемую единицу цены.

    Главное правило:
    - если unit = "шт" или item_type = backup_device, то руб/шт/мес;
    - если backup storage явно в ГБ, то руб/ГБ/мес.
    """
    item = item_type or "other"
    period = billing_period or "month"

    if normalized_to_month:
        period = normalize_billing_period_for_month(period) or "month"

    unit = resource_unit or DEFAULT_RESOURCE_BY_ITEM_TYPE.get(item)
    period_ru = PERIOD_RU.get(period, period)

    # Высший приоритет: тарификация за штуку/устройство/экземпляр.
    if unit == "шт" or item == "backup_device":
        if period == "one_time":
            return "руб/шт/разово"
        return f"руб/шт/{period_ru}"

    if period == "request":
        return "руб/запрос"

    if period == "one_time":
        if unit:
            return f"руб/{unit}/разово"
        return "руб/разово"

    # Backup без явной единицы не превращаем автоматически в ГБ.
    # Если это backup storage, unit уже будет ГБ.
    if item == "backup" and not unit:
        return f"руб/{period_ru}"

    label = PRICE_UNIT_LABELS.get((item, period))
    if label:
        return label

    if unit:
        return f"руб/{unit}/{period_ru}"

    return f"руб/{period_ru}"


def normalize_price_record(
    *,
    price_rub: Optional[float],
    text: Any = "",
    billing_period: Optional[str] = None,
    item_type: Optional[str] = None,
    resource_unit: Optional[str] = None,
    item_name: Any = None,
    to_month: bool = True,
) -> dict[str, Any]:
    """
    Удобная функция для normalizer.py.

    На вход можно дать цену и сырой текст. Функция вернет нормализованный словарь:
    - price_rub
    - price_unit
    - billing_period
    - item_type
    - resource_unit

    Если to_month=True, цена за год/день/час/минуту пересчитывается в месяц.
    """
    source_text = _to_text(text)
    combined_text = f"{_to_text(item_name)} {source_text}"

    detected_item_type = item_type or detect_item_type(source_text, item_name=item_name)
    detected_period = billing_period or detect_billing_period(combined_text) or "month"
    detected_resource_unit = resource_unit or detect_resource_unit(
        source_text,
        detected_item_type,
        item_name=item_name,
    )

    if to_month:
        normalized_price = normalize_price_to_month(price_rub, detected_period)
        normalized_period = normalize_billing_period_for_month(detected_period)
        price_unit = build_price_unit(
            item_type=detected_item_type,
            billing_period=detected_period,
            resource_unit=detected_resource_unit,
            normalized_to_month=True,
        )
    else:
        normalized_price = price_rub
        normalized_period = detected_period
        price_unit = build_price_unit(
            item_type=detected_item_type,
            billing_period=detected_period,
            resource_unit=detected_resource_unit,
            normalized_to_month=False,
        )

    return {
        "price_rub": normalized_price,
        "price_unit": price_unit,
        "billing_period": normalized_period,
        "item_type": detected_item_type,
        "resource_unit": detected_resource_unit,
    }
