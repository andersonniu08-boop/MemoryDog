"""LLM provider abstraction."""

from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass, field


@dataclass
class Message:
    role: str
    content: str
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    token_count: int = 0
    error: str = ""


class ProviderError(Exception):
    """Raised when a provider encounters a non-recoverable error."""


class BaseProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse: ...

    async def chat_async(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> LLMResponse:
        """Non-blocking version of chat() that runs in a thread pool."""
        import asyncio

        return await asyncio.to_thread(self.chat, messages, tools)

    def chat_stream(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> Generator[str, None, LLMResponse]:
        response = self.chat(messages, tools)
        yield response.content
        return response

    def check_connection(self) -> str | None:
        """Check if the provider is configured and reachable.
        Returns None if OK, or an error message string."""
        return None


class MockProvider(BaseProvider):
    def __init__(self):
        self.last_tokens = 0

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        last = messages[-1].content.lower() if messages else ""
        if "hello" in last or "hi" in last:
            response = LLMResponse(content="Hello! I'm MemoryDog. How can I help you today?")
        else:
            response = LLMResponse(
                content="I understand. Let me help you with that.",
                token_count=42,
            )
        self.last_tokens = response.token_count
        return response


class LiteLLMProvider(BaseProvider):
    def __init__(self, model: str, api_key: str, api_base: str | None = None):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.last_tokens = 0

    def _prepare(self):
        import litellm

        litellm.suppress_debug_info = True
        litellm.set_verbose = False
        return litellm

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        try:
            litellm = self._prepare()

            litellm_messages = []
            for m in messages:
                msg = {"role": m.role, "content": m.content}
                if m.tool_calls:
                    msg["tool_calls"] = m.tool_calls
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                litellm_messages.append(msg)

            kwargs: dict = {
                "model": self.model,
                "messages": litellm_messages,
                "api_key": self.api_key,
            }
            if tools:
                kwargs["tools"] = tools
            if self.api_base:
                kwargs["api_base"] = self.api_base

            response = litellm.completion(**kwargs)
            choice = response.choices[0]

            content = choice.message.content or ""
            tool_calls = []
            if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "parameters": tc.function.arguments,
                    }
                    for tc in choice.message.tool_calls
                ]

            tokens = response.usage.total_tokens if response.usage else 0
            self.last_tokens = tokens
            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                token_count=tokens,
            )
        except Exception as e:
            error_msg = _format_litellm_error(e)
            return LLMResponse(
                content=f"❌ {error_msg}",
                error=error_msg,
            )

    def chat_stream(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> Generator[str, None, LLMResponse]:
        try:
            litellm = self._prepare()

            litellm_messages = []
            for m in messages:
                msg = {"role": m.role, "content": m.content}
                if m.tool_calls:
                    msg["tool_calls"] = m.tool_calls
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                litellm_messages.append(msg)

            kwargs = {
                "model": self.model,
                "messages": litellm_messages,
                "stream": True,
                "api_key": self.api_key,
            }
            if tools:
                kwargs["tools"] = tools
            if self.api_base:
                kwargs["api_base"] = self.api_base

            stream = litellm.completion(**kwargs)
            full_content = ""
            total_tokens = 0
            tool_calls_acc = {}

            for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if choice and choice.delta:
                    has_content = bool(choice.delta.content)
                    has_tool_calls = bool(choice.delta.tool_calls)

                    if has_content and not has_tool_calls:
                        full_content += choice.delta.content
                        yield choice.delta.content
                    elif has_content and has_tool_calls:
                        pass
                    elif has_tool_calls:
                        pass

                    if has_tool_calls:
                        for tc in choice.delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc.id or "",
                                    "name": tc.function.name or "",
                                    "arguments": tc.function.arguments or "",
                                }
                            else:
                                entry = tool_calls_acc[idx]
                                if tc.id:
                                    entry["id"] = tc.id
                                if tc.function and tc.function.name:
                                    entry["name"] = tc.function.name
                                if tc.function and tc.function.arguments:
                                    entry["arguments"] += tc.function.arguments
                try:
                    if chunk.usage and chunk.usage.total_tokens:
                        total_tokens = chunk.usage.total_tokens
                except Exception:
                    pass

            tool_calls = [
                {"id": tc["id"], "name": tc["name"], "parameters": tc["arguments"]}
                for tc in sorted(tool_calls_acc.values(), key=lambda x: x["id"])
            ]

            self.last_tokens = total_tokens
            return LLMResponse(
                content=full_content,
                tool_calls=tool_calls,
                token_count=total_tokens,
            )
        except Exception as e:
            error_msg = _format_litellm_error(e)
            yield f"❌ {error_msg}"
            return LLMResponse(content="", error=error_msg)

    def check_connection(self) -> str | None:
        """Test the API key with a minimal request."""
        try:
            litellm = self._prepare()

            litellm_messages = [{"role": "user", "content": "ping"}]
            kwargs = {
                "model": self.model,
                "messages": litellm_messages,
                "max_tokens": 1,
                "api_key": self.api_key,
            }
            if self.api_base:
                kwargs["api_base"] = self.api_base
            litellm.completion(**kwargs)
            return None
        except Exception as e:
            return _format_litellm_error(e)


def _format_litellm_error(e: Exception) -> str:
    """Extract a user-friendly message from a LiteLLM exception."""
    msg = str(e)
    if "AuthenticationError" in type(e).__name__ or "authentication" in msg.lower():
        key_hint = _mask_api_key()
        return (
            f"API key rejected by provider ({key_hint}). "
            "Check with: export MEMORYDOG_API_KEY=sk-..."
        )
    if "RateLimitError" in type(e).__name__ or "rate_limit" in msg.lower():
        return "Rate limited by provider. Wait a moment and try again."
    if "NotFoundError" in type(e).__name__ or "not found" in msg.lower():
        return "Model not found. Check the model name in config."
    if "Timeout" in type(e).__name__ or "timeout" in msg.lower():
        return "Request timed out. Check your network connection."
    return msg[:200]


def _mask_api_key() -> str:
    """Show a hint about the configured API key."""
    try:
        from core.config import load_config

        cfg = load_config()
        key = cfg.provider.api_key
        if not key:
            return "no key set"
        if len(key) < 8:
            return "key too short"
        return f"{key[:4]}...{key[-4:]}"
    except Exception:
        return "unknown"
