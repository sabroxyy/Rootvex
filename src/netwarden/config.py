"""Load settings from config/settings.yaml, then let .env / environment override them.

Precedence (highest first): explicit kwargs > environment (NETWARDEN_*) > .env > settings.yaml.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# Repo root = two levels above src/netwarden. Override with NETWARDEN_HOME.
PROJECT_ROOT = Path(os.environ.get("NETWARDEN_HOME", Path(__file__).resolve().parents[2]))
SETTINGS_FILE = Path(os.environ.get("NETWARDEN_SETTINGS", PROJECT_ROOT / "config/settings.yaml"))


class LLMSettings(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    model: str = "granite4:3b"
    api_key: SecretStr = SecretStr("ollama")  # Ollama ignores it; the client needs a value
    temperature: float = 0.0
    request_timeout_s: int = 300
    # Cap on tokens per model call, so a "thinking" model cannot ramble for minutes.
    max_tokens: int | None = 2048
    # For reasoning models (qwen3.5, gpt-oss): "none", "low", "medium", "high".
    # None = don't send it (the model's default).
    reasoning_effort: str | None = None


class AgentSettings(BaseModel):
    max_steps: int = Field(12, ge=1, le=100)
    timeout_s: int = Field(300, ge=5)
    max_output_chars: int = Field(6000, ge=200)


class LabSettings(BaseModel):
    allowed_networks: list[ipaddress.IPv4Network] = Field(
        default_factory=lambda: [ipaddress.IPv4Network("10.99.0.0/24")]
    )
    inventory_file: Path = Path("config/inventory.local.yaml")
    allowlist_dir: Path = Path("config/allowlist")
    device_backend: Literal["netmiko", "fixtures"] = "netmiko"
    fixtures_dir: Path = Path("tests/fixtures")

    @field_validator("allowed_networks")
    @classmethod
    def _not_empty(cls, v: list[ipaddress.IPv4Network]) -> list[ipaddress.IPv4Network]:
        if not v:
            raise ValueError("allowed_networks must list at least one lab CIDR")
        return v


class LoggingSettings(BaseModel):
    dir: Path = Path("logs")
    level: str = "INFO"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NETWARDEN_",
        env_nested_delimiter="__",
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    lab: LabSettings = Field(default_factory=LabSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=SETTINGS_FILE)
        return (init_settings, env_settings, dotenv_settings, yaml_source)

    def resolve(self, path: Path) -> Path:
        """Turn a config-relative path into an absolute one under the project root."""
        return path if path.is_absolute() else PROJECT_ROOT / path


def load_settings(**overrides) -> Settings:
    return Settings(**overrides)
