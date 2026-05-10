from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from core.normalizer import normalize_many_provider_files


# =============================================================================
# normalize_file.py
# -----------------------------------------------------------------------------
# Основной запуск пакетной нормализации.
#
# На вход:
#   data/raw/t1_cloud_raw.json
#   data/raw/selectel_raw.json
#   data/raw/cloud_ru_raw.json
#   data/raw/vk_cloud_raw.json
#
# На выход:
#   data/normalized/providers.json
#   data/normalized/services.json
#   data/normalized/service_pricing_items.json
#   data/normalized/user_task_templates.json
#   data/normalized/parse_log.json
#   data/normalized/errors.json
# =============================================================================


# Скрипт лежит в normalization/, а data/ — в корне проекта IT-1 Case/.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
NORMALIZED_DIR = PROJECT_ROOT / "data" / "normalized"

RAW_FILES = {
    "t1-cloud": "t1_cloud_raw.json",
    "selectel": "selectel_raw.json",
    "cloud-ru": "cloud_ru_raw.json",
    "vk-cloud": "vk_cloud_raw.json",
}

OUTPUT_FILES = {
    "providers": "providers.json",
    "services": "services.json",
    "service_pricing_items": "service_pricing_items.json",
    "user_task_templates": "user_task_templates.json",
    "parse_log": "parse_log.json",
    "errors": "errors.json",
}


USER_TASK_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": 1,
        "task_category": "web-hosting",
        "tech_stack": ["Python", "PostgreSQL", "Docker", "Nginx"],
        "use_case_tags": ["web-hosting", "devops"],
        "budget_range_rub": "10000-50000",
        "compliance_required": True,
        "region": "Russia",
        "created_for_testing": True,
    },
    {
        "id": 2,
        "task_category": "backup",
        "tech_stack": ["S3", "Linux"],
        "use_case_tags": ["backup", "storage"],
        "budget_range_rub": "1000-10000",
        "compliance_required": True,
        "region": "Russia",
        "created_for_testing": True,
    },
    {
        "id": 3,
        "task_category": "analytics",
        "tech_stack": ["ClickHouse", "Kafka", "Python"],
        "use_case_tags": ["analytics", "data-lake", "streaming"],
        "budget_range_rub": "30000-150000",
        "compliance_required": False,
        "region": "Russia",
        "created_for_testing": True,
    },
    {
        "id": 4,
        "task_category": "ml-training",
        "tech_stack": ["Python", "TensorFlow", "Docker", "Kubernetes"],
        "use_case_tags": ["ml-training", "gpu", "devops"],
        "budget_range_rub": "50000-300000",
        "compliance_required": False,
        "region": "Russia",
        "created_for_testing": True,
    },
    {
        "id": 5,
        "task_category": "kubernetes",
        "tech_stack": ["Kubernetes", "Docker", "Helm"],
        "use_case_tags": ["devops", "container-orchestration", "microservices"],
        "budget_range_rub": "20000-100000",
        "compliance_required": True,
        "region": "Russia",
        "created_for_testing": True,
    },
    {
        "id": 6,
        "task_category": "database",
        "tech_stack": ["PostgreSQL", "Redis"],
        "use_case_tags": ["database", "managed-db", "web-hosting"],
        "budget_range_rub": "5000-30000",
        "compliance_required": True,
        "region": "Russia",
        "created_for_testing": True,
    },
    {
        "id": 7,
        "task_category": "cdn",
        "tech_stack": ["CDN", "S3", "Nginx"],
        "use_case_tags": ["media-storage", "static-hosting", "cdn"],
        "budget_range_rub": "3000-20000",
        "compliance_required": False,
        "region": "Russia",
        "created_for_testing": True,
    },
]


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    records_count = len(data) if isinstance(data, list) else "—"
    size_kb = path.stat().st_size / 1024

    logging.info(
        "Saved %-32s records=%s size=%.1f KB",
        path.name,
        records_count,
        size_kb,
    )


