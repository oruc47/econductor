"""Sandboxed MLX JSON-lines protocol. No network, project files, or credentials."""

from __future__ import annotations

import contextlib
import json
import re
import sys
import time
import uuid


def template_messages(messages: list[dict]) -> list[dict]:
    """Pass tool arguments as mappings, including in older saved sessions."""
    normalized = []
    for message in messages:
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            normalized.append(message)
            continue
        calls = []
        for call in message["tool_calls"]:
            function = call.get("function", call)
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("Saved tool-call arguments must be a JSON object.")
            updated_function = {**function, "arguments": arguments}
            calls.append(
                {**call, "function": updated_function}
                if "function" in call
                else updated_function
            )
        normalized.append({**message, "tool_calls": calls})
    return normalized


def main() -> None:
    protocol = sys.stdout

    def emit(value: dict) -> None:
        protocol.write(json.dumps(value, default=str) + "\n")
        protocol.flush()

    try:
        with contextlib.redirect_stdout(sys.stderr):
            import mlx.core as mx
            from mlx_lm import load, stream_generate
            from mlx_lm.sample_utils import make_sampler

            model, tokenizer = load(
                sys.argv[1], tokenizer_config={"trust_remote_code": False, "local_files_only": True}
            )
        emit({"event": "ready"})
    except Exception as error:
        emit({"event": "error", "error": f"Model load failed: {type(error).__name__}: {error}"})
        return
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                messages, tools = template_messages(request["messages"]), request.get("tools", [])
                prompt = tokenizer.apply_chat_template(
                    messages,
                    tools=tools or None,
                    tokenize=True,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
                if request["operation"] == "count":
                    emit({"event": "count", "tokens": len(prompt)})
                    continue
                if len(prompt) + request["max_tokens"] > request["context_tokens"]:
                    raise ValueError("Prompt exceeds the configured context budget.")
                start = time.monotonic()
                text = ""
                sent = 0
                marker = tokenizer.tool_call_start if tokenizer.has_tool_calling else "<tool_call>"
                generated = 0
                finish = None
                mx.reset_peak_memory()
                for result in stream_generate(
                    model,
                    tokenizer,
                    prompt=prompt,
                    max_tokens=request["max_tokens"],
                    sampler=make_sampler(temp=0.2),
                    prefill_step_size=512,
                ):
                    text += result.text
                    generated += 1
                    finish = result.finish_reason
                    # Hold the marker's length to avoid leaking half a tool call into the UI.
                    visible = text.split(marker, 1)[0]
                    end = max(0, len(visible) - len(marker)) if marker not in text else len(visible)
                    if end > sent:
                        emit({"event": "text", "text": visible[sent:end]})
                        sent = end
                calls = []
                visible = text
                if tokenizer.has_tool_calling:
                    pattern = (
                        re.escape(tokenizer.tool_call_start)
                        + r"(.*?)"
                        + re.escape(tokenizer.tool_call_end)
                    )
                    for match in re.finditer(pattern, text, re.DOTALL):
                        parsed = tokenizer.tool_parser(match.group(1), tools)
                        for call in parsed if isinstance(parsed, list) else [parsed]:
                            calls.append(
                                {
                                    "id": uuid.uuid4().hex,
                                    "name": call["name"],
                                    "arguments": call["arguments"],
                                }
                            )
                    visible = re.sub(pattern, "", text, flags=re.DOTALL)
                    if marker in visible:
                        raise ValueError("Truncated or malformed tool call; no tool was executed.")
                elif "<tool_call>" in text:
                    raise ValueError("This model's native tool parser is unsupported.")
                visible = re.sub(r"<think>.*?</think>", "", visible, flags=re.DOTALL).strip()
                # Final content is authoritative; UI replaces the streaming preview.
                emit(
                    {
                        "event": "done",
                        "text": visible,
                        "calls": calls,
                        "input_tokens": len(prompt),
                        "output_tokens": generated,
                        "seconds": time.monotonic() - start,
                        "peak_memory_bytes": mx.get_peak_memory(),
                        "truncated": finish == "length",
                    }
                )
                mx.clear_cache()
        except Exception as error:
            emit({"event": "error", "error": f"{type(error).__name__}: {error}"})


if __name__ == "__main__":
    main()
