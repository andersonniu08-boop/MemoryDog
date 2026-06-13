"""Core agent execution loop."""
import asyncio
import json
import re
from dataclasses import dataclass, field

from core.provider import BaseProvider, Message
from core.retrieval import RetrievalBudget
from core.tools import execute_tool, get_tool_definitions

DOG_STATUS = []


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
    retrieval_budget: "RetrievalBudget" = None

    def __post_init__(self):
        if self.retrieval_budget is None:
            self.retrieval_budget = RetrievalBudget()


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
    on_status=None,
    on_token=None,
    on_memories=None,
) -> str:
    _status("Matching instincts...", on_status)
    try:
        instincts = _load_and_match_instincts(user_input, state.workspace)
    except Exception:
        instincts = []
    state.active_instincts = [i.name for i in instincts]

    if instincts:
        names = ", ".join(i.name for i in instincts)
        _status(f"Instinct activated: {names}", on_status)
        _status("Sniffing for related knowledge...", on_status)

    _status("Fetching memories...", on_status)

    try:
        memory_context, memory_results = await _retrieve_context(
            user_input, state.workspace, instincts, on_status
        )
        if memory_results and on_memories:
            on_memories(memory_results)
    except Exception:
        _status("Memory system not yet available", on_status)
        memory_context = ""
        memory_results = []

    system_msg = _build_system_with_context(state.workspace, memory_context, instincts)

    _status("Thinking...", on_status)

    messages = [Message(role="system", content=system_msg)]
    if state.history:
        messages.extend(state.history)
    messages.append(Message(role="user", content=user_input))

    tools = get_tool_definitions()

    # Stream the initial response — capture LLMResponse from the stream return value
    full_content = ""
    stream_result = []
    async for token in _async_stream(provider, messages, tools, on_token, stream_result):
        if isinstance(token, str):
            full_content += token
    response = stream_result[0] if stream_result else None
    if response is None:
        response = await provider.chat_async(messages, tools=tools)

    # Record initial retrieval in budget
    state.retrieval_budget.record(triggered=False)

    if response.tool_calls:
        _status("Executing tools...", on_status)
        tool_results = []
        tool_calls_api = []
        for tc in response.tool_calls:
            tc_id = tc.get("id", "")
            tc_name = tc.get("name", "")
            tc_params = tc.get("parameters", {})
            if isinstance(tc_params, str):
                try:
                    tc_params = json.loads(tc_params)
                except json.JSONDecodeError:
                    tc_params = {}

            tool_calls_api.append(
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": tc_name,
                        "arguments": json.dumps(tc_params),
                    },
                }
            )

            try:
                if tc_name == "memory_search":
                    result = await _handle_memory_search(tc_params, state.workspace)
                else:
                    result = await asyncio.to_thread(execute_tool, tc_name, tc_params)
            except Exception as e:
                result = {"success": False, "error": str(e)}

            tool_results.append({"tool_call_id": tc_id, "result": result})

        state.history.append(
            Message(
                role="assistant",
                content=response.content or "",
                tool_calls=tool_calls_api,
            )
        )
        for tr in tool_results:
            state.history.append(
                Message(
                    role="tool",
                    content=json.dumps(tr["result"]),
                    tool_call_id=tr["tool_call_id"],
                )
            )

        # Stage 2: Triggered retrieval based on tool output
        from core.retrieval import triggered_retrieval

        triggered_memories = await triggered_retrieval(
            tool_results=tool_results,
            workspace=state.workspace,
            budget=state.retrieval_budget,
            existing_memory_ids=set(),
        )

        if triggered_memories:
            _status(f"Found {len(triggered_memories)} more related memories", on_status)
            extra_lines = ["Additional relevant memories:"]
            for i, r in enumerate(triggered_memories, 1):
                extra_lines.append(
                    f"{len(triggered_memories) + i}. [{r['memory_type']}] {r['content']}"
                )
            extra_context = "\n".join(extra_lines)
            memory_context = (
                (memory_context + "\n" + extra_context) if memory_context else extra_context
            )
            system_msg = _build_system_with_context(state.workspace, memory_context, instincts)

        followup_messages = [Message(role="system", content=system_msg)]
        followup_messages.extend(state.history)
        followup_messages.append(Message(
            role="user",
            content="I received the tool results shown above. "
                    "Using those results, answer my original question directly. "
                    "Do NOT call any more tools."
        ))

        _status("Thinking...", on_status)
        full_content2 = ""
        stream_result2 = []
        async for token in _async_stream(provider, followup_messages, [], on_token, stream_result2):
            full_content2 += token
        response2 = stream_result2[0] if stream_result2 else None
        if response2 is None:
            response2 = await provider.chat_async(followup_messages, tools=[])
        state.history.append(Message(role="assistant", content=response2.content or ""))
        final = response2.content or full_content2
    else:
        state.history.append(Message(role="assistant", content=response.content or ""))
        final = response.content or full_content

    # Skip extraction for trivial turns to save tokens
    if _should_extract(user_input, final):
        _status("Extracting memories...", on_status)
        try:
            await _extract_and_store(provider, user_input, final, state.workspace, on_status)
        except Exception:
            pass
    else:
        _status("Skipped extraction (trivial turn)", on_status)

    # Clean any residual XML tool-call markup from final response
    final = _clean_response(final or "")
    return final or ""


