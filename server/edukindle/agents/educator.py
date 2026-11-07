"""Agent 1: the Socratic Educator.

Writes the 3-question checks, explains text on demand, grades answers and
decides whether the student is ready to advance.

Latency design: the expensive thinking happens when a quiz is *generated*
(prefetched in the background while the student is still reading). Each
question carries a misconception note for every wrong option plus a Socratic
hint, so grading and feedback are instant table lookups on submit.
"""

from __future__ import annotations

import logging
import random
import re
import uuid
from typing import Any

from .. import knowledge
from ..llm import INT, LLM, LLMError, STR, arr, obj
from ..store import now
from . import offline

log = logging.getLogger("edukindle.educator")

SYSTEM = """You are the Socratic Educator inside EduKindle, a distraction-free study \
app on an E-ink Kindle. You help a student understand textbook material and check \
their comprehension. Be warm, precise and brief. Write plain text only: no Markdown, \
no LaTeX. Write math inline with plain characters and Unicode (x², √x, f'(x), dy/dx, π)."""

_QUESTION = obj({
    "concept": STR,
    "prompt": STR,
    "correct_answer": STR,
    "distractors": arr(obj({"text": STR, "misconception": STR})),
    "explanation": STR,
    "hint": STR,
})
QUIZ_SCHEMA = obj({"questions": arr(_QUESTION)})
EXPLAIN_SCHEMA = obj({"title": STR, "body": STR})

DIFFICULTY = {
    "weak": "foundational: check the core definition and the most basic use",
    "unknown": "standard: mix recall of the key idea with one straightforward application",
    "learning": "standard-to-hard: favour applying the idea over recalling it",
    "mastered": "challenging: multi-step application or a subtle edge case",
}


def _shuffle_question(q: dict[str, Any], qid: str, rng: random.Random) -> dict[str, Any]:
    """Place the correct answer at a random position (LLMs favour option A)."""
    opts = [(q["correct_answer"], q["explanation"])]
    opts += [(d["text"], d["misconception"]) for d in q["distractors"][:3]]
    rng.shuffle(opts)
    correct = next(i for i, (t, _) in enumerate(opts) if t == q["correct_answer"])
    return {
        "id": qid,
        "prompt": q["prompt"].strip(),
        "options": [t.strip() for t, _ in opts],
        "rationales": [r.strip() for _, r in opts],
        "correct": correct,
        "concept": q["concept"],
        "explanation": q["explanation"].strip(),
        "hint": q["hint"].strip(),
    }


def _valid(q: dict[str, Any]) -> bool:
    return (bool(q.get("prompt")) and bool(q.get("correct_answer"))
            and len(q.get("distractors", [])) >= 3
            and len({q["correct_answer"], *(d["text"] for d in q["distractors"][:3])}) == 4)


def _plain(text: str) -> str:
    """Strip any Markdown the model slipped in; the Kindle renders plain text."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.M)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class SocraticEducator:
    def __init__(self, llm: LLM):
        self.llm = llm

    # -- quiz generation ----------------------------------------------------

    async def generate_quiz(self, doc: dict[str, Any], section: dict[str, Any],
                            mastery: dict[str, Any], avoid: list[str]) -> dict[str, Any]:
        rng = random.Random()
        cids = section["concepts"] or list(doc["concepts"])[:1]
        raw: list[dict[str, Any]] = []
        source = "offline"

        if self.llm.enabled:
            try:
                raw = await self._llm_quiz(doc, section, cids, mastery, avoid)
                source = "openai"
            except LLMError as e:
                log.warning("quiz generation failed, using offline bank: %s", e)

        if len(raw) < 3:
            bank = [q for q in section.get("quiz_bank", []) if _valid(q)]
            fresh = [q for q in bank if q["prompt"][:60] not in avoid]
            rng.shuffle(fresh)
            stale = [q for q in bank if q not in fresh]
            rng.shuffle(stale)
            raw = [dict(q) for q in (raw + fresh + stale)[:3]]
            for q in raw:
                if q.get("concept") and "/" not in q["concept"]:
                    q["concept"] = f"{doc['id']}/{q['concept']}"
        if len(raw) < 3:
            raw += offline.cloze_quiz(doc, section, cids, avoid, rng)[: 3 - len(raw)]

        questions = [_shuffle_question(q, f"q{i + 1}", rng) for i, q in enumerate(raw[:3])]
        for q in questions:
            if q["concept"] not in doc["concepts"]:
                q["concept"] = cids[0] if cids else ""
        return {
            "id": uuid.uuid4().hex[:12],
            "section_id": section["id"],
            "doc_id": doc["id"],
            "created": now(),
            "source": source,
            "questions": questions,
            "attempts": {},       # qid -> [chosen option, ...]
            "first_score": None,
            "served": False,
        }

    async def _llm_quiz(self, doc, section, cids, mastery, avoid) -> list[dict[str, Any]]:
        concepts = doc["concepts"]
        lines = []
        for cid in cids:
            st = knowledge.status_of(mastery.get(cid))
            lines.append(f'- id "{cid}": {concepts[cid]["label"]} '
                         f'(student: {st}; target difficulty {DIFFICULTY[st]})')
        avoid_txt = "\n".join(f"- {a}" for a in avoid[-9:]) or "- (none)"
        user = f"""Write a 3-question multiple-choice comprehension check for this section.

Document: {doc['title']}
Section: {section['title']}

Concepts in this section and the student's current mastery:
{chr(10).join(lines)}

