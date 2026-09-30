from __future__ import annotations

import json
import platform
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from econductor.config import Settings, data_dir, find_rscript, find_stata, hardware
from econductor.models import PRESETS, download, model_rows, register, save_token
from econductor.security import PathGuard, check_sandbox

app = typer.Typer(help="Econductor — local economics coding", no_args_is_help=False)
models_app = typer.Typer(help="Explicit model management")
sessions_app = typer.Typer(help="Local transcript management")
app.add_typer(models_app, name="models")
app.add_typer(sessions_app, name="sessions")
console = Console()


def error(message: str) -> None:
    console.print(message, style="red", markup=False)
    raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        chat(Path.cwd())


@app.command()
def setup() -> None:
    """Configure this Mac; no model weights are downloaded."""
    info = hardware()
    console.print("Econductor setup", style="bold")
    console.print(json.dumps(info, indent=2), markup=False)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        error("V1 requires an Apple Silicon Mac.")
    try:
        if int(platform.mac_ver()[0].split(".")[0]) < 15:
            error("Econductor v1 requires macOS 15 or newer for MLX memory support.")
    except ValueError:
        error("Could not determine the macOS version.")
    settings = Settings.load()
    console.print(
        f"Python analysis: ready · Rscript: {find_rscript() or 'not installed'}", markup=False
    )
    detected = find_stata(settings.stata_path)
    console.print(f"Stata: {detected or 'not found (optional; license required)'}", markup=False)
    if Confirm.ask("Configure a Stata executable path?", default=False):
        chosen = Prompt.ask("Executable path (press Enter to keep the detected path)", default=detected or "").strip()
        if chosen.lower() not in {"", "n", "no"}:
            found = find_stata(chosen)
            if not found:
                error("Executable not found; Stata configuration unchanged.")
            settings.stata_path = found
    if Confirm.ask(
        "Log in to Hugging Face? Public presets usually do not need a token", default=False
    ):
        token = Prompt.ask("Hugging Face read token", password=True).strip()
        try:
            from huggingface_hub import HfApi

            account = HfApi(token=token).whoami()
            save_token(token)
            console.print(
                f"Logged in as {account.get('name', 'Hugging Face user')}; token saved in Keychain.",
                markup=False,
            )
        except Exception as exc:
            error(f"Login failed ({type(exc).__name__}); no token written.")
    ok, reason = check_sandbox()
    console.print(f"Sandbox: {reason}", markup=False)
    if not ok:
        error(
            "Offline enforcement unavailable: "
            f"{reason}. Setup was not saved and execution remains disabled. "
            "Run ./bootstrap.sh from a Terminal.app window outside an IDE/agent sandbox, "
            "then use 'uv run econductor doctor' if the check still fails."
        )
    settings.configured = True
    settings.save()
    console.print("Configured. No model downloaded. Launch econductor, then use /model.")
    console.print(f"Local settings: {data_dir() / 'settings.json'}", markup=False)


@app.command()
def doctor() -> None:
    """Check runtimes and the offline sandbox using synthetic local data."""
    ok, reason = check_sandbox()
    info = hardware()
    report = {
        "hardware": info,
        "sandbox": {"ok": ok, "message": reason},
        "rscript": find_rscript(),
        "stata": find_stata(Settings.load().stata_path),
        "settings": str(data_dir() / "settings.json"),
        "models": model_rows(info),
    }
    console.print_json(data=report)
    if not ok:
        raise typer.Exit(1)


@app.command()
def chat(project: Path = typer.Argument(Path("."))) -> None:
    """Open a Codex-style local terminal chat in a research project."""
    try:
        settings = Settings.load()
        if not settings.configured:
            setup()
            settings = Settings.load()
        ok, reason = check_sandbox()
        if not ok:
            error("Offline enforcement unavailable: " + reason)
        guard = PathGuard(project.expanduser())
        from econductor.ui import EconductorApp

        EconductorApp(guard, settings).run()
    except (ValueError, OSError) as exc:
        error(str(exc))


@models_app.command("list")
def models_list() -> None:
    info = hardware()
    memory = info.get("memory_bytes")
    if isinstance(memory, int):
        console.print(f"This Mac: {memory / 1024**3:.0f} GB unified memory", markup=False)
    table = Table("Preset", "Availability", "Download", "RAM min / comfortable", "This Mac", "Best use")
    for row in model_rows(info):
        ram = (
            f"{row['minimum_ram_gb']} / {row['comfortable_ram_gb']} GB"
            if row["minimum_ram_gb"]
            else "unknown"
        )
        fit = row["memory_fit"]
        if row["disk_fit"] == "low disk":
            fit += ", low disk"
        table.add_row(
            row["id"],
            "local" if row["downloaded"] else "not downloaded",
            f"≈{row['estimated_gb']:g} GB" if row["estimated_gb"] else "local folder",
            ram,
            fit,
            row["description"],
        )
    console.print(table)


@models_app.command("download")
def models_download(preset: str) -> None:
    """Explicitly download only the named preset (online operation)."""
    if preset not in PRESETS:
        error("Unknown preset; use 'models list'.")
    console.print(
        f"Downloading {preset}; ≈{PRESETS[preset].estimated_gb:g} GB estimate. Ctrl+C cancels; rerun to resume."
    )
    try:
        console.print(str(download(preset)), markup=False)
    except KeyboardInterrupt:
        error("Download cancelled. Rerun this command to resume.")
    except Exception as exc:
        error(f"Download failed: {type(exc).__name__}: {exc}")


@models_app.command("add")
def models_add(name: str, directory: Path) -> None:
    """Register an existing quantized MLX model folder; no network."""
    try:
        register(name, directory.expanduser().resolve())
        console.print(f"Registered {name}. Select it with /model.")
    except (ValueError, OSError, KeyError) as exc:
        error(str(exc))


@sessions_app.command("list")
def sessions_list(project: Path = typer.Option(Path("."))) -> None:
    guard = PathGuard(project)
    for path in sorted((guard.state / "sessions").glob("*.json")):
        console.print(path.stem, markup=False)


@sessions_app.command("delete")
def sessions_delete(session_id: str, project: Path = typer.Option(Path("."))) -> None:
    import re

    if not re.fullmatch(r"[a-f0-9]{16}", session_id):
        error("Invalid session id")
    guard = PathGuard(project)
    path = guard.state / "sessions" / f"{session_id}.json"
    if path.is_symlink() or not path.is_file():
        error("Session not found")
    if not Confirm.ask(f"Delete transcript {session_id}? Artifacts will remain", default=False):
        raise typer.Exit()
    path.unlink()
    console.print("Transcript deleted; artifacts retained.")


@app.command()
def evaluate(preset: str) -> None:
    """Evaluate an already downloaded model; never downloads weights."""
    from econductor.evaluation import evaluate_model

    try:
        console.print(str(evaluate_model(preset)), markup=False)
    except (ValueError, RuntimeError, OSError) as exc:
        error(str(exc))
