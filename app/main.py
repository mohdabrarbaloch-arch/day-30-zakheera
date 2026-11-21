"""FastAPI application factory and entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import get_settings
from app.database import Base, engine
from app.routers import admin, auth, documents, query, workspaces

logger = logging.getLogger("zakheera")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_all is enough for this schema. It is not a migration system —
    # changing a column later needs Alembic, which the README says plainly.
    Base.metadata.create_all(bind=engine)
    settings = get_settings()
    logger.info(
        "Zakheera %s starting (environment=%s, llm=%s)",
        __version__,
        settings.environment,
        "enabled" if settings.llm_enabled else "disabled",
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Zakheera",
        version=__version__,
        description=(
            "A retrieval-augmented knowledge base that cites its sources and "
            "abstains when the corpus does not contain the answer."
        ),
        lifespan=lifespan,
        # Interactive docs are a development convenience. In production they
        # describe every endpoint and schema to anyone who asks.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # FastAPI's default body is a list of dicts that includes the raw
        # input, which can echo a password back to the client. This keeps the
        # useful part and drops the rest.
        errors = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Validation failed", "errors": errors},
        )

    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(documents.router)
    app.include_router(query.router)
    app.include_router(admin.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
