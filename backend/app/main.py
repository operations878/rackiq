"""FastAPI application factory for RackIQ."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.routes import router
from .api.scores import router as scores_router
from .api.studio import router as studio_router
from .config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="RackIQ API", version=__version__,
                  description="Customer demand & margin intelligence for wholesale fuel terminals.")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(studio_router)
    app.include_router(scores_router)

    @app.get("/")
    def root():
        return {"name": "RackIQ API", "version": __version__, "docs": "/docs", "api": "/api"}

    return app


app = create_app()
