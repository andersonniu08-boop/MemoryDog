"""Custom widgets for MemoryDog TUI."""

from textual.reactive import reactive
from textual.widgets import RichLog, Static


class StatusBar(Static):
    """Bottom status bar showing agent state."""

    workspace: reactive[str] = reactive("")
    memory_count: reactive[int] = reactive(0)
    instinct_count: reactive[int] = reactive(0)
    session_time: reactive[str] = reactive("0m")
    model: reactive[str] = reactive("mock")
    tokens: reactive[int] = reactive(0)

    def on_mount(self):
        self.border_title = self._build_text()

    def watch_workspace(self, value: str):
        self.border_title = self._build_text()

    def watch_memory_count(self, value: int):
        self.border_title = self._build_text()

    def _build_text(self) -> str:
        parts = [
            "\U0001f415 Ready",
            f"\u25a1 {self.workspace}",
            f"{self.memory_count} memories",
            f"{self.instinct_count} instincts",
            f"{self.session_time}",
            f"{self.model}",
        ]
        if self.tokens:
            parts.insert(1, f"{self.tokens} tokens")
        return "  |  ".join(parts)


class DogMessage(Static):
    """Dog status message in chrome."""

    def __init__(self, text: str):
        super().__init__()
        self.update(f"\U0001f415 {text}")


class DiffPreview(RichLog):
    """RichLog pane showing file diffs or previews."""

    DEFAULT_CSS = """
    DiffPreview {
        border: solid $primary;
        height: 1fr;
    }
    """

    def show_content(self, title: str, content: str):
        self.clear()
        self.write(f"[bold]{title}[/]")
        self.write(content)


class ToolOutput(RichLog):
    """RichLog pane showing tool execution output."""

    DEFAULT_CSS = """
    ToolOutput {
        border: solid $secondary;
        height: 1fr;
    }
    """

    def show_result(self, tool_name: str, output: str):
        self.clear()
        self.write(f"[bold reverse] $ {tool_name} [/]")
        self.write(output[:2000])
