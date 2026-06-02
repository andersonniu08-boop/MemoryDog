"""Chat screen — conversation pane, input, status bar."""
import os

from textual.screen import Screen
from textual.widgets import Header, Input, RichLog

from cli.ui.widgets import StatusBar
from core.agent_loop import AgentState, run_turn
from core.provider import BaseProvider, MockProvider


class ChatScreen(Screen):
    """Main chat interface."""

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+s", "focus_input", "Focus Input"),
    ]

    def __init__(
        self,
        workspace: str = ".",
        provider: BaseProvider | None = None,
        model_name: str = "mock",
    ):
        super().__init__()
        self.workspace = workspace
        self.provider = provider or MockProvider()
        self.model_name = model_name
        self.state = AgentState(workspace=workspace)
        self.total_tokens = 0
        ws_name = os.path.basename(os.path.abspath(workspace)) or workspace
        self.status = StatusBar()
        self.status.workspace = ws_name
        self.status.model = model_name

    def compose(self):
        yield Header(show_clock=True)
        yield RichLog(id="conversation", highlight=True, markup=True, wrap=True)
        yield self.status
        yield Input(placeholder="> Type your message...", id="user-input")

    def on_mount(self):
        self.query_one("#user-input", Input).focus()
        conv = self.query_one("#conversation", RichLog)
        conv.write("[bold blue]🐕 MemoryDog ready.[/]")

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        event.input.disabled = True

        conv = self.query_one("#conversation", RichLog)
        conv.write(f"[bold blue]You:[/] {text}")

        response_text = run_turn(self.provider, self.state, text)
        conv.write(f"[bold green]MemoryDog:[/] {response_text}")

        self.total_tokens += self.provider.last_tokens
        conv.scroll_end()
        event.input.disabled = False
        event.input.focus()

    def action_focus_input(self):
        self.query_one("#user-input", Input).focus()
