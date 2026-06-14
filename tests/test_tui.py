"""Tests for MemoryDog core and CLI."""

import sys

import pytest

from core.agent_loop import AgentState, run_turn
from core.config import Config
from core.context import build_messages, build_system_prompt
from core.provider import LiteLLMProvider, Message, MockProvider
from core.tools import TOOL_REGISTRY, execute_tool, get_tool_definitions


def test_mock_provider_returns_response():
    provider = MockProvider()
    messages = [Message(role="user", content="Hello")]
    response = provider.chat(messages)
    assert response.content
    assert isinstance(response.content, str)
    assert len(response.content) > 0


def test_mock_provider_tracks_tokens():
    provider = MockProvider()
    provider.chat([Message(role="user", content="test")])
    assert provider.last_tokens >= 0


def test_litellm_provider_instantiation():
    provider = LiteLLMProvider(
        model="claude-sonnet-4-20250514",
        api_key="test-key",
    )
    assert provider.model == "claude-sonnet-4-20250514"
    assert provider.last_tokens == 0


def test_build_system_prompt():
    prompt = build_system_prompt(workspace="test-project")
    assert "coding agent" in prompt.lower()
    assert "tools" in prompt.lower()


def test_build_messages_appends_history():
    history = [Message(role="user", content="hi"), Message(role="assistant", content="hey")]
    msgs = build_messages(history=history, user_input="do stuff")
    assert msgs[0].role == "system"
    assert msgs[-1].role == "user"
    assert msgs[-1].content == "do stuff"


def test_get_tool_definitions_returns_list():
    defs = get_tool_definitions()
    assert isinstance(defs, list)
    assert len(defs) >= 6
    for d in defs:
        assert d["type"] == "function"
        assert "name" in d["function"]
        assert "description" in d["function"]


def test_tool_registry_has_all_tools():
    assert "read" in TOOL_REGISTRY
    assert "write" in TOOL_REGISTRY
    assert "edit" in TOOL_REGISTRY
    assert "bash" in TOOL_REGISTRY
    assert "glob" in TOOL_REGISTRY
    assert "grep" in TOOL_REGISTRY


def test_execute_read_returns_file_content(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    result = execute_tool("read", {"filePath": str(f)})
    assert result["success"]
    assert "hello world" in result["content"]


async def test_run_turn_returns_response():
    provider = MockProvider()
    state = AgentState()
    response = await run_turn(provider, state, "Hello")
    assert response
    assert "Hello" in response or "I'm MemoryDog" in response
    assert len(state.history) >= 1


async def test_run_turn_maintains_history():
    provider = MockProvider()
    state = AgentState()
    await run_turn(provider, state, "hi")
    await run_turn(provider, state, "what's up")
    assert len(state.history) >= 2


def test_cli_help_runs():
    old = sys.argv
    sys.argv = ["dog", "--help"]
    try:
        from cli.main import main

        main()
    except SystemExit:
        pass
    finally:
        sys.argv = old


async def test_chat_screen_exists():
    from cli.ui.chat import ChatScreen

    screen = ChatScreen(workspace="test-project", model_name="test-model")
    assert screen is not None
    assert screen.workspace == "test-project"
    assert screen.model_name == "test-model"


def test_config_defaults():
    config = Config()
    assert "phi4-mini" in config.provider.model
    assert "nomic-embed-text" in config.embedding.model
    assert config.database.url


def test_config_load_creates_file(tmp_path, monkeypatch):
    import core.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / ".memorydog")
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / ".memorydog" / "config.toml")

    cfg = config_module.create_default_config()
    assert "phi4-mini" in cfg.provider.model
    assert (tmp_path / ".memorydog" / "config.toml").exists()


def test_make_mock_provider():
    from cli.main import _make_mock_provider

    provider = _make_mock_provider()
    from core.provider import MockProvider

    assert isinstance(provider, MockProvider)


def test_dog_status_runs():
    old = sys.argv
    sys.argv = ["dog", "status"]
    try:
        from cli.main import main

        main()
    except SystemExit:
        pass
    finally:
        sys.argv = old


def test_dog_instinct_list_runs():
    old = sys.argv
    sys.argv = ["dog", "instinct", "list"]
    try:
        from cli.main import main

        main()
    except SystemExit:
        pass
    finally:
        sys.argv = old


def test_ranking_formula():
    from core.ranking import score_memory

    s = score_memory(
        vector_score=0.8,
        bm25_score=0.6,
        days_since_access=0,
        importance=0.9,
        decay_factor=1.0,
        same_workspace=True,
        access_count=10,
        mean_access_count=5.0,
    )
    assert 0 < s < 1.5


