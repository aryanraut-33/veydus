# ─────────────────────────────────────────────────────────────────
# VEYDUS — FastAPI Main Application
# ─────────────────────────────────────────────────────────────────
# What:  Root FastAPI web application entrypoint exposing HTTP endpoints
#        for workers, healthchecks, and internal services.
# How:   Initializes FastAPI application instance, registers CORS middleware,
#        includes router modules, and handles lifespan events.
# Why:   Provides standard REST API interface for VEYDUS microservices and workers.
# Tools: FastAPI, CORSMiddleware.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from veydus.api.routers.internal_jobs import router as internal_jobs_router

app = FastAPI(
    title="VEYDUS API",
    description="Hierarchical, access-controlled RAG for enterprises",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(internal_jobs_router)


@app.get("/health", tags=["system"])
async def healthcheck() -> dict[str, Any]:
    """Health check endpoint for container probes."""
    return {"status": "ok", "service": "veydus-api"}
