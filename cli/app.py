"""Textual application bootstrap."""
from textual.app import App

from cli.ui.chat import ChatScreen
from core.provider import BaseProvider


class MemoryDogApp(App):
    """MemoryDog Textual application."""

    CSS = """
    #conversation {
        height: 1fr;
        border: solid $primary;
        padding: 1;
    }
    #user-input {
        dock: bottom;
        margin: 1 0 0 0;
    }
    StatusBar {
        dock: bottom;
        height: 1;
        background: $panel;
    }
    """

    def __init__(
        self,
        workspace: str = ".",
        provider: BaseProvider | None = None,
        model_name: str = "mock",
    ):
        super().__init__()
        self.workspace = workspace
        self.provider = provider
        self.model_name = model_name

    def on_mount(self):
        self.push_screen(
            ChatScreen(
                workspace=self.workspace,
                provider=self.provider,
                model_name=self.model_name,
            )
        )
