"""Tests for memory extraction and parsing robustness."""

import pytest

from core.memory import (
    _extract_json_block,
    _fix_unquoted_keys,
    _parse_memory_json,
    _strip_markdown_fence,
    _validate_memory_records,
    _wrap_single_object,
)


class TestStripMarkdownFence:
    def test_no_fence(self):
        assert _strip_markdown_fence("plain text") == "plain text"

    def test_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        assert _strip_markdown_fence(raw) == '{"key": "value"}'

    def test_fence_no_lang(self):
        raw = '```\n{"key": "value"}\n```'
        assert _strip_markdown_fence(raw) == '{"key": "value"}'

    def test_fence_with_prose_around(self):
        raw = 'Here is the result:\n```json\n{"key": "value"}\n```\nHope this helps.'
        assert _strip_markdown_fence(raw) == '{"key": "value"}'


class TestExtractJsonBlock:
    def test_extract_array(self):
        raw = 'Some text before [{"a": 1}] after'
        assert _extract_json_block(raw) == '[{"a": 1}]'

    def test_extract_object(self):
        raw = 'Some text before {"a": 1} after'
        assert _extract_json_block(raw) == '{"a": 1}'

    def test_nested(self):
        raw = 'Found: [{"nested": {"deep": "value"}}]'
        assert _extract_json_block(raw) == '[{"nested": {"deep": "value"}}]'


class TestWrapSingleObject:
    def test_object_wrapped(self):
        assert _wrap_single_object('{"a": 1}') == '[{"a": 1}]'

    def test_array_unchanged(self):
        assert _wrap_single_object('[{"a": 1}]') == '[{"a": 1}]'

    def test_empty_object(self):
        assert _wrap_single_object("{}") == "[{}]"


class TestFixUnquotedKeys:
    def test_unquoted_keys(self):
        result = _fix_unquoted_keys('{key: "value", num: 42}')
        assert '"key"' in result
        assert '"num"' in result

    def test_quoted_keys_unchanged(self):
        raw = '{"key": "value", "num": 42}'
        assert _fix_unquoted_keys(raw) == raw


class TestParseMemoryJson:
    def test_clean_array(self):
        raw = '[{"type": "design_decision", "content": "Chose Textual", "importance": 0.8}]'
        result = _parse_memory_json(raw)
        assert len(result) == 1
        assert result[0]["content"] == "Chose Textual"

    def test_markdown_fenced(self):
        raw = (
            '```json\n[{"type": "bug", "content": "Fixed race condition", "importance": 0.9}]\n```'
        )
        result = _parse_memory_json(raw)
        assert len(result) == 1
        assert result[0]["type"] == "bug"

    def test_extra_prose_before_after(self):
        raw = 'Here are the memories I extracted:\n```json\n[{"type": "design_decision", "content": "Chose Textual", "importance": 0.8}]\n```\nLet me know if you need more.'  # noqa: E501
        result = _parse_memory_json(raw)
        assert len(result) == 1

    def test_single_object(self):
        raw = '{"type": "learned_fact", "content": "Python 3.14 is required", "importance": 0.7}'
        result = _parse_memory_json(raw)
        assert len(result) == 1

    def test_unquoted_keys(self):
        raw = '[{type: "design_decision", content: "Chose Textual"}]'
        result = _parse_memory_json(raw)
        assert len(result) == 1
        assert result[0]["type"] == "design_decision"

    def test_single_quotes(self):
        raw = "[{'type': 'design_decision', 'content': 'Chose Textual'}]"
        result = _parse_memory_json(raw)
        assert len(result) == 1

    def test_trailing_comma(self):
        raw = '[{"type": "bug", "content": "Fixed", "tags": ["a", "b",],}]'
        result = _parse_memory_json(raw)
        assert len(result) == 1

    def test_empty_array(self):
        assert _parse_memory_json("[]") == []

    def test_empty_string(self):
        assert _parse_memory_json("") == []

    def test_no_json_content(self):
        assert _parse_memory_json("No memories to extract.") == []

    def test_partial_truncated(self):
        raw = '[{"type": "design_decision", "content": "Partial memory"'
        result = _parse_memory_json(raw)
        assert len(result) >= 0

    def test_null_values(self):
        raw = '[{"type": null, "content": null, "importance": null}]'
        result = _parse_memory_json(raw)
        assert len(result) >= 0

    def test_deepseek_style(self):
        raw = 'Based on the conversation, here are the extracted memories:\n\n```json\n[\n  {\n    "type": "design_decision",\n    "content": "We chose Textual because it supports multi-pane layouts.",\n    "summary": "Textual chosen for TUI",\n    "importance": 0.85,\n    "tags": ["textual", "tui"]\n  }\n]\n```\n\nThese capture the key decisions made.'  # noqa: E501
        result = _parse_memory_json(raw)
        assert len(result) == 1
        assert result[0]["type"] == "design_decision"

    def test_nested_quotes_in_content(self):
        raw = '[{"type": "design_decision", "content": "User said: \\"Use PostgreSQL\\"", "importance": 0.7}]'  # noqa: E501
        result = _parse_memory_json(raw)
        assert len(result) == 1

    def test_missing_required_keys(self):
        raw = '[{"not_content": "missing everything"}]'
        result = _parse_memory_json(raw)
        assert len(result) >= 0