def _clean_response(text: str) -> str:
    """Strip XML tool-call markup (DeepSeek v4) from response text."""
    text = re.sub(r'<invoke[^>]*>.*?</invoke>', '', text, flags=re.DOTALL)
    text = re.sub(r'<tool_calls>.*?</tool_calls>', '', text, flags=re.DOTALL)
    text = re.sub(r'<result[^>]*>.*?</result>', '', text, flags=re.DOTALL)
    text = re.sub(r'<output>.*?</output>', '', text, flags=re.DOTALL)
    return text.strip()


def _should_extract(user_input: str, response: str) -> bool:
    """Determine if a conversation turn is worth extracting memories from."""
    if len(user_input) < 15:
        return False
    greetings = {"hello", "hi", "hey", "thanks", "ok", "okay", "goodbye", "bye", "done"}
    first_word = user_input.lower().split()[0] if user_input.split() else ""
    if first_word in greetings:
        return False
    if len(response) < 10:
        return False
    return True


async def _async_stream(provider, messages, tools, on_token, result_out=None):
    """Run sync chat_stream in a thread, yielding tokens as they arrive."""
    import queue as qmod

    q: qmod.Queue = qmod.Queue()
    sentinel = object()

    def _produce():
        gen = provider.chat_stream(messages, tools=tools)
        try:
            while True:
                token = next(gen)
                q.put(token)
        except StopIteration as e:
            if result_out is not None:
                result_out.append(e.value)
        except Exception:
            pass
        finally:
            q.put(sentinel)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _produce)

    while True:
        token = await loop.run_in_executor(None, q.get)
        if token is sentinel:
            break
        if on_token:
            on_token(token)
        yield token


def _status(msg: str, callback):
    dog_status(msg)
    if callback:
        callback(msg)


def _load_and_match_instincts(query: str, workspace: str) -> list:
    from core.instincts import load_instincts, match_instincts

    all_instincts = load_instincts()
    matched = match_instincts(all_instincts, query, workspace)
    return [inst for inst, score in matched]


async def _retrieve_context(
    query: str,
    workspace: str,
    instincts: list,
    on_status=None,
) -> tuple[str, list[dict]]:
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
        _status(
            f"Found {len(results)} related memories" + (f" (total: {total})" if total > 0 else ""),
            on_status,
        )
    else:
        _status(f"No matching memories found (total: {total})", on_status)

    if not results:
        return "", []

    lines = ["Relevant memories from previous sessions:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['memory_type']}] {r['content']}")
    return "\n".join(lines), results


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
        "",
        "## Memory System",
        "You have persistent memory that carries knowledge across sessions.",
        "When the user asks about past decisions, facts, or context:",
        "  - First consult the memories provided below",
        "  - Answer directly from memory if the information is there",
        "  - Only use tools if the answer is not in memory or the user asks for current code",
        "",
        "## Tools",
        "You have access to tools for reading, writing, editing files,"
        " running commands, and searching your memory.",
        "Use tools when you need to read or modify code.",
        f"Current workspace: {workspace}",
        "Be direct and concise.",
    ]

    if memory_context:
        parts.append(f"\n## Retrieved Memories\n{memory_context}")

    instinct_prompts = get_instinct_prompts(instincts)
    if instinct_prompts:
        parts.append(f"\n{instinct_prompts}")

    return "\n".join(parts)


async def _extract_and_store(
    provider: BaseProvider,
    user_input: str,
    assistant_response: str,
    workspace: str,
    on_status=None,
):
    """Extract memories from the completed turn and store them."""
    from core.memory import extract_memories, store_extracted_memories

    records = await extract_memories(provider, user_input, assistant_response, workspace)
    if records:
        _status("Saving memories...", on_status)
        count = await store_extracted_memories(records)
        if count > 0:
            _status(f"Learned {count} new thing{'s' if count > 1 else ''}", on_status)
