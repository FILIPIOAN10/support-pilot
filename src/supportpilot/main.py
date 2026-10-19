"""Application entry point:  uvicorn supportpilot.main:app"""

import contextvars
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from supportpilot.api import get_service, get_store, health, router
from supportpilot.config import get_settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdFilter())
log = logging.getLogger("supportpilot")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fail fast: build the agent now, so a missing key or an unreachable MongoDB stops the pod at start."""
    settings = get_settings()
    get_store()
    get_service()
    log.info("started provider=%s", settings.llm_provider)
    yield


app = FastAPI(title="SupportPilot", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(health)


STATIC = Path(__file__).parent / "static"
# The page only talks to this same origin and renders every piece of text with textContent, never innerHTML.
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
        "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    """A small page for trying the API: a customer chat and the support colleague's approval queue."""
    return FileResponse(STATIC / "index.html", headers=PAGE_HEADERS)


@app.middleware("http")
async def request_id_and_timing(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        if not request.url.path.startswith("/health"):
            log.info(
                "%s %s -> %d in %d ms",
                request.method,
                request.url.path,
                response.status_code,
                (time.perf_counter() - started) * 1000,
            )
    finally:
        request_id_var.reset(token)  # only after the access log line, so it carries the request id too
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal error"}, status_code=500)
