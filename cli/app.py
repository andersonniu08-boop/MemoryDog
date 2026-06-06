"""Textual application bootstrap."""

from textual.app import App

from cli.ui.chat import ChatScreen
from core.provider import BaseProvider


class MemoryDogApp(App):
    """MemoryDog Textual application."""

    CSS = """
    #main-layout {
        height: 1fr;
    }
    #left-pane {
        width: 2fr;
        height: 100%;
        border: solid $primary;
    }
    #right-pane {
        width: 1fr;
        height: 100%;
        border: solid $primary 40%;
        padding: 0 1;
        display: block;
    }
    #conversation {
        height: 1fr;
        border: none;
    }
    #streaming-response {
        height: auto;
        min-height: 1;
        max-height: 5;
        dock: bottom;
        margin: 0 1;
        color: $text;
        background: $surface;
    }
    #file-preview {
        height: 1fr;
        border: solid $primary 50%;
    }
    #tool-output {
        height: 1fr;
        border: solid $secondary 50%;
    }
    #user-input {
        dock: bottom;
        margin: 1 0;
    }
    StatusBar {
        dock: bottom;
        height: 1;
        background: $panel;
        border-top: solid $primary;
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
