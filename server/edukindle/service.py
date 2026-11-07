"""Orchestrates the two agents over the shared store.

Request handlers stay fast by pushing LLM work off the critical path:
- opening a section starts generating its quiz in the background;
- serving a quiz starts generating the next one (instant retakes);
- grading is deterministic, and the Curriculum Architect's coach note is
  refreshed afterwards in the background.
Background jobs are single-flight per key, so repeated taps never fan out
duplicate OpenAI calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from importlib import resources
from typing import Any, Awaitable, Callable

from . import ingest, knowledge
from .agents.curriculum import CurriculumArchitect, apply_concepts
from .agents.educator import SocraticEducator
from .config import Config
from .llm import LLM
from .store import Store, now

log = logging.getLogger("edukindle.service")

EXPLAIN_MODES = ("summary", "simple", "paragraph", "concept")


class NotFound(LookupError):
    pass


class BadRequest(ValueError):
    pass


def _public_question(q: dict[str, Any]) -> dict[str, Any]:
    return {"id": q["id"], "prompt": q["prompt"], "options": q["options"],
            "concept": q["concept"]}


class EduKindle:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.llm = LLM(cfg)
        self.store = Store(cfg.data_dir)
        self.educator = SocraticEducator(self.llm)
        self.architect = CurriculumArchitect(self.llm)
        self._jobs: dict[str, asyncio.Task] = {}
        self._pool: dict[str, dict[str, Any]] = {}  # section id -> prefetched quiz

    # -- lifecycle ---------------------------------------------------------------

    async def startup(self) -> None:
        marker = self.cfg.data_dir / ".seeded"
        if not marker.exists():
            self._seed_samples()
            marker.touch()
        for doc in self.store.documents.values():
            if not doc["concepts_ready"]:
                self._spawn(f"map:{doc['id']}", lambda d=doc: self._map(d))

    async def shutdown(self) -> None:
        for t in self._jobs.values():
            t.cancel()

    def _seed_samples(self) -> None:
        for res in resources.files("edukindle.sample").iterdir():
            if res.name.endswith(".json"):
                doc = load_sample(json.loads(res.read_text(encoding="utf-8")),
                                  self.store.documents)
                self.store.save_document(doc)
                log.info("seeded sample document %s", doc["id"])

    def _spawn(self, key: str, fn: Callable[[], Awaitable[Any]]) -> asyncio.Task:
        task = self._jobs.get(key)
        if task and not task.done():
            return task
        task = asyncio.create_task(fn())
        self._jobs[key] = task

        def done(t: asyncio.Task) -> None:
            if self._jobs.get(key) is t:
                del self._jobs[key]
            if not t.cancelled() and t.exception():
                log.error("background job %s failed", key, exc_info=t.exception())
        task.add_done_callback(done)
        return task

    async def _map(self, doc: dict[str, Any]) -> None:
        await self.architect.map_document(doc)
        self.store.save_document(doc)
        log.info("mapped %d concepts for %s", len(doc["concepts"]), doc["id"])

    async def _ready_doc(self, doc: dict[str, Any]) -> None:
        if not doc["concepts_ready"]:
            await self._spawn(f"map:{doc['id']}", lambda: self._map(doc))

    # -- lookups -------------------------------------------------------------------

    def _doc(self, doc_id: str) -> dict[str, Any]:
        doc = self.store.documents.get(doc_id)
        if not doc:
            raise NotFound(f"no document {doc_id!r}")
        return doc

    def _section(self, section_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        found = self.store.section(section_id)
        if not found:
            raise NotFound(f"no section {section_id!r}")
        return found

    def _section_status(self, sid: str) -> str:
        rec = self.store.student["sections"].get(sid)
        if not rec:
            return "new"
        return "passed" if rec.get("passed") else "read"

    # -- library -------------------------------------------------------------------

    def home(self) -> dict[str, Any]:
        st = self.store.student
        docs = []
        for doc in self.store.documents.values():
            passed = sum(1 for s in doc["sections"] if self._section_status(s["id"]) == "passed")
            docs.append({
                "id": doc["id"],
                "title": doc["title"],
                "sections": len(doc["sections"]),
                "passed": passed,
                "overall": self.architect.progress(doc, st)["overall"],
                "concepts_ready": doc["concepts_ready"],
            })
        cont = None
        visited = [(rec["visited"], sid) for sid, rec in st["sections"].items()
                   if rec.get("visited")]
        for _, sid in sorted(visited, reverse=True):
            found = self.store.section(sid)
            if found:
                doc, s = found
                cont = {"doc_id": doc["id"], "doc_title": doc["title"],
                        "section_id": sid, "title": s["title"]}
                break
        return {
            "llm": {"enabled": self.llm.enabled, "model": self.llm.model if self.llm.enabled
                    else "offline"},
            "documents": docs,
            "continue": cont,
        }

    def toc(self, doc_id: str) -> dict[str, Any]:
        doc = self._doc(doc_id)
        secs = self.store.student["sections"]
        prog = self.architect.progress(doc, self.store.student)
        return {
            "id": doc["id"],
            "title": doc["title"],
            "overall": prog["overall"],
            "passed": prog["sections_passed"],
            "total": prog["sections_total"],
            "concepts_ready": doc["concepts_ready"],
            "sections": [{
                "id": s["id"],
                "index": s["index"],
                "chapter": s["chapter"],
                "title": s["title"],
                "status": self._section_status(s["id"]),
                "best": secs.get(s["id"], {}).get("best"),
                "words": len(s["text"].split()),
            } for s in doc["sections"]],
        }

    async def add_document(self, filename: str, data: bytes) -> dict[str, Any]:
        try:
            doc = ingest.ingest(filename, data, self.store.documents)
        except ValueError as e:
            raise BadRequest(str(e)) from e
        except Exception as e:  # malformed PDFs raise all sorts
            raise BadRequest(f"couldn't read {filename}: {e}") from e
        self.store.save_document(doc)
        self._spawn(f"map:{doc['id']}", lambda: self._map(doc))
        return {"id": doc["id"], "title": doc["title"], "sections": len(doc["sections"])}

    def delete_document(self, doc_id: str) -> None:
        self._doc(doc_id)
        self.store.delete_document(doc_id)

    def reset_progress(self) -> None:
        self._pool.clear()
        self.store.reset_student()

    # -- reading -----------------------------------------------------------------

    def open_section(self, section_id: str) -> dict[str, Any]:
        doc, s = self._section(section_id)
        st = self.store.student
        rec = st["sections"].setdefault(section_id, {"passed": False, "best": None,
                                                     "attempts": 0})
        rec["visited"] = now()
        st["last_section"][doc["id"]] = section_id
        self.store.save_student()
        self._prefetch_quiz(section_id)

        secs = doc["sections"]
        prev = secs[s["index"] - 1] if s["index"] > 0 else None
        nxt = secs[s["index"] + 1] if s["index"] + 1 < len(secs) else None
        mastery = st["concepts"]
        return {
            "id": s["id"],
            "doc_id": doc["id"],
            "doc_title": doc["title"],
            "index": s["index"],
            "total": len(secs),
            "chapter": s["chapter"],
            "title": s["title"],
            "text": s["text"],
            "status": self._section_status(section_id),
            "prev": {"id": prev["id"], "title": prev["title"]} if prev else None,
            "next": {"id": nxt["id"], "title": nxt["title"]} if nxt else None,
            "concepts": [{
                "id": cid,
                "label": doc["concepts"][cid]["label"],
                "status": knowledge.status_of(mastery.get(cid)),
                "mastery": round(mastery[cid]["p"], 3) if cid in mastery else 0.0,
            } for cid in s["concepts"] if cid in doc["concepts"]],
        }

    async def explain(self, section_id: str, mode: str, paragraph: int | None = None,
                      concept_id: str | None = None) -> dict[str, Any]:
        if mode not in EXPLAIN_MODES:
            raise BadRequest(f"mode must be one of {', '.join(EXPLAIN_MODES)}")
        doc, s = self._section(section_id)
        para_text, concept, status = None, None, "unknown"
        if mode == "paragraph":
            paras = s["text"].split("\n\n")
            if paragraph is None or not 0 <= paragraph < len(paras):
                raise BadRequest("paragraph index out of range")
            para_text = paras[paragraph]
        if mode == "concept":
            concept = doc["concepts"].get(concept_id or "")
            if not concept:
                raise NotFound(f"no concept {concept_id!r}")
            status = knowledge.status_of(self.store.student["concepts"].get(concept_id))

        key = "explain:" + hashlib.sha1(
            f"{section_id}|{mode}|{paragraph}|{concept_id}|{status}|{self.llm.enabled}"
            .encode()).hexdigest()[:16]
        cached = self.store.cache_get(key)
        if cached:
            return {**cached, "cached": True}

        async def run() -> dict[str, Any]:
            out = await self.educator.explain(doc, s, mode, para_text, concept, status)
            self.store.cache_put(key, out)
            return out
        out = await self._spawn(key, run)
        return {**out, "cached": False}

    # -- assessment ----------------------------------------------------------------

    def _avoid(self, section_id: str) -> list[str]:
        return [q["prompt"][:60] for quiz in self.store.quizzes.values()
                if quiz["section_id"] == section_id for q in quiz["questions"]]

    async def _make_quiz(self, section_id: str) -> dict[str, Any]:
        doc, s = self._section(section_id)
        await self._ready_doc(doc)
        return await self.educator.generate_quiz(doc, s, self.store.student["concepts"],
                                                 self._avoid(section_id))

    def _prefetch_quiz(self, section_id: str) -> None:
        if section_id in self._pool:
            return

        async def run() -> None:
            self._pool[section_id] = await self._make_quiz(section_id)
        self._spawn(f"quiz:{section_id}", run)

    async def start_quiz(self, section_id: str) -> dict[str, Any]:
        self._section(section_id)
        quiz = self._pool.pop(section_id, None)
        if quiz is None:
            job = self._jobs.get(f"quiz:{section_id}")
            if job:
                try:
                    await job  # prefetch already in flight: wait for it
                except Exception:
                    pass  # logged by the job; generate afresh below
                quiz = self._pool.pop(section_id, None)
        if quiz is None:
            quiz = await self._make_quiz(section_id)
        quiz["served"] = True
        self.store.quizzes[quiz["id"]] = quiz
        self.store.save_quizzes()
        self._prefetch_quiz(section_id)  # the retake is ready before it's asked for
        doc, s = self._section(section_id)
        return {
            "quiz_id": quiz["id"],
            "section_id": section_id,
            "section_title": s["title"],
            "source": quiz["source"],
            "questions": [_public_question(q) for q in quiz["questions"]],
        }

    def answer(self, quiz_id: str, answers: dict[str, int]) -> dict[str, Any]:
        quiz = self.store.quizzes.get(quiz_id)
        if not quiz:
            raise NotFound(f"no quiz {quiz_id!r}")
        doc, s = self._section(quiz["section_id"])
        st = self.store.student
        first_time = quiz["first_score"] is None

        before = {c: st["concepts"].get(c, {}).get("p", knowledge.P_INIT)
                  for c in {q["concept"] for q in quiz["questions"]}}
        results = self.educator.grade(quiz, answers)
        self.architect.record(st, results)

        total = len(quiz["questions"])
        ready = self.educator.ready_to_advance(quiz, st["concepts"])
        rec = st["sections"].setdefault(s["id"], {"passed": False, "best": None,
                                                  "attempts": 0})
        if first_time and quiz["first_score"] is not None:
            rec["attempts"] += 1
            rec["best"] = max(rec["best"] or 0, quiz["first_score"])
            st["history"].append({
                "quiz_id": quiz_id, "doc_id": doc["id"], "section_id": s["id"],
                "section_title": s["title"], "ts": now(), "score": quiz["first_score"],
                "total": total, "ready": ready,
            })
        rec["passed"] = rec["passed"] or ready

        tested = [q["concept"] for q in quiz["questions"]]
        missed = [r["concept"] for r in results if not r["correct"]]
        gaps = self.architect.gaps(doc, st, missed or tested)
        retry = [q["id"] for q in quiz["questions"]
                 if len(quiz["attempts"].get(q["id"], [])) == 1
                 and quiz["attempts"][q["id"]][0] != q["correct"]]
        nxt = self.architect.next_step(doc, st, s, ready, gaps)

        self.store.save_quizzes()
        self.store.save_student()
        self._refresh_coach(doc, nxt)

        concepts = doc["concepts"]
        return {
            "quiz_id": quiz_id,
            "section_id": s["id"],
            "score": quiz["first_score"],
            "total": total,
            "ready": ready,
            "message": self.educator.message(quiz["first_score"], total, ready, len(retry)),
            "results": results,
            "retry": retry,
            "mastery": [{
                "concept": c,
                "label": concepts[c]["label"] if c in concepts else c,
                "before": round(before[c], 3),
                "after": round(st["concepts"].get(c, {}).get("p", before[c]), 3),
                "status": knowledge.status_of(st["concepts"].get(c)),
            } for c in before if c],
            "gaps": gaps,
            "next": nxt,
        }

    def _refresh_coach(self, doc: dict[str, Any], nxt: dict[str, Any] | None) -> None:
        key = f"coach:{doc['id']}"
        old = self._jobs.get(key)
        if old and not old.done():
            old.cancel()  # superseded by fresher results

        async def run() -> None:
            text = await self.architect.coach_note(doc, self.store.student, nxt)
            self.store.student["coach"][doc["id"]] = {"text": text, "ts": now()}
            self.store.save_student()
        self._spawn(key, run)

    # -- progress & graph ------------------------------------------------------------

    def progress(self, doc_id: str) -> dict[str, Any]:
        return self.architect.progress(self._doc(doc_id), self.store.student)

    def graph(self, doc_id: str) -> dict[str, Any]:
        doc = self._doc(doc_id)
        view = knowledge.graph_view(doc, self.store.student["concepts"])
        view["concepts_ready"] = doc["concepts_ready"]
        return view


def load_sample(data: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Sample documents ship with hand-written concepts and a question bank."""
    raw = [(s.get("chapter", ""), s["title"], s["paragraphs"]) for s in data["sections"]]
    doc = ingest.build_document(data["title"], raw, "sample", existing)
    for s, src in zip(doc["sections"], data["sections"]):
        s["quiz_bank"] = src.get("quiz_bank", [])
    apply_concepts(doc, data["concepts"])
    return doc
