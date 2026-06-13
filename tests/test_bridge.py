"""Tests for the JSON-RPC bridge."""

import json

import pytest


class TestWorkspaceName:
    def test_workspace_name_from_path(self):
        from core.bridge import _workspace_name

        assert _workspace_name("/home/user/my-project") == "my-project"

    def test_workspace_name_dot(self):
        from core.bridge import _workspace_name

        assert _workspace_name(".") != ""

    def test_workspace_name_trailing_slash(self):
        from core.bridge import _workspace_name

        name = _workspace_name("/home/user/project/")
        assert name == "project"
        assert "/" not in name


class TestRpcNotifications:
    """Tests for JSON-RPC notification formatting (no I/O needed)."""

    def test_notify_format(self):
        import io
        import sys

        from core.bridge import _notify

        old = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            _notify("status", {"message": "hello"})
        finally:
            sys.stdout = old

        result = json.loads(buf.getvalue().strip())
        assert result["jsonrpc"] == "2.0"
        assert result["method"] == "status"
        assert "id" not in result
        assert result["params"]["message"] == "hello"

    def test_response_format(self):
        import io
        import sys

        from core.bridge import _write_response

        old = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            _write_response(42, {"content": "hello"})
        finally:
            sys.stdout = old

        result = json.loads(buf.getvalue().strip())
        assert result["jsonrpc"] == "2.0"
        assert result["id"] == 42
        assert result["result"]["content"] == "hello"

    def test_error_format(self):
        import io
        import sys

        from core.bridge import _write_error

        old = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            _write_error(7, -32600, "Invalid Request")
        finally:
            sys.stdout = old

        result = json.loads(buf.getvalue().strip())
        assert result["jsonrpc"] == "2.0"
        assert result["id"] == 7
        assert result["error"]["code"] == -32600
        assert result["error"]["message"] == "Invalid Request"


class TestHandleRequestDispatch:
    """Tests for the RPC method dispatcher (no I/O)."""

    @pytest.mark.asyncio
    async def test_ping(self):
        from core.bridge import handle_request

        result = await handle_request("ping", {}, 1)
        assert result["pong"] is True

    @pytest.mark.asyncio
    async def test_unknown_method(self):
        from core.bridge import handle_request

        result = await handle_request("nonexistent", {}, 1)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_reset_chat(self):
        from core.bridge import handle_reset_chat

        result = await handle_reset_chat({"workspace": "test"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_chat_empty_input(self):
        from core.bridge import handle_chat

        result = await handle_chat({"user_input": "", "workspace": "test"}, 1)
        assert "error" in result


class TestAgentStatePersistence:
    """Tests for per-workspace agent state persistence."""

    @pytest.mark.asyncio
    async def test_state_created_and_reused(self):
        from core.bridge import _agent_states, _workspace_name, handle_chat

        # Clear state
        _agent_states.clear()

        ws = _workspace_name("test-persist")

        # Override the provider to avoid real LLM calls
        import core.agent_loop as agent_mod

        original_run = agent_mod.run_turn
        called_states = []

        async def fake_run(provider, state, user_input, **kwargs):
            called_states.append(id(state))
            return "mock response"

        agent_mod.run_turn = fake_run
        try:
            await handle_chat({"user_input": "hello", "workspace": "test-persist"}, 1)
            await handle_chat({"user_input": "follow up", "workspace": "test-persist"}, 2)

            # Both calls should use the same state object
            assert len(called_states) == 2
            assert called_states[0] == called_states[1]
            assert ws in _agent_states
        finally:
            agent_mod.run_turn = original_run
            _agent_states.clear()

    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        from core.bridge import _agent_states, handle_chat, handle_reset_chat

        _agent_states.clear()

        import core.agent_loop as agent_mod

        original_run = agent_mod.run_turn
        called_states = []

        async def fake_run(provider, state, user_input, **kwargs):
            called_states.append(id(state))
            return "mock"

        agent_mod.run_turn = fake_run
        try:
            await handle_chat({"user_input": "hello", "workspace": "test-reset"}, 1)
            state1 = called_states[0]

            await handle_reset_chat({"workspace": "test-reset"})

            await handle_chat({"user_input": "after reset", "workspace": "test-reset"}, 2)
            state2 = called_states[1]

            # After reset, state should be different
            assert state1 != state2
        finally:
            agent_mod.run_turn = original_run
            _agent_states.clear()
