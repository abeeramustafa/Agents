"""SourceDesk HTTP entrypoint.

One process serves the page and the API so the browser stays same-origin.
Session state will live in memory, so deployment runs a single worker.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.data_store import load_catalog
from app.llm_client import LLMError, call_json
from app.orchestrator import FlowError, Orchestrator
from app.schemas import DecisionIn, LlmPing, MessageIn, SessionIdIn, StageIn, SupplierRetryIn, SupplierSelectIn
from app.session_store import SessionStore

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sourcedesk")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # A bad CSV raises CatalogError here, before the server accepts traffic.
    catalog = load_catalog()
    _app.state.catalog = catalog
    meta = catalog.catalog_meta()
    log.info(
        "config loaded port=%s auditor=%s cost_allowance=%s model_set=%s",
        settings.port,
        settings.enable_auditor,
        settings.cost_allowance,
        bool(settings.llm_model),
    )
    log.info(
        "catalog loaded products=%s suppliers=%s offers=%s",
        meta["product_count"],
        meta["supplier_count"],
        meta["offer_count"],
    )
    _app.state.orchestrator = Orchestrator(catalog, SessionStore())
    yield


app = FastAPI(title="SourceDesk", lifespan=lifespan)

# No frontend build. The HTML, CSS, and JS are files a judge can open.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    # Hosts call this to decide the container is up. It must not touch the LLM.
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/llm-check")
async def llm_check():
    # A one-line call so a bad key shows up before the demo, not during it.
    try:
        ping = await call_json(
            "Reply with JSON only. Set ok to true and reply to pong.",
            "Return JSON for a health check.",
            LlmPing,
            0,
        )
    except LLMError as exc:
        log.warning("llm-check failed: %s", exc)
        return JSONResponse(
            status_code=502,
            content={
                "error": str(exc),
                "hint": "Check LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL in .env, then restart the server.",
            },
        )
    return {"status": "ok", "model": settings.llm_model, "reply": ping.reply}


def _fail(error: str, hint: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "hint": hint})


@app.exception_handler(RequestValidationError)
async def bad_request(_request, _exc: RequestValidationError) -> JSONResponse:
    return _fail("That request was not understood.", "Reload the page and try again.")


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception) -> JSONResponse:
    # The page shows a sentence. The traceback stays in the server log.
    if isinstance(exc, FlowError):
        return _fail(exc.error, exc.hint)
    if isinstance(exc, LLMError):
        log.warning("llm failed: %s", exc)
        return _fail(str(exc), "Check the model settings in .env, then try again.", 502)
    log.exception("request failed")
    return _fail("Something went wrong on the server.", "Try again. The catalog is still loaded.", 500)


@app.get("/api/catalog/meta")
def catalog_meta() -> dict:
    return app.state.orchestrator.catalog.catalog_meta()


@app.post("/api/session")
def create_session() -> dict:
    session = app.state.orchestrator.sessions.create()
    return app.state.orchestrator.view(session)


@app.get("/api/session/{session_id}")
def read_session(session_id: str):
    try:
        session = app.state.orchestrator.require(session_id)
    except FlowError as exc:
        return _fail(exc.error, exc.hint, 404)
    return app.state.orchestrator.view(session)


@app.post("/api/message")
async def post_message(body: MessageIn):
    try:
        return await app.state.orchestrator.handle_message(body.session_id, body.text)
    except FlowError as exc:
        return _fail(exc.error, exc.hint)
    except LLMError as exc:
        log.warning("message failed: %s", exc)
        return _fail(str(exc), "Check the model settings in .env, then try again.", 502)


@app.post("/api/products/decision")
def post_decision(body: DecisionIn):
    try:
        return app.state.orchestrator.set_decision(body.session_id, body.product_id, body.decision)
    except FlowError as exc:
        return _fail(exc.error, exc.hint)


@app.post("/api/stage/back")
def post_back(body: StageIn):
    try:
        return app.state.orchestrator.go_back(body.session_id, body.stage)
    except FlowError as exc:
        return _fail(exc.error, exc.hint)


@app.post("/api/stage/suppliers")
async def post_suppliers(body: SessionIdIn):
    try:
        return await app.state.orchestrator.request_suppliers(body.session_id)
    except FlowError as exc:
        return _fail(exc.error, exc.hint, 409)


@app.post("/api/suppliers/select")
def post_supplier_select(body: SupplierSelectIn):
    try:
        return app.state.orchestrator.select_suppliers(
            body.session_id, body.product_id, body.supplier_ids
        )
    except FlowError as exc:
        return _fail(exc.error, exc.hint)


@app.post("/api/suppliers/retry")
async def post_supplier_retry(body: SupplierRetryIn):
    try:
        return await app.state.orchestrator.retry_supplier(body.session_id, body.product_id)
    except FlowError as exc:
        return _fail(exc.error, exc.hint)
    except LLMError as exc:
        log.warning("supplier retry failed: %s", exc)
        return _fail(str(exc), "Use the retry button on that product again.", 502)


@app.post("/api/stage/final")
async def post_final(body: SessionIdIn):
    try:
        return await app.state.orchestrator.request_final(body.session_id)
    except FlowError as exc:
        return _fail(exc.error, exc.hint, 409)
    except LLMError as exc:
        log.warning("final plan failed: %s", exc)
        return _fail(str(exc), "Your supplier picks are saved. Try again.", 502)
