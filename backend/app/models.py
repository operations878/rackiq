"""Lightweight Pydantic response models (used where validation is cheap and safe)."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    profile: str
