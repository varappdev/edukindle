"""Pages the server composes for the Kindle, as richtext documents.

The Kindle app only knows how to draw blocks; building the results and progress
pages here means their wording and layout can change without redeploying the
app to the device.
"""

from __future__ import annotations

from typing import Any

from .richtext import Doc, esc, inline

STATUS_WORD = {"mastered": "Mastered", "learning": "Learning", "weak": "Weak",
               "unknown": "Not tested"}


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _verdict(r: dict[str, Any]) -> str:
    if r["correct"]:
        return "Correct, with a hint" if r["hinted"] else "Correct"
    return "Not quite" if r["can_retry"] else "Not this time"


def results_doc(res: dict[str, Any], px: int | None,
                width: int | None = None) -> dict[str, Any]:
    d = Doc(px, width)
    for r in res["results"]:
        d.h(_verdict(r), 3)
        d.p(f"<i>{inline(r['prompt'])}</i>")
        d.p(f"<b>Your answer:</b> {inline(r['chosen_text'])}")
        d.p(inline(r["feedback"]))
        if r["hint"]:
            d.note(f"<b>Hint:</b> {inline(r['hint'])}")
        if not r["correct"] and r["answer_text"]:
            d.p(f"<b>Answer:</b> {inline(r['answer_text'])}. {inline(r['explanation'])}")

    if res["mastery"]:
        d.h("Mastery", 3)
        for m in res["mastery"]:
            d.li(f"{esc(m['label'])}: {_pct(m['before'])} → <b>{_pct(m['after'])}</b>")

    for g in res["gaps"]:
        roots = [f"{esc(r['label'])} ({'weak' if r['status'] == 'weak' else 'not checked yet'})"
                 for r in g["root_causes"]]
        if roots:
            d.note(f"<b>Knowledge gap:</b> {esc(g['label'])} builds on {', '.join(roots)}.")

    if res["next"]:
        d.p(f"<b>Curriculum Architect:</b> {esc(res['next']['reason'])}")
    return d.build()


def progress_doc(prog: dict[str, Any], px: int | None,
                 width: int | None = None) -> dict[str, Any]:
    d = Doc(px, width)
    d.h("Curriculum Architect", 3)
    d.p(inline(prog["coach"]))

    if prog["weak"]:
        d.h("Knowledge gaps", 3)
        for g in prog["weak"]:
            line = f"<b>{esc(g['label'])}</b>, {_pct(g['mastery'])}"
            roots = ", ".join(esc(r["label"]) for r in g["root_causes"])
            if roots:
                line += f". Root cause: {roots}"
            d.li(line)

    d.h("All concepts", 3)
    for c in prog["concepts"]:
        d.li(f"{esc(c['label'])}: {_pct(c['mastery'])} · {STATUS_WORD[c['status']]}")

    if prog["history"]:
        d.h("Recent checks", 3)
        for h in prog["history"]:
            d.li(f"{esc(h['section_title'])}: {h['score']}/{h['total']}")
    return d.build()
