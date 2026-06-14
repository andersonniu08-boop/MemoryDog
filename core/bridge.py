"""JSON-RPC bridge for VS Code extension — stdin/stdout protocol.

The extension spawns `dog serve` as a subprocess and communicates via
newline-delimited JSON-RPC 2.0 messages.

Agent state transitions are sent as notifications:
  → {"jsonrpc":"2.0","method":"state","params":{"state":"Thinking","from":"Ready"}}
  → {"jsonrpc":"2.0","method":"status","params":{"message":"..."}}
  → {"jsonrpc":"2.0","method":"token","params":{"token":"..."}}
  ← {"jsonrpc":"2.0","id":1,"result":{"content":"..."}}
"""

import asyncio
import json
import os
import sys
import traceback
from enum import StrEnum
from typing import Any

from core.agent_loop import AgentState

# Per-workspace agent state — persists across chat turns
_agent_states: dict[str, AgentState] = {}
_agent_locks: dict[str, asyncio.Lock] = {}


class BridgeAgentState(StrEnum):
    """Structured state for the agent lifecycle.
    
    These flow through the bridge as RPC notifications so the extension
    can react programmatically (disable input, show status, drive animations).
    """
    READY = "Ready"
    THINKING = "Thinking"
    RETRIEVING_MEMORIES = "RetrievingMemories"
    RUNNING_TOOLS = "RunningTools"
    EXTRACTING_MEMORIES = "ExtractingMemories"
    SUCCESS = "Success"
    ERROR = "Error"


def _state_transition(new_state: BridgeAgentState, detail: str = ""):
    """Notify the extension of an agent state transition."""
    _notify("state", {"state": new_state.value, "detail": detail})


def _workspace_name(workspace: str) -> str:
    """Normalize a workspace path to a short name."""
    return os.path.basename(os.path.abspath(workspace)) or workspace


async def handle_request(method: str, params: dict, msg_id: Any) -> dict | None:
    """Dispatch a JSON-RPC method to the appropriate handler."""
    if method == "chat":
        return await handle_chat(params, msg_id)
    elif method == "reset_chat":
        return await handle_reset_chat(params)
    elif method == "get_memories":
        return await handle_get_memories(params)
    elif method == "get_instincts":
        return await handle_get_instincts()
    elif method == "get_status":
        return await handle_get_status(params)
    elif method == "set_config":
        return await handle_set_config(params)
    elif method == "check_health":
        return await handle_check_health()
    elif method == "list_models":
        return await handle_list_models()
    elif method == "pull_model":
        return await handle_pull_model(params)
    elif method == "current_model":
        return await handle_current_model()
    elif method == "ping":
        return {"pong": True}
    else:
        return {"error": f"Unknown method: {method}"}


async def handle_chat(params: dict, msg_id: Any) -> dict:
    """Run a chat turn with streaming status + token notifications.

    Agent state is persisted per workspace across calls, so conversation
    history and context carry forward between turns.
    """
    from core.agent_loop import init_agent, run_turn
    from core.provider import LiteLLMProvider

    user_input = params.get("user_input", "")
    workspace = params.get("workspace", ".")
    model_override = params.get("model")

    if not user_input.strip():
        return {"error": "user_input is required"}

    ws_name = _workspace_name(workspace)

    # Lock to prevent concurrent chat calls corrupting shared state
    if ws_name not in _agent_locks:
        _agent_locks[ws_name] = asyncio.Lock()

    async with _agent_locks[ws_name]:
        # Reuse or create agent state for this workspace
        if ws_name not in _agent_states:
            state = AgentState(workspace=ws_name)
            _agent_states[ws_name] = state
            try:
                await init_agent(workspace)
            except Exception:
                pass
        else:
            state = _agent_states[ws_name]

    from core.config import load_config
    from core.provider import create_provider

    config = load_config()
    pc = config.provider

    # Apply model override if provided
    if model_override:
        pc.model = model_override

    provider = create_provider(config)

    # Override model if specified in the chat request
    if model_override:
        provider.model = model_override

    def on_status(msg: str):
        _notify("status", {"message": msg})
        # Map unstructured status strings to structured states
        m = msg.lower()
        if m.startswith("fetching"):
            _state_transition(BridgeAgentState.RETRIEVING_MEMORIES, msg)
        elif m.startswith("thinking"):
            _state_transition(BridgeAgentState.THINKING, msg)
        elif m.startswith("executing"):
            _state_transition(BridgeAgentState.RUNNING_TOOLS, msg)
        elif m.startswith("extracting") or m.startswith("saving"):
            _state_transition(BridgeAgentState.EXTRACTING_MEMORIES, msg)

    def on_token(token: str):
        _notify("token", {"token": token})

    def on_memories(memories: list):
        _notify(
            "memories",
            {
                "memories": [
                    {
                        "id": str(m.get("id", "")),
                        "content": m.get("content", ""),
                        "summary": m.get("summary", ""),
                        "type": m.get("memory_type", "conversation"),
                        "importance": float(m.get("importance", 0.5)),
                    }
                    for m in memories
                ]
            },
        )

    try:
        response = await run_turn(
            provider,
            state,
            user_input,
            on_status=on_status,
            on_token=on_token,
            on_memories=on_memories,
        )
        _state_transition(BridgeAgentState.SUCCESS, "Done")
        return {"content": response}
    except Exception as e:
        _state_transition(BridgeAgentState.ERROR, str(e))
        return {"error": str(e), "content": ""}


