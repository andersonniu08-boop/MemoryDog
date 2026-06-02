"""Configuration management for MemoryDog."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".memorydog"
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG = """\
# MemoryDog configuration
[provider]
# anthropic | openai | gemini | deepseek | openrouter | ollama
provider = "anthropic"
model = "claude-sonnet-4-20250514"
api_key = ""

[embedding]
provider = "openai"
model = "text-embedding-3-small"
api_key = ""

[database]
url = "postgresql+asyncpg://memorydog:memorydog@localhost:5432/memorydog"
"""


@dataclass
class ProviderConfig:
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    api_base: str = ""


@dataclass
class EmbeddingConfig:
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    api_key: str = ""


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

    provider.api_key = provider.api_key or os.environ.get(
        f"{provider.provider.upper()}_API_KEY", ""
    )
    embedding.api_key = embedding.api_key or os.environ.get(
        f"{embedding.provider.upper()}_API_KEY", ""
    )

    return Config(provider=provider, embedding=embedding, database=database)


def save_config(config: Config) -> None:
    content = f"""\
# MemoryDog configuration
[provider]
provider = "{config.provider.provider}"
model = "{config.provider.model}"
api_key = "{config.provider.api_key}"

[embedding]
provider = "{config.embedding.provider}"
model = "{config.embedding.model}"
api_key = "{config.embedding.api_key}"

[database]
url = "{config.database.url}"
"""
    ensure_config_dir()
    CONFIG_PATH.write_text(content)
