"""JSON-file persistence.

Everything lives in memory and is written through to small JSON files under
the data dir. The server runs a single asyncio event loop, so mutations between
awaits are atomic and no locking is needed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

MAX_HISTORY = 200
MAX_QUIZZES = 60


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def empty_student() -> dict[str, Any]:
    return {
        "concepts": {},      # concept id -> {p, attempts, correct, last}
        "sections": {},      # section id -> {visited, passed, best, attempts}
        "last_section": {},  # doc id -> section id
        "history": [],       # recent graded quizzes, newest last
        "coach": {},         # doc id -> {text, ts}
    }


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.docs_dir = root / "documents"
        self.docs_dir.mkdir(parents=True, exist_ok=True)

        self.documents: dict[str, dict[str, Any]] = {}
        for p in sorted(self.docs_dir.glob("*.json")):
            doc = _read_json(p, None)
            if doc:
                self.documents[doc["id"]] = doc

        self.student: dict[str, Any] = _read_json(root / "student.json", None) or empty_student()
        self.quizzes: dict[str, dict[str, Any]] = _read_json(root / "quizzes.json", {})
        self.cache: dict[str, Any] = _read_json(root / "cache.json", {})

    # -- documents ---------------------------------------------------------

    def save_document(self, doc: dict[str, Any]) -> None:
        self.documents[doc["id"]] = doc
        _write_json(self.docs_dir / f"{doc['id']}.json", doc)

    def delete_document(self, doc_id: str) -> None:
        self.documents.pop(doc_id, None)
        (self.docs_dir / f"{doc_id}.json").unlink(missing_ok=True)

    def section(self, section_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        doc_id, _, _ = section_id.rpartition(".")
        doc = self.documents.get(doc_id)
        if not doc:
            return None
        for s in doc["sections"]:
            if s["id"] == section_id:
                return doc, s
        return None

    # -- learner -----------------------------------------------------------

    def save_student(self) -> None:
        st = self.student
        st["history"] = st["history"][-MAX_HISTORY:]
        _write_json(self.root / "student.json", st)

    def reset_student(self) -> None:
        self.student = empty_student()
        self.quizzes = {}
        self.save_student()
        self.save_quizzes()

    def save_quizzes(self) -> None:
        if len(self.quizzes) > MAX_QUIZZES:
            keep = sorted(self.quizzes.values(), key=lambda q: q["created"])[-MAX_QUIZZES:]
            self.quizzes = {q["id"]: q for q in keep}
        _write_json(self.root / "quizzes.json", self.quizzes)

    # -- LLM output cache --------------------------------------------------

    def cache_get(self, key: str) -> Any:
        return self.cache.get(key)

    def cache_put(self, key: str, value: Any) -> None:
        self.cache[key] = value
        _write_json(self.root / "cache.json", self.cache)


def now() -> float:
    return round(time.time(), 3)
