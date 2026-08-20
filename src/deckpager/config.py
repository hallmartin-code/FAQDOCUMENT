"""Runtime configuration, sourced from the environment and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field as PydanticField
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

RenderEngine = Literal["weasyprint", "reportlab"]

#: Default extraction model. Overridable per-run with `--model` or DECKPAGER_MODEL.
DEFAULT_MODEL = "claude-sonnet-4-6"

#: Cost per 1M tokens, USD, keyed by model id. Used for the cost line on success.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # model id: (input, output)
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


class Settings(BaseSettings):
    """Process configuration. Every field is overridable via `DECKPAGER_*` env vars."""

    model_config = SettingsConfigDict(
        env_prefix="DECKPAGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = PydanticField(
        default=None, validation_alias="ANTHROPIC_API_KEY"
    )
    model: str = DEFAULT_MODEL
    min_confidence: float = 0.6
    engine: RenderEngine = "weasyprint"
    cache_dir: Path = Path(".deckpager-cache")
    soffice_path: Path | None = None
    max_slides: int = 40
    max_image_slides: int = 25

    def has_api_key(self) -> bool:
        """True when an API key is present and non-empty. Never log the key itself."""
        key = self.anthropic_api_key
        return key is not None and bool(key.get_secret_value().strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once."""
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached settings. Used by tests that patch the environment."""
    get_settings.cache_clear()
