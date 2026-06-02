"""MemoryDog CLI entry point."""
import argparse


def main():
    parser = argparse.ArgumentParser(
        prog="dog", description="MemoryDog \u2014 memory-augmented coding agent"
    )
    sub = parser.add_subparsers(dest="command")

    chat_parser = sub.add_parser("chat", help="Start interactive chat session")
    chat_parser.add_argument("-w", "--workspace", default=".", help="Workspace path")
    chat_parser.add_argument(
        "-m", "--model", help="Override model from config"
    )
    chat_parser.add_argument(
        "--mock", action="store_true", help="Use mock provider (no API needed)"
    )

    sub.add_parser("config", help="Interactive configuration wizard")

    args = parser.parse_args()

    if args.command == "chat":
        from core.config import load_config

        config = load_config()

        if args.mock:
            provider = _make_mock_provider()
            model_name = "mock"
        else:
            provider, model_name = _make_provider_from_config(config, args.model)

        from cli.app import MemoryDogApp

        app = MemoryDogApp(
            workspace=args.workspace, provider=provider, model_name=model_name
        )
        app.run()

    elif args.command == "config":
        _run_config_wizard()
    else:
        parser.print_help()


def _make_mock_provider():
    from core.provider import MockProvider

    return MockProvider()


def _make_provider_from_config(config, model_override=None):
    from core.provider import LiteLLMProvider

    pc = config.provider
    model = model_override or pc.model

    provider = LiteLLMProvider(
        model=model,
        api_key=pc.api_key,
        api_base=pc.api_base or None,
    )
    return provider, model


def _run_config_wizard():
    from core.config import (
        Config,
        load_config,
        save_config,
    )

    try:
        config = load_config()
    except Exception:
        config = Config()

    print("\n\U0001F415 MemoryDog Configuration\n")
    print("Use LiteLLM model format: provider/model")
    print("Examples: anthropic/claude-sonnet-4-20250514, openai/gpt-4o, ollama/llama3\n")
    print("Press Enter to keep current values.\n")

    model = input(f"Model [{config.provider.model}]: ").strip()
    if model:
        config.provider.model = model

    api_key = input(f"API Key [{_mask(config.provider.api_key)}]: ").strip()
    if api_key:
        config.provider.api_key = api_key

    api_base = input(
        f"API Base / custom URL (optional) [{config.provider.api_base or 'none'}]: "
    ).strip()
    if api_base:
        config.provider.api_base = api_base

    embed_model = input(
        f"\nEmbedding model [{config.embedding.model}]: "
    ).strip()
    if embed_model:
        config.embedding.model = embed_model

    save_config(config)
    print("\n\U0001F415 Config saved to ~/.memorydog/config.toml")
    print("Run 'dog chat' to start.")


def _mask(key: str) -> str:
    if not key or len(key) < 8:
        return "(not set)"
    return key[:4] + "..." + key[-4:]


if __name__ == "__main__":
    main()
