"""MemoryDog CLI entry point."""

import argparse


def main():
    parser = argparse.ArgumentParser(
        prog="dog", description="MemoryDog \u2014 memory-augmented coding agent"
    )
    sub = parser.add_subparsers(dest="command")

    chat_parser = sub.add_parser("chat", help="Start interactive chat session")
    chat_parser.add_argument("-w", "--workspace", default=".", help="Workspace path")
    chat_parser.add_argument("-m", "--model", help="Override model from config")
    chat_parser.add_argument(
        "--mock", action="store_true", help="Use mock provider (no API needed)"
    )

    sub.add_parser("config", help="Interactive configuration wizard")

    sub.add_parser("status", help="Show MemoryDog status")

    instinct_parser = sub.add_parser("instinct", help="Manage instincts")
    instinct_sub = instinct_parser.add_subparsers(dest="instinct_cmd")
    instinct_sub.add_parser("list", help="List all instincts")
    show_parser = instinct_sub.add_parser("show", help="Show instinct details")
    show_parser.add_argument("name", help="Instinct name")
    edit_parser = instinct_sub.add_parser("edit", help="Open instincts file in editor")
    edit_parser.add_argument("--editor", help="Editor command (default: $EDITOR or nano)")

    sub.add_parser("install", help="Install dog to ~/.local/bin")

    args = parser.parse_args()

    if args.command == "chat":
        from core.config import load_config

        config = load_config()

        if args.mock:
            provider = _make_mock_provider()
            model_name = "mock"
            print("🐕 Starting in offline mode (--mock). No API key required.")
        else:
            provider, model_name = _make_provider_from_config(config, args.model)
            result = provider.check_connection()
            if result is not None:
                print(f"  \u26a0 API key rejected. Starting in offline mode instead.\n  {result}\n")
                provider = _make_mock_provider()
                model_name = "mock"

        from cli.app import MemoryDogApp

        app = MemoryDogApp(workspace=args.workspace, provider=provider, model_name=model_name)
        app.run()

    elif args.command == "config":
        _run_config_wizard()

    elif args.command == "status":
        _run_status()

    elif args.command == "instinct":
        _run_instinct_cmd(args)

    elif args.command == "install":
        _run_install()

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

    print("\n\U0001f415 MemoryDog Configuration\n")
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

    save_config(config)
    print("\n\U0001f415 Config saved to ~/.memorydog/config.toml")
    print("Embeddings: Ollama + nomic-embed-text (local)")
    print("Run 'dog chat' to start.")
    print("Run 'dog instinct list' to see your instincts.")


def _run_instinct_cmd(args):

    from core.instincts import ensure_instincts_file, load_instincts

    ensure_instincts_file()

    if args.instinct_cmd == "list":
        instincts = load_instincts()
        if not instincts:
            print("\U0001f415 No instincts found.")
            return
        print("\n\U0001f415 Instincts\n")
        for i, inst in enumerate(instincts, 1):
            triggers = ", ".join(inst.triggers)
            print(f"  {i}. {inst.name}")
            print(f"     {inst.description}")
            print(f"     Triggers: {triggers}\n")
        print(f"Total: {len(instincts)} instincts")
        print("Edit: dog instinct edit")

    elif args.instinct_cmd == "show":
        instincts = load_instincts()
        name_lower = args.name.lower()
        for inst in instincts:
            if inst.name.lower() == name_lower:
                print(f"\n\U0001f415 {inst.name}\n")
                print(f"  Description: {inst.description}")
                print(f"  Triggers: {', '.join(inst.triggers)}")
                print(f"  Retrieval bias: {', '.join(inst.retrieval_bias)}")
                print(f"\n  Prompt:\n    {inst.prompt}")
                return
        print(f"No instinct named '{args.name}' found.")

    elif args.instinct_cmd == "edit":
        import os
        import subprocess

        editor = args.editor or os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
        path = str(ensure_instincts_file())
        subprocess.call([editor, path])

    else:
        print("Usage: dog instinct [list|show <name>|edit]")


def _run_status():
    from core.config import load_config
    from core.instincts import load_instincts

    try:
        config = load_config()
    except Exception:
        print("No config found. Run 'dog config' first.")
        return

    instincts = load_instincts()

    print("\n\U0001f415 MemoryDog Status\n")
    print(f"  Provider: {config.provider.model}")
    print(f"  Embedding: {config.embedding.model}")
    print(f"  Instincts: {len(instincts)} loaded")
    print("  Config: ~/.memorydog/config.toml")
    print("  Instincts file: ~/.memorydog/instincts.toml")

    if not config.provider.api_key:
        print("\n  \u26a0 No API key set. Run 'dog config' to configure.")
    else:
        masked = config.provider.api_key[:4] + "..." + config.provider.api_key[-4:]
        print(f"  API Key: {masked}")

    _check_db()
    _check_api_key(config)
    _check_embedding()


def _check_db():
    try:
        import asyncio

        async def check():
            from core.db import get_pool, init_db

            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.fetchrow("SELECT 1")
            await init_db()

        asyncio.run(check())
        print("  \u2705 Database: connected and migrated")
    except Exception:
        print("  \u2753 Database: not available (install Docker and run: docker compose up)")


def _check_api_key(config):
    """Test the configured API key against the provider."""
    key = config.provider.api_key
    if not key:
        print("  \u26a0 API key: not set")
        return

    try:
        from core.provider import LiteLLMProvider

        provider = LiteLLMProvider(
            model=config.provider.model,
            api_key=key,
            api_base=config.provider.api_base or None,
        )
        result = provider.check_connection()
        if result is None:
            print("  \u2705 API key: valid")
        else:
            print(f"  \u2753 API key: {result}")
    except Exception as e:
        print(f"  \u2753 API key: check failed ({e})")


def _check_embedding():
    try:
        import asyncio

        from core.memory import check_ollama

        status = asyncio.run(check_ollama())
        if status is None:
            print("  \u2705 Embeddings: Ollama connected")
        else:
            print(f"  \u2753 Embeddings: {status}")
    except Exception:
        print("  \u2753 Embeddings: check failed")


def _mask(key: str) -> str:
    if not key or len(key) < 8:
        return "(not set)"
    return key[:4] + "..." + key[-4:]


def _run_install():
    """Install the dog command to ~/.local/bin."""
    from pathlib import Path

    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parent.parent
    launcher = repo_root / "dog"
    link = bin_dir / "dog"

    if not launcher.exists():
        print("  \u2753 Launcher script not found at repo root")
        return

    if link.exists() and link.is_symlink():
        print(f"  \u2705 Already installed at {link}")
        return

    try:
        link.symlink_to(launcher)
        print(f"  \u2705 Installed dog to {link}")
        _check_path(bin_dir)
    except Exception as e:
        print(f"  \u2753 Install failed: {e}")


def _check_path(bin_dir):
    import os

    if str(bin_dir) not in os.environ.get("PATH", ""):
        shell = os.environ.get("SHELL", "bash")
        rc = ".bashrc" if "bash" in shell else ".zshrc"
        print(f"  \u2728 Add to PATH: echo 'export PATH=\"$PATH:{bin_dir}\"' >> ~/{rc}")


if __name__ == "__main__":
    main()