def test_ranking_penalizes_old_memories():
    from core.ranking import score_memory

    recent = score_memory(0.8, 0.6, 0, 0.5, 1.0, True, 5, 5.0)
    old = score_memory(0.8, 0.6, 100, 0.5, 1.0, True, 5, 5.0)
    assert recent > old


def test_ranking_boosts_same_workspace():
    from core.ranking import score_memory

    same = score_memory(0.8, 0.6, 0, 0.5, 1.0, True, 5, 5.0)
    diff = score_memory(0.8, 0.6, 0, 0.5, 1.0, False, 5, 5.0)
    assert same > diff


def test_load_instincts():
    from core.instincts import load_instincts

    instincts = load_instincts()
    assert len(instincts) >= 3
    names = [i.name for i in instincts]
    assert "Bug Hunter" in names
    assert "AI Evaluation Expert" in names


def test_match_instincts():
    from core.instincts import load_instincts, match_instincts

    instincts = load_instincts()
    matched = match_instincts(instincts, "there is a race condition bug", "test-project")
    assert len(matched) > 0
    assert matched[0][0].name == "Bug Hunter"


def test_instinct_no_match():
    from core.instincts import load_instincts, match_instincts

    instincts = load_instincts()
    matched = match_instincts(instincts, "hello world", "test-project")
    assert len(matched) == 0


def test_ranking_all_zero_scores():
    from core.ranking import score_memory

    s = score_memory(0.0, 0.0, 0, 0.0, 0.0, False, 0, 0.0)
    assert isinstance(s, float)
    assert s >= 0.0


def test_ranking_division_by_zero_mean_access():
    from core.ranking import score_memory

    s = score_memory(
        vector_score=0.8,
        bm25_score=0.6,
        days_since_access=10,
        importance=0.9,
        decay_factor=1.0,
        same_workspace=True,
        access_count=10,
        mean_access_count=0.0,
    )
    assert s > 0
    assert isinstance(s, float)


def test_ranking_very_old_memory():
    from core.ranking import score_memory

    very_old = score_memory(0.8, 0.6, 1000, 0.5, 1.0, True, 5, 5.0)
    recent = score_memory(0.8, 0.6, 0, 0.5, 1.0, True, 5, 5.0)
    assert very_old < recent
    assert very_old >= 0.0


def test_ranking_sanitize():
    from core.ranking import sanitize

    assert sanitize(0.5) == 0.5
    assert sanitize(-0.3) == 0.0
    assert sanitize(1.5) == 1.0
    assert sanitize(0.0) == 0.0
    assert sanitize(1.0) == 1.0
    assert sanitize(None) == 0.0


def test_ranking_workspace_boost():
    from core.ranking import score_memory

    same = score_memory(0.8, 0.6, 0, 0.5, 1.0, True, 5, 5.0)
    diff = score_memory(0.8, 0.6, 0, 0.5, 1.0, False, 5, 5.0)
    assert same > diff
    assert same - diff == pytest.approx(0.20, abs=1e-6)

    zero_case_same = score_memory(0.0, 0.0, 0, 0.0, 0.0, True, 0, 0.0)
    zero_case_diff = score_memory(0.0, 0.0, 0, 0.0, 0.0, False, 0, 0.0)
    assert zero_case_same >= zero_case_diff


def test_ranking_handles_none_values():
    from core.ranking import score_memory

    s = score_memory(None, None, None, None, None, False, None, None)
    assert isinstance(s, float)
    assert s >= 0.0


def test_rerank_with_confidence():
    from core.retrieval import rerank_with_confidence

    results = [
        {"id": "1", "content": "a", "importance": 0.9},
        {"id": "2", "content": "b", "importance": 0.2},
        {"id": "3", "content": "c", "importance": 0.5},
        {"id": "4", "content": "d", "importance": 0.3},
        {"id": "5", "content": "e", "importance": 0.1},
    ]
    filtered = rerank_with_confidence(results, confidence_threshold=0.3)
    ids = [r["id"] for r in filtered]
    assert "1" in ids
    assert "2" not in ids
    assert "3" in ids
    assert "4" in ids
    assert "5" not in ids


def test_rerank_with_confidence_empty():
    from core.retrieval import rerank_with_confidence

    assert rerank_with_confidence([]) == []


def test_rerank_with_confidence_missing_importance():
    from core.retrieval import rerank_with_confidence

    results = [
        {"id": "1", "content": "a"},
        {"id": "2", "content": "b", "importance": None},
    ]
    filtered = rerank_with_confidence(results, confidence_threshold=0.3)
    assert len(filtered) == 2