async def handle_reset_chat(params: dict) -> dict:
    """Reset conversation history for a workspace."""
    workspace = params.get("workspace", ".")
    ws_name = _workspace_name(workspace)
    if ws_name in _agent_states:
        _agent_states[ws_name] = AgentState(workspace=ws_name)
    return {"success": True}


async def handle_get_memories(params: dict) -> dict:
    """Return memories for the given workspace."""
    from core.memory import count_memories, generate_embedding
    from core.retrieval import retrieve_memories

    workspace = params.get("workspace", ".")
    query = params.get("query", "")
    limit = int(params.get("limit", 20))

    ws_name = _workspace_name(workspace)

    try:
        embedding = await generate_embedding(query) if query else None
        results = await retrieve_memories(
            query=query or " ",
            workspace=ws_name,
            query_embedding=embedding,
            limit=limit,
        )
        total = await count_memories(ws_name)

        memories = []
        for r in results:
            memories.append(
                {
                    "id": str(r.get("id", "")),
                    "content": r.get("content", ""),
                    "summary": r.get("summary", ""),
                    "memory_type": r.get("memory_type", "conversation"),
                    "workspace": r.get("workspace_name", ws_name),
                    "importance": float(r.get("importance", 0.5)),
                }
            )

        return {"memories": memories, "total": total}
    except Exception as e:
        return {"memories": [], "total": 0, "error": str(e)}


async def handle_get_instincts() -> dict:
    """Return all loaded instincts."""
    from core.instincts import load_instincts

    try:
        instincts = load_instincts()
        result = []
        for i in instincts:
            result.append(
                {
                    "name": i.name,
                    "description": i.description,
                    "triggers": i.triggers,
                    "prompt": i.prompt,
                    "retrieval_bias": i.retrieval_bias,
                }
            )
        return {"instincts": result}
    except Exception as e:
        return {"instincts": [], "error": str(e)}


async def handle_get_status(params: dict) -> dict:
    """Return current status: workspace, memory count, provider, model."""
    from core.config import load_config
    from core.instincts import load_instincts

    workspace = params.get("workspace", ".")
    ws_name = _workspace_name(workspace)

    result = {
        "workspace": ws_name,
        "memory_count": 0,
        "instinct_count": 0,
        "provider": "unknown",
        "model": "unknown",
        "state": BridgeAgentState.READY.value,
    }

    try:
        config = load_config()
        model = config.provider.model
        result["provider"] = model.split("/")[0] if "/" in model else model
        result["model"] = model
    except Exception:
        pass

    try:
        instincts = load_instincts()
        result["instinct_count"] = len(instincts)
    except Exception:
        pass

    try:
        from core.memory import count_memories

        result["memory_count"] = await count_memories(ws_name)
    except Exception:
        pass

    return result


