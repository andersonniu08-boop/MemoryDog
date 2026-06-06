"""Tests for retrieval budget, triggered retrieval, and multi-stage retrieval."""

import pytest

from core.retrieval import (
    RetrievalBudget,
    clear_retrieval_log,
    extract_trigger_terms,
    get_retrieval_log,
    log_retrieval_event,
)


class TestRetrievalBudget:
    def test_default_budget(self):
        b = RetrievalBudget()
        assert b.max_triggered == 3
        assert b.max_total == 10
        assert b.triggered_count == 0
        assert b.total_count == 0

    def test_can_trigger_initially(self):
        b = RetrievalBudget()
        assert b.can_trigger() is True

    def test_budget_exhaustion_triggered(self):
        b = RetrievalBudget(max_triggered=2, max_total=10)
        assert b.can_trigger() is True
        b.record(triggered=True)
        assert b.can_trigger() is True
        b.record(triggered=True)
        assert b.can_trigger() is False

    def test_budget_exhaustion_total(self):
        b = RetrievalBudget(max_triggered=10, max_total=3)
        assert b.can_trigger() is True
        b.record(triggered=False)
        assert b.can_trigger() is True
        b.record(triggered=False)
        assert b.can_trigger() is True
        b.record(triggered=False)
        assert b.can_trigger() is False

    def test_remaining_triggered(self):
        b = RetrievalBudget(max_triggered=3, max_total=10)
        assert b.remaining_triggered() == 3
        b.record(triggered=True)
        assert b.remaining_triggered() == 2
        b.record(triggered=True)
        assert b.remaining_triggered() == 1
        b.record(triggered=True)
        assert b.remaining_triggered() == 0

    def test_non_triggered_does_not_count(self):
        b = RetrievalBudget(max_triggered=3, max_total=10)
        b.record(triggered=False)
        assert b.remaining_triggered() == 3

    def test_str_representation(self):
        b = RetrievalBudget(triggered_count=1, total_count=3)
        assert "1/3" in str(b)
        assert "3/10" in str(b)

    def test_budget_reset(self):
        b = RetrievalBudget(max_triggered=3, max_total=10)
        b.record(triggered=True)
        b.record(triggered=True)
        b.record(triggered=True)
        assert b.can_trigger() is False
        # Reset budget (e.g., new turn)
        b.triggered_count = 0
        b.total_count = 0
        assert b.can_trigger() is True


class TestExtractTriggerTerms:
    def test_empty_results(self):
        assert extract_trigger_terms([]) == []

    def test_identifiers_from_file_content(self):
        results = [
            {
                "tool_call_id": "1",
                "result": {
                    "success": True,
                    "content": "def process_async_queue():\n    return asyncio_queue_handler",
                },
            }
        ]
        terms = extract_trigger_terms(results)
        assert "process_async_queue" in terms
        assert "asyncio_queue_handler" in terms

    def test_identifiers_from_stdout(self):
        results = [
            {
                "tool_call_id": "2",
                "result": {
                    "success": True,
                    "stdout": "Found module: neural_network_trainer\n  optimization_strategy: adam",
                },
            }
        ]
        terms = extract_trigger_terms(results)
        assert "neural_network_trainer" in terms
        assert "optimization_strategy" in terms

    def test_common_words_filtered(self):
        results = [
            {
                "tool_call_id": "3",
                "result": {"success": True, "content": "the file path name list data code"},
            }
        ]
        terms = extract_trigger_terms(results)
        for word in ["the", "file", "path", "name", "data", "code"]:
            assert word not in terms

    def test_short_identifiers_filtered(self):
        results = [
            {
                "tool_call_id": "4",
                "result": {"success": True, "content": "a ab abc abcd abcde_model"},
            }
        ]
        terms = extract_trigger_terms(results)
        # Should filter out < 4 chars
        assert "abcdef_model" not in terms  # wrong pattern
        # Should keep 4+ char snake_case
        assert any("abcde_model" in t for t in terms)

    def test_camel_case_extraction(self):
        results = [
            {
                "tool_call_id": "5",
                "result": {"success": True, "content": "Found TextualMultiPaneLayout"},
            }
        ]
        terms = extract_trigger_terms(results)
        assert any("textual" in t for t in terms)

    def test_mixed_results(self):
        results = [
            {
                "tool_call_id": "6",
                "result": {"success": True, "content": "HandlerClass process_data"},
            },
            {
                "tool_call_id": "7",
                "result": {"success": True, "stdout": "async_function_name result"},
            },
        ]
        terms = extract_trigger_terms(results)
        assert "process_data" in terms
        assert "async_function_name" in terms

    def test_limit_20_terms(self):
        many_ids = " ".join(f"ident_{i}" for i in range(100))
        results = [{"tool_call_id": "8", "result": {"success": True, "content": many_ids}}]
        terms = extract_trigger_terms(results)
        assert len(terms) <= 20

    def test_path_extraction(self):
        results = [
            {
                "tool_call_id": "9",
                "result": {"success": True, "content": "/home/user/project/src/core/agent_loop.py"},
            }
        ]
        terms = extract_trigger_terms(results)
        assert any("agent_loop" in t for t in terms)

    def test_no_identifiers(self):
        results = [{"tool_call_id": "10", "result": {"success": True, "content": "hello world"}}]
        terms = extract_trigger_terms(results)
        assert len(terms) == 0


class TestRetrievalLog:
    def test_log_append(self):
        clear_retrieval_log()
        log_retrieval_event({"type": "test", "data": "hello"})
        log = get_retrieval_log()
        assert len(log) == 1
        assert log[0]["type"] == "test"
        assert "timestamp" in log[0]

    def test_log_clear(self):
        clear_retrieval_log()
        log_retrieval_event({"type": "test"})
        clear_retrieval_log()
        assert get_retrieval_log() == []

    def test_log_limit(self):
        clear_retrieval_log()
        for i in range(120):
            log_retrieval_event({"type": f"event_{i}"})
        log = get_retrieval_log()
        assert len(log) <= 70  # 120 - 50 trimmed


class TestAgentStateBudget:
    def test_agent_state_creates_budget(self):
        from core.agent_loop import AgentState

        state = AgentState()
        assert state.retrieval_budget is not None
        assert state.retrieval_budget.max_triggered == 3
        assert state.retrieval_budget.max_total == 10

    def test_budget_recorded_in_run_turn(self):
        from core.agent_loop import AgentState

        state = AgentState(workspace="test")
        state.retrieval_budget.record(triggered=False)
        assert state.retrieval_budget.total_count == 1

    @pytest.mark.asyncio
    async def test_initial_retrieval_counts(self):
        from core.agent_loop import AgentState, run_turn
        from core.provider import MockProvider

        provider = MockProvider()
        state = AgentState(workspace="test-budget")
        await run_turn(provider, state, "Hello")
        assert state.retrieval_budget.total_count >= 1
