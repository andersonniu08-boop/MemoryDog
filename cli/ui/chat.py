"""Chat screen — multi-pane layout with conversation, file preview, tool output."""

import json
import os
import time

from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Header, Input, RichLog, Static

from cli.ui.widgets import DiffPreview, PlanPanel, StatusBar, ToolOutput
from core.provider import BaseProvider, MockProvider


class ChatScreen(Screen):
    """Main chat interface with multi-pane layout."""

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+s", "focus_input", "Focus Input"),
        ("ctrl+p", "toggle_panels", "Toggle Panels"),
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
        self.state = None
        self.total_tokens = 0
        ws_name = os.path.basename(os.path.abspath(workspace)) or workspace
        self.status = StatusBar()
        self.status.workspace = ws_name
        self.status.model = model_name
        self._panels_visible = True

    def compose(self):
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            with Container(id="left-pane"):
                yield PlanPanel(id="plan-panel")
                yield RichLog(id="conversation", highlight=True, markup=True, wrap=True)
                yield Static(id="streaming-response")
            with Container(id="right-pane"):
                yield DiffPreview(id="file-preview")
                yield ToolOutput(id="tool-output")
        yield self.status
        yield Input(placeholder="> Type your message...", id="user-input")

    async def on_mount(self):
        from core.agent_loop import init_agent

        try:
            self.state = await init_agent(self.workspace)
        except Exception:
            self.state = None

        conv = self.query_one("#conversation", RichLog)
        conv.write("[bold blue]🐕 MemoryDog ready.[/]")

        if self.state and self.state.workspace:
            try:
                from core.memory import count_memories

                count = await count_memories(self.state.workspace)
                if count > 0:
                    conv.write(
                        f"[dim]🐕 I remember this project."
                        f" {count} memories from previous sessions.[/]"
                    )
                    self.status.memory_count = count
            except Exception:
                pass

        self._show_status(conv)

        self.query_one("#user-input", Input).focus()
        self._start_time = time.time()
        self.set_interval(30, self._update_session_time)

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        event.input.disabled = True

        conv = self.query_one("#conversation", RichLog)
        conv.write(f"[bold blue]You:[/] {text}")
        conv.scroll_end()

        if self.state is None:
            from core.agent_loop import AgentState

            self.provider = self.provider or MockProvider()
            ws = os.path.basename(os.path.abspath(self.workspace)) or self.workspace
            self.state = AgentState(workspace=ws)
            response = self.provider.chat([])
            conv.write(f"[bold green]MemoryDog:[/] {response.content}")
            event.input.disabled = False
            event.input.focus()
            return

        from core.agent_loop import run_turn

        # Show initial status
        conv.write("[dim italic]🐕 Running...[/]")
        conv.scroll_end()

        # Streaming response widget at bottom of left pane
        stream_widget = self.query_one("#streaming-response", Static)
        stream_widget.update("[bold green]MemoryDog:[/]")

        response_parts = []

        def on_status(msg: str):
            conv.write(f"[dim]🐕 {msg}[/]")
            conv.scroll_end()

        def on_token(token: str):
            response_parts.append(token)
            full = "".join(response_parts)
            stream_widget.update(f"[bold green]MemoryDog:[/] {full}")

        response_text = await run_turn(
            self.provider,
            self.state,
            text,
            on_status=on_status,
            on_token=on_token,
        )

        # Clear streaming widget and show final response in conversation
        stream_widget.update("")
        conv.write(f"[bold green]MemoryDog:[/] {response_text}")

        self.total_tokens += self.provider.last_tokens
        self.status.tokens = self.total_tokens
        await self._refresh_memory_counts()
        await self._update_preview(conv)

        conv.scroll_end()
        event.input.disabled = False
        event.input.focus()

    def _maybe_show_plan(self, conv: RichLog, text: str):
        try:
            if "```json" in text:
                start = text.index("```json") + 7
                end = text.index("```", start)
                plan_data = json.loads(text[start:end])
                if "plan" in plan_data:
                    steps = plan_data["plan"]
                    self.query_one("#plan-panel", PlanPanel).show_plan(steps)
        except (ValueError, json.JSONDecodeError, KeyError):
            pass

    async def _update_preview(self, conv: RichLog):

        if not self.state or not self.state.history:
            return

        last_tool = None
        for msg in reversed(self.state.history):
            if msg.role == "tool":
                last_tool = msg.content
                break

        if last_tool:
            try:
                results = json.loads(last_tool)
                if not isinstance(results, list):
                    results = [results]
                for item in results:
                    res = item.get("result", item)
                    if isinstance(res, dict) and res.get("success"):
                        if "content" in res:
                            preview = self.query_one("#file-preview", DiffPreview)
                            preview.show_content(
                                "File Content",
                                res["content"][:1000],
                            )
                        elif "stdout" in res and res["stdout"].strip():
                            output = self.query_one("#tool-output", ToolOutput)
                            output.show_result("command", res["stdout"])
            except Exception:
                pass

    def _show_status(self, conv: RichLog):
        if self.state and self.state.active_instincts:
            names = ", ".join(self.state.active_instincts)
            conv.write(f"[dim]🐕 Instincts active: {names}[/]")
        conv.write(f"[dim]🐕 Model: {self.model_name}[/]")

    async def _refresh_memory_counts(self):
        try:
            from core.memory import count_instinct_activations, count_memories

            self.status.memory_count = await count_memories(self.state.workspace)
            self.status.instinct_count = await count_instinct_activations()
        except Exception:
            pass

    def action_focus_input(self):
        self.query_one("#user-input", Input).focus()

    def action_toggle_panels(self):
        right = self.query_one("#right-pane", Container)
        right.display = not right.display
        self._panels_visible = not self._panels_visible

    def _update_session_time(self):
        elapsed = int(time.time() - self._start_time)
        minutes = elapsed // 60
        self.status.session_time = f"{minutes}m"
