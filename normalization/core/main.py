from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query

from core.llm_client import ask_llm
from core.normalizer import normalize_many_provider_files, normalize_provider_file
from core.schemas import RawProviderFile


app = FastAPI(
    title="Cloud Marketplace Normalization API",
    description=(
        "API для нормализации raw JSON облачных провайдеров. "
        "LLM нормализует смысл услуги и выбирает цену из коротких кандидатов, "
        "код собирает кандидатов и нормализует тарифные позиции."
    ),
    version="0.2.0",
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/test-llm")
def test_llm() -> dict[str, str]:
    """
    Проверка соединения с LLM.
    """
    try:
        result = ask_llm(
            system_prompt="Ты тестовый модуль. Ответь строго одним словом: ok",
            user_prompt="Проверка соединения.",
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"LLM connection failed: {error}",
        ) from error

    return {"llm_response": result}


@app.post("/normalize-provider-file")
def normalize_provider_file_endpoint(
    raw_file: RawProviderFile,
    dry_run: bool = Query(
        default=False,
        description="Если true, LLM не вызывается, используются deterministic fallback values.",
    ),
    llm_price_selection: bool = Query(
        default=True,
        description="Если true, LLM выбирает цену услуги из коротких кандидатов.",
    ),
) -> dict[str, Any]:
    """
    Нормализует один полный raw JSON провайдера.

    На вход:
      {
        "provider": ...,
        "collected_at": ...,
        "source_pages": ...,
        "service_candidates": ...,
        "pricing_items_raw": ...,
        "compliance_evidence_raw": ...,
        "region_evidence_raw": ...,
        "parse_log": ...
      }

    На выход:
      provider
      services
      service_pricing_items
      parse_log
      errors
    """
    try:
        provider, services, pricing_items, parse_log, errors = normalize_provider_file(
            raw_file,
            dry_run=dry_run,
            use_llm_price_selection=llm_price_selection,
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Provider file normalization failed: {error}",
        ) from error

    return {
        "provider": provider.model_dump(mode="json"),
        "services": [item.model_dump(mode="json") for item in services],
        "service_pricing_items": [item.model_dump(mode="json") for item in pricing_items],
        "parse_log": [item.model_dump(mode="json") for item in parse_log],
        "errors": [item.model_dump(mode="json") for item in errors],
    }


@app.post("/normalize-provider-files")
def normalize_provider_files_endpoint(
    raw_files: list[RawProviderFile],
    dry_run: bool = Query(
        default=False,
        description="Если true, LLM не вызывается, используются deterministic fallback values.",
    ),
    llm_price_selection: bool = Query(
        default=True,
        description="Если true, LLM выбирает цену услуги из коротких кандидатов.",
    ),
) -> dict[str, list[dict[str, Any]]]:
    """
    Нормализует несколько raw JSON провайдеров сразу.

    На выходе структура уже совпадает с файлами:
      providers.json
      services.json
      service_pricing_items.json
      parse_log.json
      errors.json
    """
    try:
        result = normalize_many_provider_files(
            raw_files,
            dry_run=dry_run,
            use_llm_price_selection=llm_price_selection,
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Provider files normalization failed: {error}",
        ) from error

    return result


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "name": "Cloud Marketplace Normalization API",
        "version": "0.2.0",
        "endpoints": [
            "GET /health",
            "GET /test-llm",
            "POST /normalize-provider-file",
            "POST /normalize-provider-files",
        ],
    }
