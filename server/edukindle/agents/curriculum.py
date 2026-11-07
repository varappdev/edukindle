"""Agent 2: the Curriculum Architect.

Owns the knowledge graph. It maps a document onto syllabus concepts and their
prerequisites, folds every graded answer into per-concept mastery, traces wrong
answers back to the unmastered prerequisites underneath them, and decides what
the student should study next.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .. import knowledge
from ..llm import INT, LLM, LLMError, STR, arr, obj
from ..store import now
from . import offline

log = logging.getLogger("edukindle.curriculum")

SYSTEM = """You are the Curriculum Architect inside EduKindle, a study app. You map \
learning material onto a knowledge graph of syllabus concepts and plan what a student \
should study next. Be concrete and concise. Plain text only, no Markdown."""

CONCEPT_SCHEMA = obj({
    "concepts": arr(obj({
        "key": STR,
        "label": STR,
        "description": STR,
        "sections": arr(INT),
        "prerequisites": arr(STR),
    })),
})
COACH_SCHEMA = obj({"note": STR})


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:32]


def apply_concepts(doc: dict[str, Any], raw: list[dict[str, Any]]) -> None:
    """Install extracted concepts on the document and its sections."""
    n_sections = len(doc["sections"])
    keys = {_key(c["key"] or c["label"]) for c in raw}
    concepts: dict[str, Any] = {}
    for c in raw:
        key = _key(c["key"] or c["label"])
        if not key or f"{doc['id']}/{key}" in concepts:
            continue
        secs = sorted({i for i in c["sections"] if 1 <= i <= n_sections})
        if not secs:
            continue
        concepts[f"{doc['id']}/{key}"] = {
            "id": f"{doc['id']}/{key}",
            "label": c["label"].strip()[:48],
            "description": c["description"].strip(),
            "sections": [doc["sections"][i - 1]["id"] for i in secs],
            "prereqs": [f"{doc['id']}/{_key(p)}" for p in c["prerequisites"]
                        if _key(p) in keys and _key(p) != key],
        }
    doc["concepts"] = concepts
    for s in doc["sections"]:
        s["concepts"] = [cid for cid, c in concepts.items() if s["id"] in c["sections"]]
    # Every section needs at least one concept so its quiz can update mastery.
    for s in doc["sections"]:
        if not s["concepts"]:
            prev = next((p for p in reversed(doc["sections"][: s["index"]]) if p["concepts"]),
                        None)
            if prev:
                cid = prev["concepts"][-1]
                concepts[cid]["sections"].append(s["id"])
                s["concepts"] = [cid]
    doc["concepts_ready"] = True


class CurriculumArchitect:
    def __init__(self, llm: LLM):
        self.llm = llm

    # -- mapping the material --------------------------------------------------

    async def map_document(self, doc: dict[str, Any]) -> None:
        raw: list[dict[str, Any]] = []
        if self.llm.enabled:
            try:
                raw = await self._llm_concepts(doc)
            except LLMError as e:
                log.warning("concept extraction failed, using offline: %s", e)
        if not raw:
            raw = offline.extract_concepts(doc)
        apply_concepts(doc, raw)

    async def _llm_concepts(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        n = len(doc["sections"])
        budget = max(40, 12000 // max(n, 1))  # words of each section to show
        parts = []
        for s in doc["sections"]:
            words = s["text"].split()
            parts.append(f"[Section {s['index'] + 1}] {s['title']}\n{' '.join(words[:budget])}")
        target = min(30, max(6, round(n * 1.8)))
        user = f"""Build the concept map for "{doc['title']}".

Identify about {target} key syllabus concepts taught in the sections below (never more \
than 30). For each concept give:
- key: short lowercase id like "chain-rule"
- label: 1-4 word display name
- description: one sentence a student would find useful
- sections: the section numbers where it is taught or practised (at least one)
- prerequisites: keys of other concepts in your list that a student must understand first. \
Only direct prerequisites; no cycles.

Every section should be covered by at least one concept.

