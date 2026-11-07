from __future__ import annotations

import dataclasses
import io

import pytest
from fastapi.testclient import TestClient

from edukindle import config, ingest, knowledge
from edukindle.api import create_app

SAMPLE = "calculus-i-derivatives"


@pytest.fixture
def client(tmp_path):
    cfg = dataclasses.replace(config.load(), data_dir=tmp_path, api_key=None, offline=True)
    with TestClient(create_app(cfg)) as c:
        yield c


def answer_all(client, quiz, pick):
    """pick(question index, n options) -> option index"""
    answers = {q["id"]: pick(i, len(q["options"])) for i, q in enumerate(quiz["questions"])}
    r = client.post(f"/api/quiz/{quiz['quiz_id']}/answers", json={"answers": answers})
    assert r.status_code == 200
    return r.json()


def correct_index(client, quiz_id, qid):
    svc = client.app.state.svc
    q = next(q for q in svc.store.quizzes[quiz_id]["questions"] if q["id"] == qid)
    return q["correct"]


def test_home_has_sample(client):
    home = client.get("/api/home").json()
    assert home["llm"]["enabled"] is False
    assert [d["id"] for d in home["documents"]] == [SAMPLE]
    assert home["continue"] is None


def test_section_paragraphs_are_normalised(client):
    toc = client.get(f"/api/documents/{SAMPLE}").json()
    assert len(toc["sections"]) == 7
    sec = client.get(f"/api/sections/{toc['sections'][5]['id']}").json()
    assert sec["title"] == "The Chain Rule"
    assert "\n\n\n" not in sec["text"] and len(sec["text"].split("\n\n")) == 5
    assert {c["label"] for c in sec["concepts"]} == {"Function Composition", "Chain Rule"}
    assert client.get("/api/home").json()["continue"]["section_id"] == sec["id"]


def test_quiz_hides_answers_and_grades(client):
    sid = f"{SAMPLE}.6"
    quiz = client.post("/api/quiz", json={"section_id": sid}).json()
    assert len(quiz["questions"]) == 3
    for q in quiz["questions"]:
        assert set(q) == {"id", "prompt", "options", "concept"}
        assert len(q["options"]) == 4

    # All wrong first time -> hints, no answers revealed, retry offered.
    res = answer_all(client, quiz,
                     lambda i, n: (correct_index(client, quiz["quiz_id"], f"q{i + 1}") + 1) % n)
    assert res["score"] == 0 and res["ready"] is False
    assert res["retry"] == ["q1", "q2", "q3"]
    for r in res["results"]:
        assert r["answer"] is None and r["hint"] and r["feedback"]
    assert all(m["after"] < m["before"] + 0.2 for m in res["mastery"])
    assert res["next"]["action"] in ("review", "reread")

    # Retry correctly -> answers revealed, counted as hinted.
    retry = {qid: correct_index(client, quiz["quiz_id"], qid) for qid in res["retry"]}
    res2 = client.post(f"/api/quiz/{quiz['quiz_id']}/answers", json={"answers": retry}).json()
    assert res2["score"] == 0  # first-attempt score is what counts
    assert all(r["correct"] and r["hinted"] and r["answer"] is not None
               for r in res2["results"])
    assert res2["retry"] == []

    # A third submission changes nothing.
    res3 = client.post(f"/api/quiz/{quiz['quiz_id']}/answers", json={"answers": retry}).json()
    assert res3["results"] == []


def test_mastery_progress_and_advance(client):
    sid = f"{SAMPLE}.1"
    for _ in range(2):
        quiz = client.post("/api/quiz", json={"section_id": sid}).json()
        res = answer_all(client, quiz,
                         lambda i, n: correct_index(client, quiz["quiz_id"], f"q{i + 1}"))
        assert res["score"] == 3
    assert res["ready"] is True
    assert res["next"] == {"action": "advance", "section_id": f"{SAMPLE}.2",
                           "title": "The Derivative as a Limit",
                           "reason": "You're ready for the next section."}

    prog = client.get(f"/api/progress/{SAMPLE}").json()
    assert prog["sections_passed"] == 1
    limits = next(c for c in prog["concepts"] if c["label"] == "Limits")
    assert limits["status"] == "mastered"
    assert prog["history"][0]["score"] == 3
    toc = client.get(f"/api/documents/{SAMPLE}").json()
    assert toc["sections"][0]["status"] == "passed"


