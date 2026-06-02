"""Core agent execution loop."""
from dataclasses import dataclass, field

from core.provider import BaseProvider, Message
from core.tools import execute_tool, get_tool_definitions

DOG_STATUS = []  # mutable list so callers can read status messages


def dog_status(msg: str):
    DOG_STATUS.append(msg)


def pop_status() -> list[str]:
    msgs = list(DOG_STATUS)
    DOG_STATUS.clear()
    return msgs


@dataclass
class AgentState:
    history: list[Message] = field(default_factory=list)
    workspace: str = "."
    conversation_id: str | None = None
    active_instincts: list[str] = field(default_factory=list)


async def init_agent(workspace: str) -> AgentState:
    """Initialize database and return agent state."""
    from core.db import init_db

    await init_db()

    import os

    ws_name = os.path.basename(os.path.abspath(workspace)) or workspace
    return AgentState(workspace=ws_name)


async def run_turn(
    provider: BaseProvider,
    state: AgentState,
    user_input: str,
) -> str:
    try:
        instincts = _load_and_match_instincts(user_input, state.workspace)
    except Exception:
        instincts = []
    state.active_instincts = [i.name for i in instincts]

    if instincts:
        names = ", ".join(i.name for i in instincts)
        dog_status(f"Instinct activated: {names}")
        dog_status("Sniffing for related knowledge...")

    dog_status("Fetching memories...")

    try:
        memory_context = await _retrieve_context(user_input, state.workspace, instincts)
    except Exception:
        dog_status("Memory system not yet available — running without context")
        memory_context = ""

    system_msg = _build_system_with_context(state.workspace, memory_context, instincts)
    messages = [Message(role="system", content=system_msg)]
    if state.history:
        messages.extend(state.history)
    messages.append(Message(role="user", content=user_input))

    tools = get_tool_definitions()
    response = provider.chat(messages, tools=tools)

    if response.tool_calls:
        tool_results = []
        for tc in response.tool_calls:
            # Check if it's a memory_search call — handle internally
            if tc["name"] == "memory_search":
                result = await _handle_memory_search(
                    tc.get("parameters", {}), state.workspace
                )
            else:
                result = execute_tool(tc["name"], tc.get("parameters", {}))
            tool_results.append({"tool_call_id": tc.get("id", ""), "result": result})

        state.history.append(Message(role="assistant", content=response.content or ""))
        state.history.append(Message(role="tool", content=str(tool_results)))

        followup_messages = [Message(role="system", content=system_msg)]
        followup_messages.extend(state.history)
        followup_messages.append(
            Message(role="user", content="Tool results above. Continue.")
        )

        response2 = provider.chat(followup_messages, tools=[])
        state.history.append(
            Message(role="assistant", content=response2.content or "")
        )
        final = response2.content
    else:
        state.history.append(Message(role="assistant", content=response.content or ""))
        final = response.content

    try:
        await _extract_and_store(provider, user_input, final, state.workspace)
    except Exception:
        pass

    return final or ""


def _load_and_match_instincts(query: str, workspace: str) -> list:
    from core.instincts import load_instincts, match_instincts

    all_instincts = load_instincts()
    matched = match_instincts(all_instincts, query, workspace)
    return [inst for inst, score in matched]


async def _retrieve_context(query: str, workspace: str, instincts: list) -> str:
    from core.instincts import get_retrieval_bias
    from core.memory import count_memories, generate_embedding
    from core.retrieval import log_retrieval_access, retrieve_memories

    bias = get_retrieval_bias(instincts) if instincts else []
    embedding = await generate_embedding(query)

    results = await retrieve_memories(
        query=query,
        workspace=workspace,
        query_embedding=embedding,
        bias_terms=bias,
        limit=5,
    )

    if results:
        ids = [r["id"] for r in results]
        await log_retrieval_access(ids)

    total = await count_memories(workspace)
    if results:
        ages = []
        for r in results:
            if r.get("memory_type"):
                ages.append(r["memory_type"])
        dog_status(
            f"Found {len(results)} related memories"
            + (f" (total: {total})" if total > 0 else "")
            + (" across workspaces" if any(
                r.get("workspace_name") != workspace for r in results
            ) else "")
        )
    else:
        dog_status(f"No matching memories found (total: {total})")

    if not results:
        return ""

    lines = ["Relevant memories from previous sessions:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['memory_type']}] {r['content']}")
    return "\n".join(lines)


async def _handle_memory_search(params: dict, workspace: str) -> dict:
    from core.memory import generate_embedding
    from core.retrieval import retrieve_memories

    query = params.get("query", "")
    embedding = await generate_embedding(query)
    results = await retrieve_memories(
        query=query,
        workspace=workspace,
        query_embedding=embedding,
        limit=10,
    )

    return {
        "success": True,
        "memories": [
            {
                "id": r["id"],
                "type": r["memory_type"],
                "content": r["content"],
                "summary": r["summary"],
                "importance": r["importance"],
            }
            for r in results
        ],
        "count": len(results),
    }


def _build_system_with_context(workspace: str, memory_context: str, instincts: list) -> str:
    from core.instincts import get_instinct_prompts

    parts = [
        "You are MemoryDog, a coding agent with persistent memory.",
        "You have access to tools for reading, writing, editing files,"
        " running commands, and searching your memory.",
        f"Current workspace: {workspace}",
        "Be direct and concise. Use tools when you need to read or modify code.",
    ]

    if memory_context:
        parts.append(f"\n{memory_context}")

    instinct_prompts = get_instinct_prompts(instincts)
    if instinct_prompts:
        parts.append(f"\n{instinct_prompts}")

    return "\n".join(parts)


async def _extract_and_store(
    provider: BaseProvider,
    user_input: str,
    assistant_response: str,
    workspace: str,
):
    """Extract memories from the completed turn and store them."""
    from core.memory import extract_memories, store_extracted_memories

    records = await extract_memories(provider, user_input, assistant_response, workspace)
    if records:
        count = await store_extracted_memories(records)
        if count > 0:
            dog_status(f"Learned {count} new thing{'s' if count > 1 else ''}")
