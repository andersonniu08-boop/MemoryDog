"""Configuration management for MemoryDog."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".memorydog"
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG = """\
# MemoryDog configuration
# provider_type: "ollama" (local) or "litellm" (cloud API)
[provider]
provider_type = "ollama"
model = "phi4-mini"
api_key = ""
# api_base = "https://custom-api.example.com"  # optional

[embedding]
model = "nomic-embed-text"

[database]
url = "postgresql+asyncpg://memorydog:memorydog@localhost:5432/memorydog"
"""


@dataclass
class ProviderConfig:
    provider_type: str = "ollama"
    model: str = "phi4-mini"
    api_key: str = ""
    api_base: str = ""


@dataclass
class EmbeddingConfig:
    model: str = "nomic-embed-text"


@dataclass
class DatabaseConfig:
    url: str = "postgresql+asyncpg://memorydog:memorydog@localhost:5432/memorydog"


@dataclass
class Config:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def create_default_config() -> Config:
    ensure_config_dir()
    CONFIG_PATH.write_text(DEFAULT_CONFIG)
    return load_config()


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        return create_default_config()

    data = tomllib.loads(CONFIG_PATH.read_text())

    provider = ProviderConfig(**data.get("provider", {}))
    embedding = EmbeddingConfig(**data.get("embedding", {}))
    database = DatabaseConfig(**data.get("database", {}))

    provider.api_key = provider.api_key or os.environ.get("MEMORYDOG_API_KEY", "")

    return Config(provider=provider, embedding=embedding, database=database)


def save_config(config: Config) -> None:
    base_line = (
        f'api_base = "{config.provider.api_base}"'
        if config.provider.api_base
        else '# api_base = "https://custom-api.example.com"  # optional'
    )
    content = f"""\
# MemoryDog configuration
# Use LiteLLM model format: provider/model
[provider]
provider_type = "{config.provider.provider_type}"
model = "{config.provider.model}"
api_key = "{config.provider.api_key}"
{base_line}

[embedding]
model = "{config.embedding.model}"

[database]
url = "{config.database.url}"
"""
    ensure_config_dir()
    CONFIG_PATH.write_text(content)