def test_failing_chain_rule_traces_root_gap(client):
    # Fail the power-rule section, then fail the chain rule: the Architect
    # should send the student back to the power rule.
    for sid in (f"{SAMPLE}.3", f"{SAMPLE}.6"):
        quiz = client.post("/api/quiz", json={"section_id": sid}).json()
        res = answer_all(client, quiz, lambda i, n, q=quiz: (
            correct_index(client, q["quiz_id"], f"q{i + 1}") + 1) % n)
    gap = next(g for g in res["gaps"] if g["label"] == "Chain Rule")
    assert "Power Rule" in {r["label"] for r in gap["root_causes"]}
    assert res["next"]["action"] == "review"
    assert res["next"]["section_id"] != f"{SAMPLE}.6"


def test_graph_layout(client):
    g = client.get(f"/api/graph/{SAMPLE}").json()
    assert len(g["nodes"]) == 13
    pos = {n["id"]: n for n in g["nodes"]}
    for e in g["edges"]:
        assert pos[e["source"]]["y"] < pos[e["target"]]["y"]  # prerequisites above
    for n in g["nodes"]:
        assert 0 < n["x"] < 1 and 0 < n["y"] < 1
    svg = client.get(f"/api/graph/{SAMPLE}/svg")
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.text.count("<rect") == 13 * 3


def test_explain_modes_and_cache(client):
    sid = f"{SAMPLE}.2"
    for body in ({"mode": "summary"}, {"mode": "simple"}, {"mode": "paragraph", "paragraph": 2},
                 {"mode": "concept", "concept_id": f"{SAMPLE}/derivative"}):
        r = client.post("/api/explain", json={"section_id": sid, **body})
        assert r.status_code == 200, r.text
        assert r.json()["title"] and r.json()["body"]
    again = client.post("/api/explain", json={"section_id": sid, "mode": "summary"}).json()
    assert again["cached"] is True
    bad = client.post("/api/explain", json={"section_id": sid, "mode": "paragraph",
                                            "paragraph": 99})
    assert bad.status_code == 400
    assert client.get("/api/sections/nope.1").status_code == 404


def test_upload_text_document_gets_mapped_and_quizzed(client):
    text = "# Cell Biology\n\n" + "\n\n".join(
        f"## {name}\n\n" + " ".join([
            f"The {name.lower()} is an important structure inside eukaryotic cells.",
            f"Scientists study the {name.lower()} using microscopes and staining techniques.",
            "Membranes separate the interior of organelles from the surrounding cytoplasm.",
            "Energy production, protein synthesis and transport all depend on organelles.",
        ] * 6)
        for name in ("Nucleus", "Mitochondria", "Ribosomes"))
    r = client.post("/api/documents/upload",
                    files={"file": ("cells.md", io.BytesIO(text.encode()), "text/markdown")})
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    assert r.json()["sections"] == 3
    quiz = client.post("/api/quiz", json={"section_id": f"{doc_id}.2"}).json()
    assert len(quiz["questions"]) == 3
    g = client.get(f"/api/graph/{doc_id}").json()
    assert [n["label"] for n in g["nodes"]] == ["Nucleus", "Mitochondria", "Ribosomes"]
    assert client.delete(f"/api/documents/{doc_id}").json() == {"ok": True}


def test_dashboard_renders(client):
    html = client.get("/").text
    assert "Knowledge graph" in html and "<svg" in html
    assert "Curriculum Architect" in client.get("/dashboard/live").text


def test_bkt_moves_in_right_direction():
    p = knowledge.P_INIT
    up = knowledge.bkt_update(p, True)
    hinted = knowledge.bkt_update(p, True, hinted=True)
    down = knowledge.bkt_update(p, False)
    assert down < p < hinted < up


def test_long_text_is_chunked():
    paras = [" ".join(["word"] * 100)] * 20
    doc = ingest.build_document("Long", [("", "Big", paras)], "text")
    assert len(doc["sections"]) >= 3
    assert all(len(s["text"].split()) <= ingest.MAX_WORDS for s in doc["sections"])