Rules:
- Exactly 3 questions. Cover the concepts above, giving more weight to weak or unknown ones.
- Set "concept" to one of the concept ids above, exactly as written.
- Each question has one correct_answer and exactly 3 distractors. Every option under 90 characters.
- Distractors must be plausible and each must reflect a specific, common misconception. \
In "misconception", tell the student (second person, 1-2 sentences) what thinking leads to \
that answer and why it's wrong, without stating the correct answer.
- "hint": a Socratic nudge (a question or pointer back to the text) that helps the student \
find the answer without giving it away.
- "explanation": 1-2 sentences on why the correct answer is right.
- Test understanding, not trivia. Answerable from the section text alone.
- Don't repeat these earlier questions:
{avoid_txt}

Section text:
\"\"\"
{section['text'][:9000]}
\"\"\""""
        data = await self.llm.json(system=SYSTEM, user=user, name="quiz", schema=QUIZ_SCHEMA)
        return [q for q in data.get("questions", []) if _valid(q)][:3]

    # -- explanations --------------------------------------------------------

    async def explain(self, doc: dict[str, Any], section: dict[str, Any], mode: str,
                      paragraph: str | None = None,
                      concept: dict[str, Any] | None = None,
                      concept_status: str = "unknown") -> dict[str, str]:
        if not self.llm.enabled:
            return offline.explain(section, mode, paragraph, concept)

        if mode == "summary":
            task = ("Summarise this section as 4-6 short bullet points (start each line "
                    "with '• '), then one line starting 'Big idea:'. Title: 'Key points'.")
        elif mode == "simple":
            task = ("Re-explain this whole section in simple terms for a student who "
                    "found it confusing: short paragraphs, one everyday analogy, and one "
                    "small worked example if it suits the topic. Under 250 words.")
        elif mode == "paragraph":
            task = ("The student tapped the paragraph below because it confused them. "
                    "Explain it plainly in under 150 words: what it says, why it matters "
                    "in this section, and one tiny example. End with one short question "
                    f"they can ask themselves to check understanding.\n\nParagraph:\n{paragraph}")
        else:
            task = (f"Explain the concept \"{concept['label']}\" as it is used in this "
                    f"material. The student's mastery of it is: {concept_status}. Pitch it "
                    "accordingly, in under 200 words, with one worked example and the most "
                    "common mistake students make with it.")

        user = (f"Document: {doc['title']}\nSection: {section['title']}\n\n{task}\n\n"
                f"Section text:\n\"\"\"\n{section['text'][:9000]}\n\"\"\"")
        try:
            data = await self.llm.json(system=SYSTEM, user=user, name="explain",
                                       schema=EXPLAIN_SCHEMA, max_tokens=2500)
        except LLMError as e:
            log.warning("explain failed, using offline: %s", e)
            return offline.explain(section, mode, paragraph, concept)
        return {"title": _plain(data["title"])[:80], "body": _plain(data["body"])}

    # -- grading ---------------------------------------------------------------

    @staticmethod
    def grade(quiz: dict[str, Any], answers: dict[str, int]) -> list[dict[str, Any]]:
        """Record answers and return per-question feedback.

        First attempt wrong -> misconception + hint, may retry.
        Second attempt -> correct answer revealed either way.
        """
        results = []
        for q in quiz["questions"]:
            if q["id"] not in answers:
                continue
            chosen = int(answers[q["id"]])
            if not 0 <= chosen < len(q["options"]):
                continue
            tries = quiz["attempts"].setdefault(q["id"], [])
            if tries and (tries[-1] == q["correct"] or len(tries) >= 2):
                continue  # already settled: answered right, or answer revealed
            tries.append(chosen)
            ok = chosen == q["correct"]
            attempt = len(tries)
            reveal = ok or attempt >= 2
            results.append({
                "id": q["id"],
                "concept": q["concept"],
                "prompt": q["prompt"],
                "chosen": chosen,
                "chosen_text": q["options"][chosen],
                "correct": ok,
                "attempt": attempt,
                "hinted": attempt > 1,
                "can_retry": not reveal,
                "answer": q["correct"] if reveal else None,
                "answer_text": q["options"][q["correct"]] if reveal else None,
                "feedback": q["explanation"] if ok else q["rationales"][chosen],
                "hint": q["hint"] if not reveal else None,
                "explanation": q["explanation"] if reveal else None,
            })
        if quiz["first_score"] is None and all(q["id"] in quiz["attempts"]
                                               for q in quiz["questions"]):
            quiz["first_score"] = sum(1 for q in quiz["questions"]
                                      if quiz["attempts"][q["id"]][0] == q["correct"])
        return results

    @staticmethod
    def ready_to_advance(quiz: dict[str, Any], mastery: dict[str, Any]) -> bool:
        """At least 2/3 right first time, and no tested concept still weak."""
        if quiz["first_score"] is None or quiz["first_score"] < 2:
            return False
        tested = {q["concept"] for q in quiz["questions"]}
        return all(knowledge.status_of(mastery.get(c)) != "weak" for c in tested)

    @staticmethod
    def message(score: int | None, total: int, ready: bool, retry_left: int) -> str:
        if score is None:
            return "Answer every question to see how you did."
        if score == total:
            return "Perfect score. You clearly understand this section."
        if ready:
            return (f"{score} of {total} right first time. Good enough to move on; "
                    "glance over the feedback for the one you missed.")
        if retry_left:
            return (f"{score} of {total} right first time. Read the hints and try the "
                    "ones you missed again. Working it out yourself is what makes it stick.")
        return (f"{score} of {total}. This section hasn't clicked yet. That's normal. "
                "Review it, or the gap underneath it, then take a fresh check.")
