from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.normalizer import normalize_provider_file


# =============================================================================
# test_normalize_subset.py
# -----------------------------------------------------------------------------
# Быстрый тест нормализации только 3 услуг T1 Cloud:
#   1. Veeam Backup
#   2. Managed Service for PostgreSQL
#   3. Managed Service for Kubernetes
#
# Это нужно, чтобы не ждать полный прогон всего t1_cloud_raw.json.
# =============================================================================


# Скрипт лежит в normalization/, а data/ — в корне проекта IT-1 Case/.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "t1_cloud_raw.json"
OUT_DIR = PROJECT_ROOT / "data" / "normalized_test"

# Ключевые слова для выбора нужных услуг из service_candidates.
# Поиск идет по всему объекту service_candidate, чтобы не зависеть от точного поля.
WANTED_SERVICES = [
    {
        "label": "Veeam Backup",
        "keywords": ["veeam", "резервное копирование(veeam)", "резервное копирование veeam"],
    },
    {
        "label": "Managed Service for PostgreSQL",
        "keywords": ["postgresql", "managed service for postgresql"],
    },
    {
        "label": "Managed Service for Kubernetes",
        "keywords": ["kubernetes", "managed service for kubernetes", "k8s"],
    },
]


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Input raw file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def normalize_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).lower().replace("ё", "е")


def service_matches(candidate: dict[str, Any], keywords: list[str]) -> bool:
    text = normalize_text(candidate)
    return any(keyword.lower().replace("ё", "е") in text for keyword in keywords)


def select_service_candidates(raw_data: dict[str, Any]) -> list[dict[str, Any]]:
    all_candidates = raw_data.get("service_candidates", [])
    selected: list[dict[str, Any]] = []
    selected_labels: list[str] = []

    for wanted in WANTED_SERVICES:
        label = wanted["label"]
        keywords = wanted["keywords"]

        match = None
        for candidate in all_candidates:
            if service_matches(candidate, keywords):
                match = candidate
                break

        if match is None:
            print(f"WARNING: service not found: {label}")
            continue

        selected.append(match)
        selected_labels.append(label)

    print("Selected services:")
    for label in selected_labels:
        print(f"- {label}")

    return selected


def main() -> None:
    print(f"Reading raw file: {RAW_PATH}")
    raw_data = read_json(RAW_PATH)

    selected_candidates = select_service_candidates(raw_data)

    if not selected_candidates:
        raise RuntimeError("No service candidates selected. Check WANTED_SERVICES keywords.")

    # Подменяем service_candidates только на выбранные 3 услуги.
    # pricing_items_raw оставляем полным, чтобы matcher сам выбрал релевантные тарифные строки.
    raw_data["service_candidates"] = selected_candidates

    print()
    print("Starting subset normalization...")
    print(f"Service candidates: {len(raw_data['service_candidates'])}")
    print(f"Pricing items raw:  {len(raw_data.get('pricing_items_raw', []))}")

    provider, services, service_pricing_items, parse_log, errors = normalize_provider_file(
        raw_data,
        dry_run=False,
        use_llm_price_selection=True,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    write_json(OUT_DIR / "providers.json", [provider.model_dump(mode="json")])
    write_json(OUT_DIR / "services.json", [item.model_dump(mode="json") for item in services])
    write_json(
        OUT_DIR / "service_pricing_items.json",
        [item.model_dump(mode="json") for item in service_pricing_items],
    )
    write_json(OUT_DIR / "parse_log.json", [item.model_dump(mode="json") for item in parse_log])
    write_json(OUT_DIR / "errors.json", [item.model_dump(mode="json") for item in errors])

    print()
    print("=" * 70)
    print("SUBSET NORMALIZATION DONE")
    print("=" * 70)
    print(f"Output dir:             {OUT_DIR}")
    print(f"Providers:              1")
    print(f"Services:               {len(services)}")
    print(f"Pricing items:          {len(service_pricing_items)}")
    print(f"Errors:                 {len(errors)}")

    services_with_price = [item for item in services if item.price_from_rub is not None]
    print(f"Services with price:    {len(services_with_price)}")

    print()
    print("Service prices:")
    for service in services:
        print(
            f"- {service.name}: "
            f"price={service.price_from_rub} "
            f"unit={service.price_unit} "
            f"source={service.price_source} "
            f"confidence={service.price_confidence}"
        )

    if errors:
        print()
        print("Errors:")
        for error in errors[:10]:
            print(f"- {error.item_type} | {error.provider_id} | {error.error}")


if __name__ == "__main__":
    main()
