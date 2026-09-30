from __future__ import annotations

import json
import subprocess
from pathlib import Path

from econductor.config import Settings
from econductor.security import (
    safe_environment,
    sandbox_command,
    sandbox_profile,
    stop_process,
    worker_python,
)
from econductor.types import Completion, ToolCall


class MLXInference:
    def __init__(self, path: Path, work: Path, settings: Settings):
        self.path, self.work, self.settings = path, work, settings
        self.process: subprocess.Popen | None = None
        self.log_handle = None

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        self.work.mkdir(parents=True, exist_ok=True, mode=0o700)
        python = worker_python()
        profile = sandbox_profile([self.path], [self.work], gpu=True)
        self.log_handle = (self.work / "inference.log").open("w")
        self.process = subprocess.Popen(
            sandbox_command(
                [str(python), "-m", "econductor.inference_worker", str(self.path)], profile
            ),
            env=safe_environment(self.work),
            cwd=self.work,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log_handle,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        response = self._read()
        if response.get("event") != "ready":
            self.unload()
            raise RuntimeError(response.get("error", "Inference worker did not become ready."))

    def _read(self) -> dict:
        if not self.process or not self.process.stdout:
            raise RuntimeError("Inference worker not running")
        line = self.process.stdout.readline()
        if not line:
            tail = (self.work / "inference.log").read_text(errors="replace")[-2000:]
            raise RuntimeError("Inference worker exited. " + tail)
        value = json.loads(line)
        if value.get("event") == "error":
            raise RuntimeError(value["error"])
        return value

    def _send(self, request: dict) -> None:
        self.start()
        if not self.process or not self.process.stdin:
            raise RuntimeError("Inference worker not running")
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()

    def count(self, messages: list[dict], tools: list[dict]) -> int:
        self._send({"operation": "count", "messages": messages, "tools": tools})
        return self._read()["tokens"]

    def complete(self, messages: list[dict], tools: list[dict], on_text) -> Completion:
        self._send(
            {
                "operation": "complete",
                "messages": messages,
                "tools": tools,
                "max_tokens": self.settings.generation_tokens,
                "context_tokens": self.settings.context_tokens,
            }
        )
        while True:
            value = self._read()
            if value["event"] == "text":
                on_text(value["text"])
            elif value["event"] == "done":
                value.pop("event")
                value["calls"] = [ToolCall(**call) for call in value["calls"]]
                return Completion(**value)

    def unload(self) -> None:
        process, self.process = self.process, None
        if process:
            stop_process(process)
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