async def handle_set_config(params: dict) -> dict:
    """Save API key and/or model to config."""
    from core.config import load_config, save_config

    try:
        config = load_config()
    except Exception:
        from core.config import Config

        config = Config()

    if "api_key" in params and params["api_key"] and params["api_key"].strip():
        config.provider.api_key = params["api_key"].strip()
    if "model" in params and params["model"] and params["model"].strip():
        config.provider.model = params["model"].strip()

    save_config(config)
    return {"success": True}


async def handle_check_health() -> dict:
    """Run diagnostics: API key, database, embeddings.

    Skips provider validation if API key is missing — no point hitting the API.
    """
    result = {
        "api_key": "not_checked",
        "database": "not_checked",
        "embedding": "not_checked",
        "all_ok": False,
    }

    # Check API key presence and validity
    try:
        from core.config import load_config

        config = load_config()
        key = config.provider.api_key.strip() if config.provider.api_key else ""

        if not key:
            result["api_key"] = "missing"
        elif len(key) < 8:
            result["api_key"] = "invalid"
        else:
            from core.provider import LiteLLMProvider

            provider = LiteLLMProvider(
                model=config.provider.model,
                api_key=key,
                api_base=config.provider.api_base or None,
            )
            err = provider.check_connection()
            result["api_key"] = "ok" if err is None else "rejected"
    except Exception as e:
        result["api_key"] = f"error: {e}"

    # Check database
    try:
        from core.db import get_pool, init_db

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchrow("SELECT 1")
        await init_db()
        result["database"] = "ok"
    except Exception as e:
        result["database"] = f"error: {e}"

    # Check embeddings (Ollama)
    try:
        from core.memory import check_ollama

        status = await check_ollama()
        result["embedding"] = "ok" if status is None else f"error: {status}"
    except Exception as e:
        result["embedding"] = f"error: {e}"

    result["all_ok"] = (
        result["api_key"] == "ok" and result["database"] == "ok" and result["embedding"] == "ok"
    )

    return result


async def handle_list_models() -> dict:
    """List models installed in Ollama."""
    try:
        import httpx

        async with httpx.AsyncClient() as c:
            resp = await c.get("http://localhost:11434/api/tags", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                models.append({
                    "name": name,
                    "size": m.get("size", 0),
                    "modified": m.get("modified_at", ""),
                })
            return {"models": models, "count": len(models)}
    except Exception as e:
        return {"error": str(e), "models": [], "count": 0}


async def handle_pull_model(params: dict) -> dict:
    """Pull a model via Ollama. Returns immediately, pull runs in background."""
    model = params.get("model", "")
    if not model:
        return {"error": "model name required"}

    import asyncio

    async def _do_pull():
        try:
            import httpx
            async with httpx.AsyncClient() as c:
                await c.post("http://localhost:11434/api/pull", json={"model": model}, timeout=600)
        except Exception:
            pass

    asyncio.create_task(_do_pull())
    return {"success": True, "message": f"Pulling {model} in background..."}


async def handle_current_model() -> dict:
    """Return the currently configured model."""
    from core.config import load_config

    config = load_config()
    return {
        "provider_type": getattr(config.provider, "provider_type", "litellm"),
        "model": config.provider.model,
    }


def _notify(method: str, params: dict):
    """Write a JSON-RPC notification to stdout (no id)."""
    msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _write_response(msg_id: Any, result: dict):
    """Write a JSON-RPC response to stdout."""
    resp = {"jsonrpc": "2.0", "id": msg_id, "result": result}
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def _write_error(msg_id: Any, code: int, message: str):
    """Write a JSON-RPC error response."""
    resp = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


async def serve():
    """Main loop: read JSON-RPC from stdin, dispatch, write responses to stdout."""
    loop = asyncio.get_event_loop()

    while True:
        # Read a line from stdin in a thread (avoid asyncio pipe complexity)
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except Exception:
            break

        if not line:
            break  # EOF — extension closed

        line_str = line.strip()
        if not line_str:
            continue

        try:
            msg = json.loads(line_str)
        except json.JSONDecodeError:
            _write_error(None, -32700, "Parse error")
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        try:
            result = await handle_request(method, params, msg_id)
            if result is not None:
                _write_response(msg_id, result)
        except Exception as e:
            _write_error(msg_id, -32603, str(e))
            traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(serve())
