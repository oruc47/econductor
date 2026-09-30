from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from threading import Event

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Markdown, Static, TextArea

from econductor.agent import Agent
from econductor.config import Settings, data_dir
from econductor.inference import MLXInference
from econductor.models import PRESETS, local_model, model_rows
from econductor.security import PathGuard, safe_environment, stop_process
from econductor.sessions import Session
from econductor.types import Permission


class Composer(TextArea):
    BINDINGS = [
        Binding("enter", "submit", "Send", priority=True),
        Binding("shift+enter", "newline", "New line", priority=True),
        Binding("ctrl+j", "newline", "New line", priority=True),
    ]

    class Submitted(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    def action_submit(self) -> None:
        if self.text.strip():
            self.post_message(self.Submitted(self.text.strip()))

    def action_newline(self) -> None:
        self.insert("\n")


class Picker(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss_none", "Cancel", priority=True)]

    def __init__(self, title: str, choices: list[tuple[str, str]]):
        super().__init__()
        self.title_text, self.choices = title, choices

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label(self.title_text)
            yield ListView(
                *(ListItem(Label(description, markup=False)) for _, description in self.choices)
            )
            yield Label("↑↓ select · Enter confirm · Escape cancel", classes="muted")

    def on_mount(self) -> None:
        self.query_one(ListView).focus()

    @on(ListView.Selected)
    def selected(self, event: ListView.Selected) -> None:
        index = self.query_one(ListView).index
        if index is not None:
            self.dismiss(self.choices[index][0])

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class Approval(ModalScreen[str]):
    BINDINGS = [Binding("escape", "deny", "Deny", priority=True)]

    def __init__(self, title: str, preview: str):
        super().__init__()
        self.title_text, self.preview = title, preview

    def compose(self) -> ComposeResult:
        with Vertical(id="approval"):
            yield Label(f"Approve {self.title_text}?")
            yield TextArea(self.preview, read_only=True, show_line_numbers=True, id="preview")
            yield Button("Approve once", id="yes", variant="primary")
            yield Button("Auto approve this session", id="session")
            yield Button("Deny", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "no")

    def action_deny(self) -> None:
        self.dismiss("no")


HELP = """**Commands**

`/model` choose/download a local model · `/permissions` approve, read-only, autonomous

`/new` new session · `/resume` reopen a transcript · `/status` project and model status

`/help` commands · `/quit` exit

Enter sends · Shift+Enter / Ctrl+J adds a line · Escape cancels

Try: “Inspect my datasets and suggest a cleaning plan.”

Inference and analysis stay offline. Only selecting an uncached model initiates a download.
Approval dialogs can enable auto approval for the current session; it resets on /new, /resume, or restart.
"""


class EconductorApp(App):
    TITLE = "Econductor"
    CSS = """
    Screen { background: $surface; }
    #conversation { height: 1fr; padding: 1 2; }
    .message { margin-bottom: 1; }
    .user { color: $text; background: $panel; padding: 1; }
    .muted { color: $text-muted; }
    #activity { height: 1; padding: 0 2; color: $text-muted; }
    #composer { height: 5; margin: 0 2; border: round $primary; }
    #status { height: 1; padding: 0 2; color: $text-muted; }
    Picker, Approval { align: center middle; background: $background 75%; }
    #picker { width: 90%; max-width: 110; height: auto; max-height: 80%; padding: 1 2; border: round $primary; background: $surface; }
    #picker ListView { height: auto; max-height: 24; margin: 1 0; }
    #picker ListItem { padding: 1 0; }
    #approval { width: 90%; height: 85%; padding: 1 2; border: round $primary; background: $surface; }
    #preview { height: 1fr; margin: 1 0; }
    #approval Button { width: 100%; margin-top: 1; }
    """
    BINDINGS = [
        Binding("escape", "cancel_operation", "Cancel", priority=True),
        Binding("ctrl+c", "quit_safely", "Quit", priority=True),
    ]

    def __init__(self, guard: PathGuard, settings: Settings, *, agent: Agent | None = None):
        super().__init__()
        self.guard, self.settings = guard, settings
        self.agent = agent or Agent(guard, settings)
        self.busy = False
        self.preview_widget: Markdown | None = None
        self.streaming_text = ""
        self.download_process: subprocess.Popen | None = None
        self.cancel_requested = Event()
        self.approval_wait: Event | None = None
        self.guard.initialize()

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="conversation")
        yield Static("", id="activity", markup=False)
        yield Composer(id="composer", soft_wrap=True)
        yield Static("", id="status", markup=False)

    def on_mount(self) -> None:
        self.add_message(
            "Econductor",
            "Local economics coding. Type `/model` to choose a model, or `/help` for commands.",
        )
        path = local_model(self.settings.model)
        if path:
            self.agent.inference = MLXInference(
                path, self.guard.state / "work" / "inference", self.settings
            )
        self.update_status()
        self.query_one(Composer).focus()

    def add_message(self, role: str, content: str, *, plain: bool = False):
        view = self.query_one("#conversation", VerticalScroll)
        widget = (
            Static(f"{role}\n{content}", markup=False, classes="message user")
            if plain
            else Markdown(f"**{role}**\n\n{content}", classes="message")
        )
        view.mount(widget)
        view.scroll_end(animate=False)
        return widget

    def update_status(self) -> None:
        ready = "local" if self.agent.inference else "select /model"
        permission = "auto-approve session" if self.agent.auto_approve_session else self.agent.permission
        self.query_one("#status", Static).update(
            f"{self.settings.model} · {ready} · {permission} · {self.agent.tokens:,}/{self.settings.context_tokens:,} tokens · Enter send / Shift+Enter newline"
        )

    def event(self, kind: str, value) -> None:
        if kind == "activity":
            self.query_one("#activity", Static).update(value)
        elif kind == "begin":
            self.streaming_text = ""
            self.preview_widget = self.add_message("Econductor", "…")
        elif kind == "text":
            self.streaming_text += value
            if self.preview_widget:
                self.preview_widget.update("**Econductor**\n\n" + self.streaming_text)
        elif kind == "answer":
            if self.preview_widget:
                self.preview_widget.update("**Econductor**\n\n" + value)
        elif kind == "tool":
            name, result = value
            status = "✓" if result.ok else "×"
            self.add_message(f"{status} {name}", result.summary[:3500], plain=True)
        self.update_status()

    @on(Composer.Submitted)
    def submitted(self, event: Composer.Submitted) -> None:
        if self.busy:
            self.notify("An operation is running. Escape cancels it.")
            return
        self.query_one(Composer).clear()
        if event.text.startswith("/"):
            self.command(event.text)
        else:
            self.add_message("You", event.text, plain=True)
            self.busy = True
            self.turn(event.text)

    def command(self, text: str) -> None:
        command, _, argument = text.partition(" ")
        if command == "/model":
            if argument:
                self.select_model(argument.strip())
            else:
                choices = []
                for row in model_rows():
                    available = (
                        "downloaded"
                        if row["downloaded"]
                        else f"download ≈{row['estimated_gb']:g} GB"
                    )
                    memory = (
                        f"RAM {row['minimum_ram_gb']}/{row['comfortable_ram_gb']} GB min/comfortable · {row['memory_fit']}"
                        if row["minimum_ram_gb"]
                        else "RAM fit unknown"
                    )
                    disk = " · low disk" if row["disk_fit"] == "low disk" else ""
                    choices.append(
                        (
                            row["id"],
                            f"{row['id']} · {available} · {memory}{disk} · {row['description']}",
                        )
                    )
                self.push_screen(
                    Picker(
                        "Choose a model · fit is an estimate; selecting an uncached preset downloads it", choices
                    ),
                    self.select_model,
                )
        elif command == "/permissions":
            if argument:
                self.set_permission(argument.strip())
            else:
                self.push_screen(
                    Picker(
                        "Execution permissions · all modes remain offline",
                        [
                            (
                                "approve",
                                "Approve · review edits and generated code before execution",
                            ),
                            (
                                "read-only",
                                "Read-only · inspect files/data/documents; no generated code or SQL",
                            ),
                            (
                                "autonomous",
                                "Autonomous · isolated execution and new code; overwrites still ask",
                            ),
                        ],
                    ),
                    self.set_permission,
                )
        elif command == "/new":
            self.agent.save()
            self.agent.session = Session(model=self.settings.model)
            self.agent.auto_approve_session = False
            self.agent.tokens = 0
            self.add_message("Econductor", "New session started. Previous transcript saved.")
        elif command == "/resume":
            paths = sorted(
                (self.guard.state / "sessions").glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:30]
            if paths:
                choices = []
                for path in paths:
                    try:
                        session = Session.load(path)
                        title = next(
                            (m["content"] for m in session.messages if m["role"] == "user"),
                            "Empty session",
                        )[:65].replace("\n", " ")
                        choices.append((session.id, f"{session.id} · {title}"))
                    except (ValueError, TypeError):
                        continue
                self.push_screen(Picker("Resume session", choices), self.resume)
            else:
                self.notify("No saved sessions yet.")
        elif command == "/status":
            permission = (
                "auto-approve session"
                if self.agent.auto_approve_session
                else str(self.agent.permission)
            )
            self.add_message(
                "Econductor",
                f"Project: `{self.guard.project}`\n\nSession: `{self.agent.session.id}`\n\nModel: `{self.settings.model}` · permissions: `{permission}`\n\nState and artifacts: `{self.guard.state}`\n\nInference/analysis offline. Model evaluation: {next((r['validation'] for r in model_rows() if r['id'] == self.settings.model), 'not evaluated')}.",
            )
        elif command == "/help":
            self.add_message("Econductor", HELP)
        elif command == "/quit":
            self.action_quit_safely()
        else:
            self.notify("Unknown command. Use /help.")
        self.update_status()

    def set_permission(self, value: str | None) -> None:
        if value:
            try:
                self.agent.permission = Permission(value)
                self.agent.auto_approve_session = False
                self.add_message(
                    "Econductor",
                    f"Permissions changed to **{value}**. Inference and execution remain offline.",
                )
            except ValueError:
                self.notify("Choose approve, read-only, or autonomous.")
            self.update_status()

    def resume(self, value: str | None) -> None:
        if not value:
            return
        self.agent.save()
        self.agent.session = Session.load(self.guard.state / "sessions" / f"{value}.json")
        self.agent.session.model = self.settings.model
        self.agent.permission = Permission.APPROVE
        self.agent.auto_approve_session = False
        self.agent.tokens = 0
        self.add_message(
            "Econductor",
            f"Resumed `{value}`. Permissions reset to approve; current model retained.",
        )
        for message in self.agent.session.messages[-12:]:
            if message["role"] in {"user", "assistant"} and message.get("content"):
                self.add_message(
                    "You" if message["role"] == "user" else "Econductor",
                    message["content"],
                    plain=message["role"] == "user",
                )
        self.update_status()

    def select_model(self, value: str | None) -> None:
        if not value:
            return
        if value not in {r["id"] for r in model_rows()}:
            self.notify("Unknown model. Use /model or 'models add'.")
            return
        self.busy = True
        self.cancel_requested.clear()
        self.switch_model(value)

    def approve(self, name: str, preview: str) -> bool:
        if self.agent.auto_approve_session:
            return True
        gate = Event()
        self.approval_wait = gate
        result = ["no"]

        def resolved(value: str) -> None:
            result[0] = value
            gate.set()

        self.call_from_thread(self.push_screen, Approval(name, preview), resolved)
        while not gate.wait(0.1):
            if self.agent.cancelled.is_set():
                self.approval_wait = None
                return False
        self.approval_wait = None
        if self.agent.cancelled.is_set():
            return False
        if result[0] == "session":
            self.agent.auto_approve_session = True
            self.call_from_thread(
                self.add_message,
                "Econductor",
                "Auto approval enabled for this session. `/new`, `/resume`, or restart resets it.",
            )
            self.call_from_thread(self.update_status)
        return result[0] in {"yes", "session"}

    @work(thread=True, exclusive=True, group="operation")
    def turn(self, prompt: str) -> None:
        try:
            self.agent.turn(
                prompt,
                lambda kind, value: self.call_from_thread(self.event, kind, value),
                self.approve,
            )
        except Exception as error:
            self.call_from_thread(self.add_message, "Econductor", f"Stopped: {error}")
        finally:
            self.call_from_thread(self.finished)

    @work(thread=True, exclusive=True, group="operation")
    def switch_model(self, value: str) -> None:
        old = self.agent.inference
        candidate = None
        try:
            path = local_model(value)
            if path is None:
                if value not in PRESETS:
                    raise ValueError("Local model files are incomplete. Register a valid folder.")
                self.call_from_thread(
                    self.event, "activity", f"Downloading {value}… Escape cancels. Analysis paused."
                )
                helper_work = data_dir() / "download-work"
                helper_work.mkdir(parents=True, exist_ok=True, mode=0o700)
                env = safe_environment(helper_work)
                env["HOME"] = str(Path.home())
                env["ECONDUCTOR_HOME"] = str(data_dir())
                env["HF_HUB_OFFLINE"] = "0"
                log = helper_work / "download.log"
                with log.open("w") as output:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "econductor.download_worker", value],
                        cwd=helper_work,
                        env=env,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    self.download_process = process
                    while process.poll() is None:
                        if self.cancel_requested.wait(0.2):
                            stop_process(process)
                            raise RuntimeError(
                                "Download cancelled. Selecting the model again resumes it."
                            )
                        with log.open(errors="replace") as handle:
                            handle.seek(max(0, log.stat().st_size - 2000))
                            progress = handle.read().replace("\r", "\n").splitlines()
                        if progress:
                            self.call_from_thread(
                                self.event, "activity", f"{value}: {progress[-1][-180:]}"
                            )
                    self.download_process = None
                    if process.returncode != 0:
                        raise RuntimeError(log.read_text(errors="replace")[-1500:])
                path = local_model(value)
                if path is None:
                    raise RuntimeError("Downloaded model did not pass validation.")
            if self.cancel_requested.is_set():
                raise RuntimeError("Model switch cancelled.")
            self.call_from_thread(self.event, "activity", f"Loading {value} locally…")
            if old:
                old.unload()
            candidate = MLXInference(path, self.guard.state / "work" / "candidate", self.settings)
            # Expose candidate for cancellation during model loading.
            self.agent.inference = candidate
            candidate.start()
            if self.cancel_requested.is_set():
                raise RuntimeError("Model switch cancelled.")
            self.agent.inference = candidate
            self.settings.model = value
            self.settings.save()
            self.agent.session.model = value
            self.agent.tokens = 0
            self.agent.save()
            self.call_from_thread(
                self.add_message,
                "Econductor",
                f"Model changed to **{value}**. Conversation retained; context is recalculated on the next prompt.",
            )
        except Exception as error:
            if candidate:
                candidate.unload()
            self.agent.inference = old
            self.call_from_thread(
                self.add_message,
                "Econductor",
                f"Model switch failed: {error}\n\nPrevious selection retained; it reloads when needed.",
            )
        finally:
            self.download_process = None
            self.call_from_thread(self.finished)

    def finished(self) -> None:
        self.busy = False
        self.query_one("#activity", Static).update("")
        self.update_status()
        self.query_one(Composer).focus()

    def action_cancel_operation(self) -> None:
        if self.busy:
            self.cancel_requested.set()
            self.agent.cancel()
            if self.download_process:
                stop_process(self.download_process)
            if isinstance(self.screen, Approval):
                self.screen.dismiss("no")
            self.notify("Cancellation requested; partial artifacts remain saved.")

    def action_quit_safely(self) -> None:
        self.action_cancel_operation()
        self.agent.save()
        if self.agent.inference:
            self.agent.inference.unload()
        self.exit()

    def on_unmount(self) -> None:
        self.agent.cancel()