def resolve_provider_files(provider: str | None = None) -> list[tuple[str, Path]]:
    if provider:
        if provider not in RAW_FILES:
            allowed = ", ".join(RAW_FILES.keys())
            raise ValueError(f"Unknown provider: {provider}. Allowed: {allowed}")

        return [(provider, RAW_DIR / RAW_FILES[provider])]

    return [
        (provider_id, RAW_DIR / filename)
        for provider_id, filename in RAW_FILES.items()
    ]


def load_raw_provider_files(provider: str | None = None, skip_missing: bool = False) -> list[dict[str, Any]]:
    raw_files: list[dict[str, Any]] = []

    for provider_id, path in resolve_provider_files(provider):
        if not path.exists():
            message = f"Raw file for {provider_id} not found: {path}"

            if skip_missing:
                logging.warning(message)
                continue

            raise FileNotFoundError(message)

        logging.info("Loading %s from %s", provider_id, path)
        raw_data = read_json(path)
        raw_files.append(raw_data)

    return raw_files


def save_normalized_result(result: dict[str, list[dict[str, Any]]]) -> None:
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)

    write_json(
        NORMALIZED_DIR / OUTPUT_FILES["providers"],
        result.get("providers", []),
    )
    write_json(
        NORMALIZED_DIR / OUTPUT_FILES["services"],
        result.get("services", []),
    )
    write_json(
        NORMALIZED_DIR / OUTPUT_FILES["service_pricing_items"],
        result.get("service_pricing_items", []),
    )
    write_json(
        NORMALIZED_DIR / OUTPUT_FILES["user_task_templates"],
        USER_TASK_TEMPLATES,
    )
    write_json(
        NORMALIZED_DIR / OUTPUT_FILES["parse_log"],
        result.get("parse_log", []),
    )
    write_json(
        NORMALIZED_DIR / OUTPUT_FILES["errors"],
        result.get("errors", []),
    )


def print_summary(result: dict[str, list[dict[str, Any]]]) -> None:
    providers = result.get("providers", [])
    services = result.get("services", [])
    pricing_items = result.get("service_pricing_items", [])
    parse_log = result.get("parse_log", [])
    errors = result.get("errors", [])

    services_with_price = [
        service for service in services
        if service.get("price_from_rub") is not None
    ]

    pricing_with_price = [
        item for item in pricing_items
        if item.get("price_rub") is not None
    ]

    print()
    print("=" * 70)
    print("NORMALIZATION SUMMARY")
    print("=" * 70)
    print(f"Providers:              {len(providers)}")
    print(f"Services:               {len(services)}")
    print(f"Services with price:    {len(services_with_price)}")
    print(f"Pricing items:          {len(pricing_items)}")
    print(f"Pricing items w/ price: {len(pricing_with_price)}")
    print(f"Parse log records:      {len(parse_log)}")
    print(f"Errors:                 {len(errors)}")
    print(f"Output dir:             {NORMALIZED_DIR}")
    print("=" * 70)

    if errors:
        print()
        print("First errors:")
        for error in errors[:5]:
            print(f"- {error.get('item_type')} | {error.get('provider_id')} | {error.get('error')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize raw cloud provider JSON files into normalized JSON files."
    )

    parser.add_argument(
        "--provider",
        choices=list(RAW_FILES.keys()),
        help="Normalize only one provider.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call LLM. Use deterministic fallback values.",
    )
    parser.add_argument(
        "--no-llm-price-selection",
        action="store_true",
        help="Disable LLM-based price selection (services.price_from_rub will be null).",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip missing raw files instead of failing.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logs.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    logging.info("Raw dir:        %s", RAW_DIR)
    logging.info("Normalized dir: %s", NORMALIZED_DIR)

    if args.dry_run:
        logging.info("DRY RUN: LLM calls are disabled.")

    raw_files = load_raw_provider_files(
        provider=args.provider,
        skip_missing=args.skip_missing,
    )

    if not raw_files:
        raise RuntimeError("No raw provider files loaded.")

    result = normalize_many_provider_files(
        raw_files,
        dry_run=args.dry_run,
        use_llm_price_selection=not args.no_llm_price_selection,
    )

    save_normalized_result(result)
    print_summary(result)


if __name__ == "__main__":
    main()
