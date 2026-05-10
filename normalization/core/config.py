from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Корень всего проекта IT-1 Case (на два уровня выше config.py: core/ -> normalization/ -> IT-1 Case/).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# .env ищем сначала в корне проекта, потом в normalization/, потом рядом с config.py.
# Pydantic-settings возьмет первый существующий файл из кортежа.
ENV_FILES = (
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / "normalization" / ".env",
    Path(__file__).resolve().parent / ".env",
)


class Settings(BaseSettings):
    llm_api_key: str
    llm_base_url: str = "https://llm.api.cloud.yandex.net/v1"
    llm_model: str = "deepseek-v3"

    model_config = SettingsConfigDict(
        env_file=tuple(str(path) for path in ENV_FILES),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
