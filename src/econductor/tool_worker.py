"""One-shot offline worker for trusted built-in tools (JSON in, JSON out)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from econductor.security import PRIVATE_NAMES, PathGuard


def dispatch(request: dict) -> dict:
    guard = PathGuard(Path(request["project"]))
    name, args = request["name"], request["arguments"]
    work, cache = Path(request["work"]), guard.state / "cache"
    if name == "list_files":
        try:
            root = guard.resolve(args.get("path", "."))
        except ValueError as error:
            raise ValueError(
                f"{error} Retry list_files with no path to list the selected project root."
            ) from error
        paths = []
        for file in root.rglob("*"):
            relative = file.relative_to(guard.project)
            if (
                any(part.startswith(".") or part in PRIVATE_NAMES for part in relative.parts)
                or not file.is_file()
            ):
                continue
            try:
                guard.resolve(str(relative))
            except ValueError:
                continue
            paths.append(str(relative))
            if len(paths) >= 500:
                break
        return {"files": paths, "limit": 500}
    if name == "read_file":
        path = guard.resolve(args["path"])
        if path.suffix.lower() in {".pdf", ".parquet", ".dta", ".xlsx"}:
            raise ValueError("Use inspect_dataset or search_documents for binary files.")
        with path.open(errors="replace") as handle:
            content = handle.read(12000)
        return {
            "path": str(path.relative_to(guard.project)),
            "content": content,
            "note": "Read bounded to 12,000 characters.",
        }
    if name == "search_files":
        query = args["query"]
        hits = []
        for relative in dispatch({**request, "name": "list_files", "arguments": {}})["files"]:
            path = guard.resolve(relative)
            if path.suffix.lower() not in {
                ".py",
                ".r",
                ".do",
                ".md",
                ".txt",
                ".sql",
                ".toml",
                ".json",
                ".yaml",
                ".yml",
            }:
                continue
            if path.stat().st_size > 2_000_000:
                continue
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if query.casefold() in line.casefold():
                    hits.append({"path": relative, "line": number, "text": line[:400]})
                    if len(hits) >= 30:
                        return {"matches": hits, "limit": 30}
        return {"matches": hits}
    if name == "inspect_dataset":
        from econductor.data import inspect_dataset

        return inspect_dataset(guard.resolve(args["path"]), cache, work, request["memory"])
    if name == "query":
        from econductor.data import query_dataset

        paths = {alias: guard.resolve(path) for alias, path in args["tables"].items()}
        return query_dataset(paths, args["sql"], cache, work, request["memory"])
    if name == "search_documents":
        from econductor.documents import document_search

        return document_search(guard, args["query"], cache / "documents.sqlite")
    raise ValueError(f"Unknown worker tool: {name}")


def main() -> None:
    try:
        request = json.loads(sys.stdin.readline())
        result = {"ok": True, "result": dispatch(request)}
    except Exception as error:
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
