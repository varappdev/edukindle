"""HTTP API for the Kindle client plus the web dashboard. No auth by design."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from . import config, dashboard, knowledge
from .service import BadRequest, EduKindle, NotFound

log = logging.getLogger("edukindle.api")

MAX_UPLOAD = 40 * 1024 * 1024


def display(request: Request) -> dict[str, int | None]:
    """The Kindle sends its reading-text size and page width (device pixels) so
    display maths is rendered to match its text and wrapped to fit its page."""
    out: dict[str, int | None] = {}
    for key, header in (("px", "x-ek-px"), ("width", "x-ek-width")):
        try:
            out[key] = int(request.headers[header])
        except (KeyError, ValueError):
            out[key] = None
    return out


class ExplainReq(BaseModel):
    section_id: str
    mode: str = "summary"
    paragraph: int | None = None
    concept_id: str | None = None


class QuizReq(BaseModel):
    section_id: str


class AnswersReq(BaseModel):
    answers: dict[str, int] = Field(default_factory=dict)


class TextDoc(BaseModel):
    title: str
    text: str


def create_app(cfg: config.Config | None = None) -> FastAPI:
    cfg = cfg or config.load()
    svc = EduKindle(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await svc.startup()
        log.info("EduKindle ready: %d documents, LLM %s", len(svc.store.documents),
                 svc.llm.model if svc.llm.enabled else "offline")
        yield
        await svc.shutdown()

    app = FastAPI(title="EduKindle", version="0.1.0", lifespan=lifespan)
    app.state.svc = svc

    @app.exception_handler(NotFound)
    async def _nf(_: Request, e: NotFound):
        return JSONResponse({"error": str(e)}, status_code=404)

    @app.exception_handler(BadRequest)
    async def _br(_: Request, e: BadRequest):
        return JSONResponse({"error": str(e)}, status_code=400)

    @app.middleware("http")
    async def timing(request: Request, call_next):
        t0 = time.perf_counter()
        resp = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        resp.headers["X-Response-Time"] = f"{ms:.1f}ms"
        if request.url.path.startswith("/api/"):
            log.info("%s %s %d %.1fms", request.method, request.url.path,
                     resp.status_code, ms)
        return resp

    # -- Kindle API ---------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        return {"ok": True, "llm": svc.llm.enabled, "provider": svc.llm.provider,
                "model": svc.llm.model}

    @app.get("/api/home")
    async def home():
        return svc.home()

    @app.get("/api/documents/{doc_id}")
    async def toc(doc_id: str):
        return svc.toc(doc_id)

    @app.post("/api/documents")
    async def add_text(doc: TextDoc):
        name = doc.title if doc.title.lower().endswith((".md", ".txt")) else doc.title + ".md"
        text = doc.text if doc.text.lstrip().startswith("#") else f"# {doc.title}\n\n{doc.text}"
        return await svc.add_document(name, text.encode())

    @app.post("/api/documents/upload")
    async def upload(file: UploadFile = File(...)):
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise BadRequest("file too large (40 MB max)")
        return await svc.add_document(file.filename or "upload.txt", data)

    @app.delete("/api/documents/{doc_id}")
    async def delete(doc_id: str):
        svc.delete_document(doc_id)
        return {"ok": True}

    @app.get("/api/sections/{section_id}")
    async def section(section_id: str, request: Request):
        return svc.open_section(section_id, **display(request))

    @app.post("/api/explain")
    async def explain(req: ExplainReq, request: Request):
        return await svc.explain(req.section_id, req.mode, req.paragraph, req.concept_id,
                                 **display(request))

    @app.post("/api/quiz")
    async def quiz(req: QuizReq):
        return await svc.start_quiz(req.section_id)

    @app.post("/api/quiz/{quiz_id}/answers")
    async def answers(quiz_id: str, req: AnswersReq, request: Request):
        return svc.answer(quiz_id, req.answers, **display(request))

    @app.get("/api/progress/{doc_id}")
    async def progress(doc_id: str, request: Request):
        return svc.progress(doc_id, with_doc=True, **display(request))

    @app.get("/api/graph/{doc_id}")
    async def graph(doc_id: str):
        return svc.graph(doc_id)

    @app.get("/api/graph/{doc_id}/svg")
    async def graph_svg(doc_id: str):
        return Response(knowledge.graph_svg(svc.graph(doc_id)), media_type="image/svg+xml")

    @app.post("/api/reset")
    async def reset():
        svc.reset_progress()
        return {"ok": True}

    # -- Web dashboard ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(doc: str | None = None):
        return dashboard.page(svc, doc)

    @app.get("/dashboard/live", response_class=HTMLResponse)
    async def live(doc: str | None = None):
        return dashboard.live(svc, doc)

    @app.post("/dashboard/upload")
    async def dash_upload(file: UploadFile = File(...)):
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise BadRequest("file too large (40 MB max)")
        out = await svc.add_document(file.filename or "upload.txt", data)
        return RedirectResponse(f"/?doc={out['id']}", status_code=303)

    return app