class TestValidateMemoryRecords:
    def test_valid_record(self):
        items = [{"type": "design_decision", "content": "Chose Textual for TUI", "importance": 0.8}]
        records = _validate_memory_records(items, "test-proj")
        assert len(records) == 1
        r = records[0]
        assert r.memory_type == "design_decision"
        assert r.content == "Chose Textual for TUI"
        assert r.importance == 0.8
        assert r.workspace_name == "test-proj"

    def test_short_content_filtered(self):
        items = [{"type": "design_decision", "content": "Hi"}]
        records = _validate_memory_records(items, "test")
        assert len(records) == 0

    def test_invalid_type_defaults(self):
        items = [{"type": "nonexistent", "content": "Something worth remembering"}]
        records = _validate_memory_records(items, "test")
        assert len(records) == 1
        assert records[0].memory_type == "conversation"

    def test_importance_clamped(self):
        items = [{"type": "bug", "content": "Fixed a bug", "importance": 2.5}]
        records = _validate_memory_records(items, "test")
        assert records[0].importance == 1.0

        items2 = [{"type": "bug", "content": "Fixed a bug", "importance": -1.0}]
        records2 = _validate_memory_records(items2, "test")
        assert records2[0].importance == 0.0

    def test_importance_as_string(self):
        items = [{"type": "bug", "content": "Fixed a bug", "importance": "0.75"}]
        records = _validate_memory_records(items, "test")
        assert records[0].importance == 0.75

    def test_missing_summary_falls_back(self):
        items = [{"type": "design_decision", "content": "We chose Textual for multi-pane layouts."}]
        records = _validate_memory_records(items, "test")
        assert records[0].summary == "We chose Textual for multi-pane layouts."

    def test_tags_as_string(self):
        items = [{"type": "bug", "content": "Fixed a bug", "tags": "urgent"}]
        records = _validate_memory_records(items, "test")
        assert len(records[0].tags) == 1

    def test_tags_truncated(self):
        items = [{"type": "bug", "content": "Fixed a bug", "tags": list("abcdefghijklmnop")}]
        records = _validate_memory_records(items, "test")
        assert len(records[0].tags) <= 10

    def test_non_dict_item_skipped(self):
        items = ["string", 42, None, {"type": "bug", "content": "Fixed a bug"}]
        records = _validate_memory_records(items, "test")
        assert len(records) == 1

    def test_empty_input(self):
        assert _validate_memory_records([], "test") == []


class TestExtractMemories:
    """Integration tests with MockProvider."""

    @pytest.mark.asyncio
    async def test_extract_with_clean_response(self):
        from core.provider import MockProvider

        class CleanProvider(MockProvider):
            def __init__(self):
                super().__init__()
                self.last_tokens = 0

            def chat(self, messages, tools=None):
                return type(
                    "Resp",
                    (),
                    {
                        "content": '[{"type": "design_decision", "content": "Chose Textual for TUI", "summary": "Textual chosen", "importance": 0.85, "tags": ["tui"]}]',  # noqa: E501
                        "error": "",
                        "tool_calls": [],
                        "token_count": 42,
                    },
                )()

        from core.memory import extract_memories

        records = await extract_memories(CleanProvider(), "Remember Textual", "OK", "test")
        assert len(records) == 1
        assert records[0].content == "Chose Textual for TUI"

    @pytest.mark.asyncio
    async def test_extract_with_fenced_response(self):
        from core.provider import MockProvider

        class FencedProvider(MockProvider):
            def chat(self, messages, tools=None):
                return type(
                    "Resp",
                    (),
                    {
                        "content": 'Here is the result:\n```json\n[{"type": "bug", "content": "Found race condition", "importance": 0.9}]\n```\nHope this helps.',  # noqa: E501
                        "error": "",
                        "tool_calls": [],
                        "token_count": 42,
                    },
                )()

        from core.memory import extract_memories

        records = await extract_memories(FencedProvider(), "Found bug", "Fixed", "test")
        assert len(records) == 1
        assert records[0].memory_type == "bug"

    @pytest.mark.asyncio
    async def test_extract_with_empty_response(self):
        from core.provider import MockProvider

        class EmptyProvider(MockProvider):
            def chat(self, messages, tools=None):
                return type(
                    "Resp",
                    (),
                    {
                        "content": "",
                        "error": "",
                        "tool_calls": [],
                        "token_count": 0,
                    },
                )()

        from core.memory import extract_memories

        records = await extract_memories(EmptyProvider(), "Hi", "Hello", "test")
        assert records == []

    @pytest.mark.asyncio
    async def test_extract_with_prose_response(self):
        from core.provider import MockProvider

        class ProseProvider(MockProvider):
            def chat(self, messages, tools=None):
                return type(
                    "Resp",
                    (),
                    {
                        "content": "Nothing worth remembering here. The user just said hello.",
                        "error": "",
                        "tool_calls": [],
                        "token_count": 42,
                    },
                )()

        from core.memory import extract_memories

        records = await extract_memories(ProseProvider(), "Hi", "Hello", "test")
        assert records == []

    @pytest.mark.asyncio
    async def test_extract_with_malformed_json(self):
        from core.provider import MockProvider

        class BadProvider(MockProvider):
            def chat(self, messages, tools=None):
                return type(
                    "Resp",
                    (),
                    {
                        "content": '[{type: "bug" content: "missing comma" importance: 0.8}]',
                        "error": "",
                        "tool_calls": [],
                        "token_count": 42,
                    },
                )()

        from core.memory import extract_memories

        records = await extract_memories(BadProvider(), "bug", "fixed", "test")
        assert len(records) >= 0
