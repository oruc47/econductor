from __future__ import annotations

import difflib
import json
import subprocess
import time
import uuid
from pathlib import Path
from threading import Event

from econductor.config import Settings, find_rscript, find_stata
from econductor.security import (
    PathGuard,
    safe_environment,
    sandbox_command,
    sandbox_profile,
    stop_process,
    worker_python,
)
from econductor.types import ToolCall, ToolResult


class Executor:
    def __init__(self, guard: PathGuard, settings: Settings):
        self.guard, self.settings = guard, settings
        self.cancelled = Event()
        self.process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        if self.process:
            stop_process(self.process)

    def reset(self) -> None:
        self.cancelled.clear()

    def run_dir(self) -> Path:
        directory = (
            self.guard.state
            / "artifacts"
            / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        )
        if (self.guard.state / "artifacts").is_symlink():
            raise ValueError("Unsafe artifacts directory")
        directory.mkdir(mode=0o700)
        return directory

    def preview_edit(self, args: dict) -> tuple[Path, str, bool]:
        path = self.guard.resolve(args["path"], write=True)
        if path.suffix.lower() not in {
            ".py",
            ".r",
            ".do",
            ".sql",
            ".md",
            ".txt",
            ".toml",
            ".json",
            ".yaml",
            ".yml",
            ".ipynb",
        }:
            raise ValueError(
                "Edits support code/text files only; source-data modification is outside the agent."
            )
        if path.exists() and path.stat().st_size > 2_000_000:
            raise ValueError("File too large for an inline edit.")
        before = path.read_text() if path.exists() else ""
        after = args["content"]
        if len(after) > 200_000:
            raise ValueError("Edit too large; limit is 200,000 characters.")
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(True),
                after.splitlines(True),
                fromfile=str(path.relative_to(self.guard.project)),
                tofile=str(path.relative_to(self.guard.project)),
            )
        )
        return path, diff, path.exists()

    def edit(self, args: dict) -> ToolResult:
        path, _, _ = self.preview_edit(args)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Repeat guard after mkdir to prevent traversal through an existing symlink.
        path = self.guard.resolve(args["path"], write=True)
        if path.exists():
            backup = self.run_dir() / "before-edit.txt"
            backup.write_text(path.read_text())
        path.write_text(args["content"])
        return ToolResult(True, f"Saved {path.relative_to(self.guard.project)}", [str(path)])

    def _process(
        self,
        command: list[str],
        work: Path,
        *,
        payload: dict | None = None,
        built_in: bool = False,
        stata: Path | None = None,
    ) -> tuple[int, str, str]:
        writes = [work, self.guard.state / "cache"] if built_in else [work]
        profile = sandbox_profile(
            [self.guard.project], writes, project=self.guard.project, stata=stata
        )
        env = safe_environment(work, python_path=built_in)
        env["PROJECT_ROOT"], env["OUTPUT_DIR"] = str(self.guard.project), str(work)
        log = work / "execution.log"
        stdin = work / "request.json"
        stdin.write_text(json.dumps(payload) if payload is not None else "")
        with stdin.open("r") as input_file, log.open("w") as output_file:
            process = subprocess.Popen(
                sandbox_command(command, profile),
                cwd=work,
                env=env,
                stdin=input_file,
                stdout=output_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.process = process
            try:
                start = time.monotonic()
                while process.poll() is None:
                    if self.cancelled.is_set():
                        stop_process(process)
                        raise RuntimeError(
                            "Execution cancelled. Partial artifacts remain in the run folder."
                        )
                    if time.monotonic() - start > self.settings.timeout_seconds:
                        stop_process(process)
                        raise RuntimeError(
                            f"Execution exceeded {self.settings.timeout_seconds}s. Partial artifacts remain in {work}"
                        )
                    if log.stat().st_size > 10_000_000:
                        stop_process(process)
                        raise RuntimeError(
                            "Execution output exceeded 10 MB. Write detailed output to an artifact file."
                        )
                    self.cancelled.wait(0.05)
            finally:
                # Also kill descendants that outlive their parent.
                import os
                import signal

                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process = None
        with log.open(errors="replace") as handle:
            text = handle.read(16000)
        return process.returncode, text, str(log)

    def builtin(self, call: ToolCall) -> ToolResult:
        work = self.run_dir()
        payload = {
            "project": str(self.guard.project),
            "work": str(work),
            "memory": self.settings.duckdb_memory,
            "name": call.name,
            "arguments": call.arguments,
        }
        code, output, log = self._process(
            [str(worker_python()), "-m", "econductor.tool_worker"],
            work,
            payload=payload,
            built_in=True,
        )
        if code != 0:
            return ToolResult(False, output or f"Worker exited {code}", [log])
        try:
            response = json.loads(Path(log).read_text())
        except ValueError:
            return ToolResult(False, "Tool worker returned invalid output: " + output, [log])
        if not response["ok"]:
            return ToolResult(False, response["error"], [log])
        details = response["result"]
        artifacts = [log]
        if details.get("artifact"):
            artifacts.append(details["artifact"])
        return ToolResult(True, json.dumps(details, default=str)[:12000], artifacts)

    def execute(self, args: dict) -> ToolResult:
        language, code = args["language"], args["code"]
        if len(code) > 100000:
            raise ValueError("Script too long; limit is 100,000 characters.")
        work = self.run_dir()
        suffix = {"python": ".py", "r": ".R", "stata": ".do"}[language]
        script = work / ("analysis" + suffix)
        script.write_text(code)
        stata = None
        if language == "python":
            command = [str(worker_python()), "-I", str(script)]
        elif language == "r":
            rscript = find_rscript()
            if not rscript:
                return ToolResult(
                    False, "Rscript is not installed. Install R outside analysis.", [str(script)]
                )
            command = [rscript, "--vanilla", str(script)]
        else:
            executable = find_stata(self.settings.stata_path)
            if not executable:
                return ToolResult(
                    False,
                    "Licensed Stata executable not found. Configure it in setup.",
                    [str(script)],
                )
            stata = Path(executable)
            command = [executable, "-b", "do", str(script)]
        status, output, execution_log = self._process(command, work, stata=stata)
        if language != "stata":
            output = Path(execution_log).read_text(errors="replace")
        stata_log = work / "analysis.log"
        if language == "stata" and stata_log.exists():
            full_log = stata_log.read_text(errors="replace")
            lines = full_log.splitlines(keepends=True)
            start = next((i for i, line in enumerate(lines) if line.startswith(". do ")), None)
            if start is None:
                output += "\nStata did not reach do-file execution; inspect analysis.log."
            else:
                output += "\n" + "".join(lines[start:])[-12000:]
            # Stata can return OS success for a do-file error; read its r(code).
            import re

            if re.search(r"(?m)^r\([1-9]\d*\);", output):
                status = 1
        artifacts = [str(p) for p in work.iterdir() if p.is_file() and p.name != "request.json"]
        if len(output) > 4000:
            omitted = len(output) - 3600
            output = (
                output[:600]
                + f"\n...[{omitted} characters omitted; full output is in {execution_log}]...\n"
                + output[-3000:]
            )
        return ToolResult(
            status == 0,
            f"Exit {status}. Working/output directory: {work}\nFull log: {execution_log}\n{output}",
            artifacts,
            {"run_directory": str(work)},
        )
