from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from econductor.security import PRIVATE_NAMES, PathGuard


def chunks(text: str, width: int = 3000):
    for start in range(0, len(text), width - 200):
        yield text[start : start + width]


def document_search(guard: PathGuard, query: str, index: Path) -> dict:
    warnings = []
    indexed = 0
    with sqlite3.connect(index) as db:
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(path UNINDEXED, page UNINDEXED, body)"
        )
        db.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, stamp TEXT)")
        seen = set()
        for candidate in guard.project.rglob("*"):
            relative = candidate.relative_to(guard.project)
            if any(part.startswith(".") or part in PRIVATE_NAMES for part in relative.parts):
                continue
            if candidate.suffix.lower() not in {".pdf", ".md", ".txt"} or not candidate.is_file():
                continue
            path = guard.resolve(str(relative))
            if path.stat().st_size > 100 * 1024**2:
                warnings.append(f"Skipped oversized document: {relative}")
                continue
            name = str(relative)
            seen.add(name)
            stamp = f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
            if db.execute("SELECT stamp FROM files WHERE path=?", (name,)).fetchone() == (stamp,):
                continue
            db.execute("DELETE FROM passages WHERE path=?", (name,))
            try:
                if path.suffix.lower() == ".pdf":
                    from pypdf import PdfReader

                    pages = (
                        (i + 1, p.extract_text() or "") for i, p in enumerate(PdfReader(path).pages)
                    )
                else:
                    pages = [(1, path.read_text(errors="replace")[:2_000_000])]
                has_text = False
                for page, text in pages:
                    if text.strip():
                        has_text = True
                        for body in chunks(text[:500_000]):
                            db.execute("INSERT INTO passages VALUES(?,?,?)", (name, page, body))
                            indexed += 1
                if not has_text:
                    warnings.append(f"No text in {name}; scanned PDF OCR is not supported.")
                db.execute("INSERT OR REPLACE INTO files VALUES(?,?)", (name, stamp))
            except Exception as error:
                db.execute("DELETE FROM passages WHERE path=?", (name,))
                warnings.append(f"Could not index {name}: {type(error).__name__}")
        for (name,) in db.execute("SELECT path FROM files").fetchall():
            if name not in seen:
                db.execute("DELETE FROM files WHERE path=?", (name,))
                db.execute("DELETE FROM passages WHERE path=?", (name,))
        tokens = re.findall(r"\w+", query)[:20]
        if not tokens:
            raise ValueError("Enter a search phrase containing words.")
        expression = " OR ".join('"' + t + '"' for t in tokens)
        matches = db.execute(
            "SELECT path,page,body FROM passages WHERE passages MATCH ? ORDER BY bm25(passages) LIMIT 6",
            (expression,),
        ).fetchall()
    return {
        "matches": [{"path": p, "page": page, "excerpt": body[:1800]} for p, page, body in matches],
        "warnings": warnings[:20],
        "new_passages": indexed,
    }
