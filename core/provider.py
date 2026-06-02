"""LLM provider abstraction."""
from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass, field


@dataclass
class Message:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    token_count: int = 0


class BaseProvider(ABC):
    @abstractmethod
    def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> LLMResponse:
        ...

    def chat_stream(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> Generator[str, None, LLMResponse]:
        response = self.chat(messages, tools)
        yield response.content
        return response


class MockProvider(BaseProvider):
    def __init__(self):
        self.last_tokens = 0

    def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> LLMResponse:
        last = messages[-1].content.lower()
        if "hello" in last or "hi" in last:
            response = LLMResponse(
                content="Hello! I'm MemoryDog. How can I help you today?"
            )
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

    def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> LLMResponse:
        import litellm

        litellm_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        kwargs = {"model": self.model, "messages": litellm_messages}
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

    def chat_stream(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> Generator[str, None, LLMResponse]:
        import litellm

        litellm_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        kwargs = {"model": self.model, "messages": litellm_messages, "stream": True}
        if tools:
            kwargs["tools"] = tools
        if self.api_base:
            kwargs["api_base"] = self.api_base

        stream = litellm.completion(**kwargs)
        full_content = ""
        total_tokens = 0

        for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice and choice.delta and choice.delta.content:
                full_content += choice.delta.content
                yield choice.delta.content
            if chunk.usage and chunk.usage.total_tokens:
                total_tokens = chunk.usage.total_tokens

        return LLMResponse(content=full_content, token_count=total_tokens)
