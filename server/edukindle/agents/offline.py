"""Heuristic stand-ins for the LLM agents.

Used when no OPENAI_API_KEY is configured, and as a fallback whenever an API
call fails, so a student mid-session never hits a dead end. Output has the same
shape as the LLM paths.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from typing import Any

OFFLINE_NOTE = "(Offline mode: set OPENAI_API_KEY on the server for AI explanations.)"

_STOP = set("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each either else
even every few for from further had has have having he her here hers him his how however i
if in into is it its itself just less like many may might more most much must my near
neither no nor not now of off often on once one only or other our out over own per rather
same see she should since so some such than that the their them then there these they this
those though through thus to too two under until up upon us use used uses using very was
way we well were what when where whether which while who whom whose why will with within
without would yet you your also called means given gives example first second next value
values number numbers make makes made find found write written another always never thing
things point points becomes become still simply
""".split())


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\"'])", text.replace("\n\n", " "))
    return [p.strip() for p in parts if p.strip()]


def key_terms(text: str, n: int = 12) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]{4,}", text)
    counts = Counter(w.lower() for w in words if w.lower() not in _STOP)
    return [w for w, _ in counts.most_common(n)]


def _clean_label(title: str) -> str:
    title = re.sub(r"\s*\(\d+/\d+\)$", "", title)
    title = re.sub(r"^(?:\d+(?:\.\d+)*\.?|chapter\s+\w+[.:]?)\s*", "", title, flags=re.I)
    return title.strip() or "Overview"


def extract_concepts(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """One concept per distinct section title, chained in reading order."""
    out: list[dict[str, Any]] = []
    by_label: dict[str, dict[str, Any]] = {}
    prev_key = None
    for s in doc["sections"]:
        label = _clean_label(s["title"])
        key = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:32] or f"s{s['index']}"
        if label in by_label:
            by_label[label]["sections"].append(s["index"] + 1)
            continue
        first = sentences(s["text"])[:1]
        c = {
            "key": key,
            "label": label,
            "description": first[0][:200] if first else "",
            "sections": [s["index"] + 1],
            "prerequisites": [prev_key] if prev_key else [],
        }
        by_label[label] = c
        out.append(c)
        prev_key = key
    return out[:40]


def cloze_quiz(doc: dict[str, Any], section: dict[str, Any], concept_ids: list[str],
               avoid: list[str], rng: random.Random) -> list[dict[str, Any]]:
    """Fill-in-the-blank questions built from the section's own sentences."""
    terms = key_terms(section["text"], 10)
    pool_terms = key_terms(" ".join(s["text"] for s in doc["sections"]), 40)
    cands = []
    for sent in dict.fromkeys(sentences(section["text"])):
        n = len(sent.split())
        if not 8 <= n <= 45:
            continue
        for rank, t in enumerate(terms):
            m = re.search(rf"\b{re.escape(t)}\b", sent, re.I)
            if m:
                cands.append((rank, sent, m.group(0)))
    rng.shuffle(cands)
    # Prefer unseen sentences, then the section's most central terms.
    cands.sort(key=lambda c: (any(c[1][:20] in a for a in avoid), c[0]))

    paras = section["text"].split("\n\n")
    questions = []
    used_terms: set[str] = set()
    used_sents: set[str] = set()
    for _, sent, term in cands:
        if term.lower() in used_terms or sent in used_sents:
            continue
        others = [t for t in pool_terms if t != term.lower() and t not in sent.lower()]
        if len(others) < 3:
            continue
        used_terms.add(term.lower())
        used_sents.add(sent)
        distractors = rng.sample(others[:15], 3)
        blanked = re.sub(rf"\b{re.escape(term)}\b", "_____", sent, count=1, flags=re.I)
        para = next((p for p in paras if sent[:40] in p), paras[0])
        questions.append({
            "prompt": f"Fill in the blank: “{blanked}”",
            "correct_answer": term.lower(),
            "distractors": [
                {"text": d, "misconception":
                    f"“{d}” appears in this material, but it doesn't fit this statement."}
                for d in distractors
            ],
            "explanation": f"The text says: “{sent}”",
            "hint": "Look again at the paragraph that begins “"
                    + " ".join(para.split()[:6]) + "…”",
            "concept": concept_ids[len(questions) % len(concept_ids)] if concept_ids else "",
        })
        if len(questions) == 3:
            break
    return questions


def explain(section: dict[str, Any], mode: str, paragraph: str | None,
            concept: dict[str, Any] | None) -> dict[str, str]:
    paras = section["text"].split("\n\n")
    if mode == "summary":
        points = [sentences(p)[0] for p in paras if sentences(p)][:6]
        body = "\n".join("• " + p for p in points)
        return {"title": f"Key points: {section['title']}", "body": body + "\n\n" + OFFLINE_NOTE}
    if mode == "paragraph" and paragraph:
        sents = sentences(paragraph)
        body = "In short: " + (sents[0] if sents else paragraph)
        if len(sents) > 1:
            body += "\n\nStep by step:\n" + "\n".join("• " + s for s in sents[1:])
        return {"title": "This paragraph, step by step", "body": body + "\n\n" + OFFLINE_NOTE}
    if mode == "concept" and concept:
        body = concept.get("description") or concept["label"]
        return {"title": concept["label"], "body": body + "\n\n" + OFFLINE_NOTE}
    # "simple": the opening sentence of every paragraph reads as a plain digest.
    body = "\n\n".join(sentences(p)[0] for p in paras if sentences(p))
    return {"title": f"In simple terms: {section['title']}", "body": body + "\n\n" + OFFLINE_NOTE}


def coach_note(doc_title: str, weak: list[str], learning: list[str], mastered: list[str],
               next_title: str | None) -> str:
    parts = []
    if mastered:
        parts.append(f"Solid so far: {', '.join(mastered[:4])}.")
    if weak:
        parts.append(f"Your biggest gaps are {', '.join(weak[:3])}. Re-read those sections "
                     "and retake their checks before moving on, since later topics build on them.")
    elif learning:
        parts.append(f"You're getting there with {', '.join(learning[:3])}; one more "
                     "check on each should lock them in.")
    if next_title:
        parts.append(f"Suggested next: {next_title}.")
    if not parts:
        parts.append(f"Start reading {doc_title} and take the 3-question check at the end "
                     "of each section. Your knowledge map fills in as you go.")
    return " ".join(parts)