{chr(10).join(parts)}"""
        data = await self.llm.json(system=SYSTEM, user=user, name="concepts",
                                   schema=CONCEPT_SCHEMA, max_tokens=6000)
        return data.get("concepts", [])

    # -- tracking performance ---------------------------------------------------

    @staticmethod
    def record(student: dict[str, Any], results: list[dict[str, Any]]) -> None:
        for r in results:
            cid = r["concept"]
            if not cid:
                continue
            rec = student["concepts"].setdefault(
                cid, {"p": knowledge.P_INIT, "attempts": 0, "correct": 0, "last": 0})
            rec["p"] = round(knowledge.bkt_update(rec["p"], r["correct"], r["hinted"]), 4)
            rec["attempts"] += 1
            rec["correct"] += int(r["correct"])
            rec["last"] = now()

    def gaps(self, doc: dict[str, Any], student: dict[str, Any],
             concept_ids: list[str]) -> list[dict[str, Any]]:
        """For each weak concept, the unmastered prerequisites likely behind it."""
        g = knowledge.build_graph(doc)
        mastery = student["concepts"]
        out = []
        for cid in dict.fromkeys(concept_ids):
            if cid not in doc["concepts"]:
                continue
            if knowledge.status_of(mastery.get(cid)) not in ("weak", "learning"):
                continue
            roots = []
            for r in knowledge.root_gaps(g, mastery, cid):
                c = doc["concepts"][r]
                sec = self._section(doc, c["sections"][0])
                roots.append({
                    "concept": r,
                    "label": c["label"],
                    "status": knowledge.status_of(mastery.get(r)),
                    "section_id": sec["id"],
                    "section_title": sec["title"],
                })
            out.append({
                "concept": cid,
                "label": doc["concepts"][cid]["label"],
                "mastery": mastery[cid]["p"] if cid in mastery else 0.0,
                "status": knowledge.status_of(mastery.get(cid)),
                "root_causes": roots,
            })
        return out

    # -- planning ---------------------------------------------------------------

    def next_step(self, doc: dict[str, Any], student: dict[str, Any],
                  section: dict[str, Any], ready: bool,
                  gaps: list[dict[str, Any]]) -> dict[str, Any] | None:
        mastery = student["concepts"]
        if not ready:
            # An unmastered prerequisite from an *earlier* section is the real
            # blocker; known-weak ones outrank never-tested ones.
            roots = [(gap, root) for gap in gaps for root in gap["root_causes"]
                     if root["section_id"] != section["id"]]
            roots.sort(key=lambda gr: gr[1]["status"] != "weak")
            for gap, root in roots:
                shaky = "looks shaky" if root["status"] == "weak" else "hasn't been checked yet"
                return {"action": "review", "section_id": root["section_id"],
                        "title": root["section_title"],
                        "reason": f"{gap['label']} builds on {root['label']}, which "
                                  f"{shaky}. Strengthen it first."}
            return {"action": "reread", "section_id": section["id"],
                    "title": section["title"],
                    "reason": "Re-read this section, then take a fresh check."}

        # Ready: next section in reading order whose prerequisites aren't weak.
        g = knowledge.build_graph(doc)
        for s in doc["sections"][section["index"] + 1:]:
            if student["sections"].get(s["id"], {}).get("passed"):
                continue
            blockers = [p for c in s["concepts"] if c in g for p in g.predecessors(c)
                        if knowledge.status_of(mastery.get(p)) == "weak"]
            if blockers:
                c = doc["concepts"][blockers[0]]
                sec = self._section(doc, c["sections"][0])
                return {"action": "review", "section_id": sec["id"], "title": sec["title"],
                        "reason": f"Before {s['title']}, firm up {c['label']}."}
            return {"action": "advance", "section_id": s["id"], "title": s["title"],
                    "reason": "You're ready for the next section."}
        weakest = self.weakest(doc, student)
        if weakest:
            c = doc["concepts"][weakest[0]]
            sec = self._section(doc, c["sections"][0])
            return {"action": "review", "section_id": sec["id"], "title": sec["title"],
                    "reason": f"All sections done. {c['label']} is your weakest topic."}
        return None

    @staticmethod
    def weakest(doc: dict[str, Any], student: dict[str, Any]) -> list[str]:
        mastery = student["concepts"]
        weak = [cid for cid in doc["concepts"]
                if knowledge.status_of(mastery.get(cid)) in ("weak", "learning")]
        return sorted(weak, key=lambda c: mastery[c]["p"])

    def progress(self, doc: dict[str, Any], student: dict[str, Any]) -> dict[str, Any]:
        mastery = student["concepts"]
        concepts = []
        for cid, c in doc["concepts"].items():
            rec = mastery.get(cid)
            concepts.append({
                "id": cid,
                "label": c["label"],
                "mastery": round(rec["p"], 3) if rec else 0.0,
                "attempts": rec["attempts"] if rec else 0,
                "status": knowledge.status_of(rec),
            })
        counts = {k: 0 for k in ("mastered", "learning", "weak", "unknown")}
        for c in concepts:
            counts[c["status"]] += 1
        secs = student["sections"]
        passed = sum(1 for s in doc["sections"] if secs.get(s["id"], {}).get("passed"))
        overall = (sum(c["mastery"] for c in concepts) / len(concepts)) if concepts else 0.0
        weak_ids = self.weakest(doc, student)
        history = [h for h in student["history"] if h["doc_id"] == doc["id"]][-10:]
        coach = student["coach"].get(doc["id"])
        return {
            "doc_id": doc["id"],
            "title": doc["title"],
            "overall": round(overall, 3),
            "sections_total": len(doc["sections"]),
            "sections_passed": passed,
            "counts": counts,
            "concepts": concepts,
            "weak": self.gaps(doc, student, weak_ids[:5]),
            "history": list(reversed(history)),
            "coach": coach["text"] if coach else offline.coach_note(
                doc["title"], [], [], [], None),
        }

    async def coach_note(self, doc: dict[str, Any], student: dict[str, Any],
                         next_step: dict[str, Any] | None) -> str:
        prog = self.progress(doc, student)
        by = {k: [c["label"] for c in prog["concepts"] if c["status"] == k]
              for k in ("mastered", "learning", "weak", "unknown")}
        nxt = next_step["title"] if next_step else None
        if not self.llm.enabled:
            return offline.coach_note(doc["title"], by["weak"], by["learning"],
                                      by["mastered"], nxt)
        gap_lines = [f"- {g['label']} ({g['status']}); likely root causes: "
                     + (", ".join(r["label"] for r in g["root_causes"]) or "none found")
                     for g in prog["weak"]]
        recent = [f"- {h['section_title']}: {h['score']}/{h['total']}" for h in prog["history"][:5]]
        user = f"""Write a short personal study note (3-4 sentences, under 90 words) for a \
student working through "{doc['title']}". Address them as "you". Name specific \
concepts. Say what is going well, the single most important gap and why it matters for \
what comes next, and the concrete next step.

Mastered: {', '.join(by['mastered']) or 'none yet'}
Learning: {', '.join(by['learning']) or 'none'}
Weak: {', '.join(by['weak']) or 'none'}
Not yet tested: {', '.join(by['unknown'][:8]) or 'none'}
Gaps traced through the prerequisite graph:
{chr(10).join(gap_lines) or '- none'}
Recent checks:
{chr(10).join(recent) or '- none'}
Recommended next: {nxt or 'free choice'}"""
        try:
            data = await self.llm.json(system=SYSTEM, user=user, name="coach",
                                       schema=COACH_SCHEMA, max_tokens=1500)
            return data["note"].strip()
        except LLMError as e:
            log.warning("coach note failed, using offline: %s", e)
            return offline.coach_note(doc["title"], by["weak"], by["learning"],
                                      by["mastered"], nxt)

    @staticmethod
    def _section(doc: dict[str, Any], section_id: str) -> dict[str, Any]:
        return next(s for s in doc["sections"] if s["id"] == section_id)
