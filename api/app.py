"""
Backend FastAPI — Radar Exynax API (mínimo).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Radar Exynax API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://radarexynax.com",
        "https://www.radarexynax.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}
