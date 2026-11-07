"""AI layout restoration for PDF sections."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from fastapi.testclient import TestClient

from edukindle import config, ingest, service
from edukindle.agents.typesetter import faithful
from edukindle.api import create_app
from edukindle.llm import LLMError

RAW = ("Learning objectives •Understand the use of pseudocode for algorithm description "
       "•Understand the design of iterative algorithms. The sum 𝑇(𝑛) = 𝑛(𝑛 + 1)/2 holds "
       "for every natural number and we prove it carefully by induction on the length.")
TIDY = ("Learning objectives:\n\n- Understand the use of pseudocode for algorithm description\n"
        "- Understand the design of iterative algorithms.\n\nThe sum\n\n"
        "$$T(n) = \\frac{n(n+1)}{2}$$\n\nholds for every natural number and we prove it "
        "carefully by induction on the length.")


def test_faithfulness_check():
    assert faithful(RAW, TIDY)
    assert not faithful(RAW, "Objectives: pseudocode, iteration, and a sum formula.")  # summary
    assert not faithful(RAW, TIDY + "\n\n" + TIDY)  # padded


class FakeLLM:
    enabled = True
    model = "fake"
    provider = "Fake"

    def __init__(self):
        self.reply = TIDY
        self.calls = 0
        self.delay = 0.0
        self.fail = False

    async def json(self, *, system, user, name, schema, max_tokens=4000, **_):
        assert name == "typeset", name
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise LLMError("boom")
        return {"markdown": self.reply}


@pytest.fixture
def setup(tmp_path):
    cfg = dataclasses.replace(config.load(), data_dir=tmp_path, api_key=None, offline=True)
    app = create_app(cfg)
    fake = FakeLLM()
    with TestClient(app) as client:
        svc = app.state.svc
        svc.llm = svc.typesetter.llm = fake
        svc.educator.llm = svc.architect.llm = type("Off", (), {"enabled": False})()
        doc = ingest.build_document("Notes", [("", f"Part {i}", [RAW]) for i in range(1, 5)],
                                    "pdf", svc.store.documents)
        svc.store.save_document(doc)
        yield client, svc, fake, doc["id"]


def test_opening_a_pdf_section_restores_its_layout(setup):
    client, svc, fake, doc_id = setup
    sec = client.get(f"/api/sections/{doc_id}.1").json()
    assert sec["layout_pending"] is False
    kinds = [b["t"] for b in sec["doc"]["blocks"]]
    assert kinds == ["p", "li", "li", "p", "math", "p"]
    stored = svc.store.documents[doc_id]["sections"][0]
    assert stored["raw"] == RAW and stored["layout"]["by"] == "ai"
    # The next sections are prepared in the background; reopening costs nothing.
    calls = fake.calls
    client.get(f"/api/sections/{doc_id}.1")
    assert fake.calls == calls


def test_unfaithful_layout_is_rejected_and_not_retried(setup):
    client, svc, fake, doc_id = setup
    fake.reply = "A short summary of the section."
    sec = client.get(f"/api/sections/{doc_id}.1").json()
    assert "Understand the use of pseudocode" in sec["text"]  # original kept
    stored = svc.store.documents[doc_id]["sections"][0]
    assert stored["layout"]["by"] == "rejected"
    assert not svc._needs_layout(svc.store.documents[doc_id], stored)


def test_model_errors_keep_the_text_and_retry_later(setup):
    client, svc, fake, doc_id = setup
    fake.fail = True
    sec = client.get(f"/api/sections/{doc_id}.2").json()
    assert sec["text"] == RAW
    stored = svc.store.documents[doc_id]["sections"][1]
    assert "layout" not in stored and svc._needs_layout(svc.store.documents[doc_id], stored)


def test_slow_layout_returns_extracted_text_now(setup, monkeypatch):
    client, svc, fake, doc_id = setup
    monkeypatch.setattr(service, "LAYOUT_WAIT_S", 0.05)
    fake.delay = 0.3
    sec = client.get(f"/api/sections/{doc_id}.3").json()
    assert sec["layout_pending"] is True and sec["text"] == RAW


def test_only_pdfs_are_typeset(setup):
    client, svc, fake, doc_id = setup
    client.get("/api/sections/calculus-i-derivatives.1")
    assert fake.calls == 0
