"""Exercise the OpenAI-backed agent paths with a stubbed model."""

from __future__ import annotations

import dataclasses
import io
import time

import pytest
from fastapi.testclient import TestClient

from edukindle import config
from edukindle.api import create_app
from edukindle.llm import LLMError

SAMPLE = "calculus-i-derivatives"


class FakeLLM:
    enabled = True
    model = "fake-model"
    provider = "Fake"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fail: set[str] = set()

    async def json(self, *, system, user, name, schema, max_tokens=4000):
        self.calls.append((name, user))
        if name in self.fail:
            raise LLMError("boom")
        if name == "quiz":
            cid = user.split('id "', 1)[1].split('"', 1)[0]
            return {"questions": [{
                "concept": cid,
                "prompt": f"Question {i}?",
                "correct_answer": f"right {i}",
                "distractors": [{"text": f"wrong {i}.{k}", "misconception": f"because {k}"}
                                for k in range(3)],
                "explanation": "why",
                "hint": "think",
            } for i in range(3)]}
        if name == "explain":
            return {"title": "## Key points", "body": "**Bold** idea\n- one\n- two"}
        if name == "concepts":
            return {"concepts": [
                {"key": "osmosis", "label": "Osmosis", "description": "d", "sections": [1],
                 "prerequisites": []},
                {"key": "Active Transport", "label": "Active transport", "description": "d",
                 "sections": [2, 99], "prerequisites": ["osmosis", "missing"]},
            ]}
        if name == "coach":
            return {"note": "Keep going."}
        raise AssertionError(name)


@pytest.fixture
def setup(tmp_path):
    cfg = dataclasses.replace(config.load(), data_dir=tmp_path, api_key=None, offline=True)
    app = create_app(cfg)
    fake = FakeLLM()
    with TestClient(app) as c:
        svc = app.state.svc
        svc.llm = svc.educator.llm = svc.architect.llm = fake
        yield c, fake


def test_llm_quiz_is_shuffled_and_graded(setup):
    client, fake = setup
    quiz = client.post("/api/quiz", json={"section_id": f"{SAMPLE}.6"}).json()
    assert quiz["source"] == "openai"
    assert "chain-rule" in fake.calls[0][1] or "composition" in fake.calls[0][1]
    svc = client.app.state.svc
    stored = svc.store.quizzes[quiz["quiz_id"]]
    for q in stored["questions"]:
        assert q["options"][q["correct"]].startswith("right")
        assert q["rationales"][q["correct"]] == "why"
    answers = {q["id"]: q["correct"] for q in stored["questions"]}
    res = client.post(f"/api/quiz/{quiz['quiz_id']}/answers", json={"answers": answers}).json()
    assert res["score"] == 3


def test_llm_failure_falls_back_to_bank(setup):
    client, fake = setup
    fake.fail.add("quiz")
    quiz = client.post("/api/quiz", json={"section_id": f"{SAMPLE}.6"}).json()
    assert quiz["source"] == "offline" and len(quiz["questions"]) == 3


def test_llm_explain_keeps_markdown_and_renders_doc(setup):
    client, _ = setup
    out = client.post("/api/explain", json={"section_id": f"{SAMPLE}.1", "mode": "summary"}).json()
    assert out["title"] == "Key points"  # header text is plain
    assert out["body"] == "**Bold** idea\n- one\n- two"
    blocks = out["doc"]["blocks"]
    assert [b["t"] for b in blocks] == ["p", "li", "li"]
    assert blocks[0]["m"] == "<b>Bold</b> idea"
    assert [b["mark"] for b in blocks[1:]] == ["\u2022", "\u2022"]


def test_llm_concept_mapping_sanitised(setup):
    client, _ = setup
    text = "# Cells\n\n## Osmosis\n\n" + "Water moves. " * 80 + "\n\n## Pumps\n\n" + "ATP. " * 80
    r = client.post("/api/documents/upload",
                    files={"file": ("cells.md", io.BytesIO(text.encode()), "text/markdown")})
    doc_id = r.json()["id"]
    for _ in range(100):  # mapping runs in the background
        g = client.get(f"/api/graph/{doc_id}").json()
        if g["concepts_ready"]:
            break
        time.sleep(0.02)
    assert {n["label"] for n in g["nodes"]} == {"Osmosis", "Active transport"}
    # Out-of-range section and unknown prerequisite are dropped.
    assert g["edges"] == [{"source": f"{doc_id}/osmosis", "target": f"{doc_id}/active-transport"}]
    at = next(n for n in g["nodes"] if n["label"] == "Active transport")
    assert at["sections"] == [f"{doc_id}.2"]
