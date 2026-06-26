"""FastAPI application factory for RackIQ."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.calendar import router as calendar_router
from .api.daily import router as daily_router
from .api.deals import router as deals_router
from .api.demand import router as demand_router
from .api.hedging import router as hedging_router
from .api.margin import router as margin_router
from .api.position import router as position_router
from .api.pricing import router as pricing_router
from .api.reconciliation import router as reconciliation_router
from .api.routes import router
from .api.scores import router as scores_router
from .api.studio import router as studio_router
from .api.variability import router as variability_router
from .api.weather import router as weather_router
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
    app.include_router(reconciliation_router)
    app.include_router(daily_router)
    app.include_router(demand_router)
    app.include_router(pricing_router)
    app.include_router(calendar_router)
    app.include_router(hedging_router)
    app.include_router(deals_router)
    app.include_router(variability_router)
    app.include_router(margin_router)
    app.include_router(weather_router)
    app.include_router(position_router)

    @app.get("/")
    def root():
        return {"name": "RackIQ API", "version": __version__, "docs": "/docs", "api": "/api"}

    return app


app = create_app()
