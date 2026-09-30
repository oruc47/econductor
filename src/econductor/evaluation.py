from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from econductor.agent import Agent
from econductor.config import Settings, atomic_json
from econductor.inference import MLXInference
from econductor.models import PRESETS, local_model, registry
from econductor.security import PathGuard
from econductor.types import Permission

PROMPTS = [
    "Inspect evaluation_fixture.csv, then use the query tool to report the exact row count and mean of y. Show the computed result.",
    "Use the query tool on evaluation_fixture.csv and calculate the exact mean of y. Show your SQL and the result.",
    "Run a Python statsmodels OLS regression of y on x with an intercept using evaluation_fixture.csv. Save the script; print JSON with numeric keys coefficient and standard_error, then explain the coefficient.",
]


def evaluate_model(preset: str) -> str:
    data = local_model(preset)
    if preset not in PRESETS and data is None:
        raise ValueError(f"Model {preset!r} is not downloaded. Evaluation never downloads weights.")
    if data is None:
        raise ValueError(f"Model {preset!r} is not downloaded. Evaluation never downloads weights.")
    model = preset
    guard = PathGuard(Path.cwd())
    guard.initialize()
    settings = Settings.load()
    settings.model = model
    inference = MLXInference(data, guard.state / "work" / "evaluation-inference", settings)
    output = (
        guard.state / "artifacts" / f"evaluation-{model}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    )
    output.mkdir(mode=0o700)
    fixture = output / "evaluation_fixture.csv"
    fixture.write_text("x,y\n1,3\n2,5\n3,7\n4,9\n5,11\n")
    agent = Agent(guard, settings, inference)
    agent.permission = Permission.AUTONOMOUS
    records = []
    prompts = [
        prompt.replace("evaluation_fixture.csv", str(fixture.relative_to(guard.project)))
        for prompt in PROMPTS
    ]
    try:
        for number, prompt in enumerate(prompts):
            started = time.monotonic()
            agent.session.messages.clear()
            agent.session.events.clear()
            try:
                agent.turn(prompt, lambda *_: None, lambda *_: False)
                tools = [event for event in agent.session.events if event["kind"] == "tool"]
                text = " ".join(
                    m.get("content", "") for m in agent.session.messages if m["role"] == "assistant"
                ).casefold()
                details = [
                    json.loads(m["content"]) for m in agent.session.messages if m["role"] == "tool"
                ]
                evidence = " ".join(d.get("summary", "") for d in details).casefold()
                if number < 2:
                    if number == 0:
                        count_seen = re.search(r'"(?:row_count|count_star\(\))"\s*:\s*5', evidence)
                        correct = (
                            any(e["name"] == "query" and e["ok"] for e in tools)
                            and bool(count_seen)
                            and "7.0" in evidence
                        )
                    else:
                        correct = (
                            any(e["name"] == "query" and e["ok"] for e in tools)
                            and "7.0" in evidence
                            and "y" in evidence
                        )
                else:
                    coefficient = re.search(r'"coefficient"\s*:\s*(-?\d+(?:\.\d+)?)', evidence)
                    standard_error = re.search(
                        r'"standard_error"\s*:\s*(-?\d+(?:\.\d+)?)', evidence
                    )
                    correct = (
                        any(e["name"] == "execute" and e["ok"] for e in tools)
                        and bool(coefficient and standard_error)
                        and abs(float(coefficient.group(1)) - 2) < 0.01
                    )
                records.append(
                    {
                        "task": number + 1,
                        "elapsed_seconds": time.monotonic() - started,
                        "tool_calls": len(tools),
                        "successful_tools": sum(e["ok"] for e in tools),
                        "answer_check": "pass" if correct else "fail",
                        "answer": text[-1000:],
                        "events": agent.session.events.copy(),
                    }
                )
            except Exception as error:
                records.append(
                    {
                        "task": number + 1,
                        "elapsed_seconds": time.monotonic() - started,
                        "answer_check": "error",
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
            agent.session.messages.clear()
    finally:
        inference.unload()
    report = {
        "model": model,
        "repository": PRESETS[model].repo if model in PRESETS else registry()[model]["path"],
        "revision": registry().get(model, {}).get("revision"),
        "evaluation": "synthetic smoke suite; not an independent capability benchmark",
        "tasks": records,
        "answer_check_rate": sum(r.get("answer_check") == "pass" for r in records) / len(records),
        "mean_seconds": sum(r.get("elapsed_seconds", 0) for r in records) / len(records),
        "peak_memory_bytes": max(
            (
                e.get("peak_memory_bytes", 0)
                for r in records
                for e in r.get("events", [])
                if e["kind"] == "completion"
            ),
            default=0,
        ),
        "fixture": "y = 2*x + 1 for x=1..5; mean(y)=7",
    }
    path = output / "report.json"
    atomic_json(path, report)
    table = output / "report.md"
    table.write_text(
        f"# Econductor evaluation: {model}\n\nSynthetic smoke suite, not a capability benchmark.\n\n- Repository: `{report['repository']}`\n- Revision: `{report['revision']}`\n- Answer checks passed: {sum(r.get('answer_check') == 'pass' for r in records)}/{len(records)}\n- Mean task time: {report['mean_seconds']:.1f}s\n- Peak memory: {report['peak_memory_bytes'] / 1024**3:.2f} GiB\n\n| Task | Seconds | Tool calls | Check |\n|---:|---:|---:|---|\n"
        + "\n".join(
            f"| {r['task']} | {r.get('elapsed_seconds', 0):.1f} | {r.get('tool_calls', 0)} | {r.get('answer_check', 'error')} |"
            for r in records
        )
        + f"\n\nDetailed answers and events: `{path}`.\n"
    )
    records_path = guard.state / "sessions"
    # Evaluation results are artifacts only; no transcript is retained.
    for transcript in records_path.glob("*.json"):
        try:
            if json.loads(transcript.read_text()).get("id") == agent.session.id:
                transcript.unlink()
        except (ValueError, OSError):
            pass
    return str(table)
