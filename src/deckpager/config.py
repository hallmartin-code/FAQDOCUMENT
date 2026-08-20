"""Runtime configuration.

Values resolve highest-precedence first: CLI override -> environment -> .env ->
`config/default.toml` -> the field's own default. The model ID and provider name are never
hard-coded at a call site; they always come from here.

`config/default.toml` is nested for readability while `Settings` stays flat, so an
environment override is always just `DECKPAGER_<FIELD>`. `_TOML_KEYS` is the explicit
bridge between the two, which also means a mistyped key in the TOML is reported rather
than silently ignored.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from deckpager.errors import ConfigError
from deckpager.paths import config_dir

Effort = Literal["low", "medium", "high", "xhigh", "max"]
ProviderName = Literal["anthropic", "openai", "ollama", "fake"]

#: Shipped fallbacks used only if `config/default.toml` is unreadable.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_PROVIDER: ProviderName = "anthropic"

#: TOML table path -> Settings field name.
_TOML_KEYS: dict[tuple[str, ...], str] = {
    ("provider", "name"): "provider",
    ("model", "name"): "model",
    ("model", "max_tokens"): "max_tokens",
    ("model", "effort"): "effort",
    ("ingest", "max_slides"): "max_slides",
    ("ingest", "max_image_bytes"): "max_image_bytes",
    ("ingest", "no_images"): "no_images",
}


def _flatten_toml(data: dict[str, Any], source: Path) -> dict[str, Any]:
    """Map the nested config file onto flat Settings fields, rejecting unknown keys."""
    known = {path[0] for path in _TOML_KEYS}
    flat: dict[str, Any] = {}
    for table, contents in data.items():
        if table not in known:
            raise ConfigError(
                f"{source} has an unknown section [{table}].\n"
                f"Known sections: {', '.join(sorted(known))}."
            )
        if not isinstance(contents, dict):
            raise ConfigError(
                f"{source}: [{table}] must be a table, not {type(contents).__name__}."
            )
        for key, value in contents.items():
            field = _TOML_KEYS.get((table, key))
            if field is None:
                valid = sorted(k for t, k in _TOML_KEYS if t == table)
                raise ConfigError(
                    f"{source} has an unknown key `{key}` in [{table}].\n"
                    f"Valid keys for [{table}]: {', '.join(valid)}."
                )
            flat[field] = value
    return flat


class TomlDefaultsSource(PydanticBaseSettingsSource):
    """Feeds `config/default.toml` in as the lowest-precedence layer."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: D102
        raise NotImplementedError  # pragma: no cover - __call__ supplies everything at once

    def __call__(self) -> dict[str, Any]:
        """Read and flatten the file, treating its absence as 'no opinion'."""
        path = config_dir() / "default.toml"
        if not path.is_file():
            return {}
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Could not read {path}: {exc}") from exc
        return _flatten_toml(data, path)


class Settings(BaseSettings):
    """Configuration bound from the environment, `.env`, and `config/default.toml`."""

    model_config = SettingsConfigDict(
        env_prefix="DECKPAGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: ProviderName = Field(
        default=DEFAULT_PROVIDER, description="Which LLM backend to use."
    )
    model: str = Field(default=DEFAULT_MODEL, description="Model ID for the chosen provider.")
    max_tokens: int = Field(default=32000, ge=1024, description="Max output tokens.")
    effort: Effort = Field(default="high", description="Reasoning effort level.")
    max_slides: int = Field(default=60, ge=1, description="Slide cap per request.")
    max_image_bytes: int = Field(
        default=5_000_000, ge=0, description="Total raw image byte cap per request."
    )
    no_images: bool = Field(default=False, description="Skip slide rasterization.")

    # Read from the un-prefixed vendor variables, not DECKPAGER_ANTHROPIC_API_KEY, so the
    # tool picks up the same keys the vendor SDKs and other tooling already use.
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Highest precedence first; the TOML defaults sit below the environment."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlDefaultsSource(settings_cls),
            file_secret_settings,
        )

    def require_api_key(self, provider: str | None = None) -> str:
        """Return the key for `provider`, or explain exactly which variable to set."""
        name = provider or self.provider
        keys: dict[str, tuple[str | None, str]] = {
            "anthropic": (self.anthropic_api_key, "ANTHROPIC_API_KEY=sk-ant-..."),
            "openai": (self.openai_api_key, "OPENAI_API_KEY=sk-..."),
        }
        if name not in keys:
            raise ConfigError(f"Provider {name!r} does not use an API key.")
        value, example = keys[name]
        if not value:
            variable = example.split("=", 1)[0]
            raise ConfigError(
                f"{variable} is not set, and the {name} provider needs it.\n"
                f"Set it in your environment or in a .env file next to your deck:\n"
                f"    {example}\n"
                f"See .env.example for the full list of supported variables, or run with "
                f"--provider fake to work offline."
            )
        return value


def load_settings(
    *,
    provider: str | None = None,
    model: str | None = None,
    no_images: bool | None = None,
) -> Settings:
    """Build Settings from the configured sources, applying CLI overrides on top."""
    settings = Settings()
    overrides: dict[str, object] = {}
    if provider is not None:
        if provider not in get_args(ProviderName):
            valid = ", ".join(get_args(ProviderName))
            raise ConfigError(
                f"Unknown provider {provider!r}.\n"
                f"Choose one of: {valid}.\n"
                f"Run `deckpager providers` to see which are configured on this machine."
            )
        overrides["provider"] = provider
    if model is not None:
        overrides["model"] = model
    if no_images is not None:
        overrides["no_images"] = no_images
    if not overrides:
        return settings
    # Re-validate rather than model_copy: an unknown --provider must fail here, with the
    # valid names in the message, not later at the registry lookup.
    try:
        return Settings(**{**settings.model_dump(), **overrides})
    except ValueError as exc:
        raise ConfigError(f"Invalid option: {exc}") from exc


@lru_cache(maxsize=1)
def load_weights() -> dict[str, float]:
    """Read `config/weights.toml`, validated against the scorecard it has to cover.

    Returns the ten input-category weights. "Overall Investability" is the computed
    output and carries no weight of its own.
    """
    from deckpager.analysis.schema import SCORECARD_ORDER

    path = config_dir() / "weights.toml"
    expected = set(SCORECARD_ORDER) - {"Overall Investability"}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(
            f"Could not read {path}: {exc}\n"
            f"It ships with deckpager; restore it from version control if it was moved."
        ) from exc

    table = data.get("weights")
    if not isinstance(table, dict):
        raise ConfigError(f"{path} must contain a [weights] table.")

    missing = sorted(expected - set(table))
    unexpected = sorted(set(table) - expected)
    if missing or unexpected:
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if unexpected:
            detail.append(f"unexpected {unexpected}")
        raise ConfigError(
            f"{path} must weight exactly the ten scored categories ({'; '.join(detail)}).\n"
            f"'Overall Investability' is computed from the others and must not be listed."
        )

    weights: dict[str, float] = {}
    for name, value in table.items():
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ConfigError(f"{path}: weight for {name!r} must be a number, got {value!r}.")
        if value < 0:
            raise ConfigError(f"{path}: weight for {name!r} is negative ({value}).")
        weights[name] = float(value)

    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(
            f"{path}: the ten weights must sum to 1.0, but they sum to {total:.6f}.\n"
            f"Adjust them so the weighted overall stays on the same 1-10 scale as the "
            f"categories it is built from."
        )
    return weights
