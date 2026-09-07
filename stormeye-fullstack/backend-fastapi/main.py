"""
StormEye AI — FastAPI backend
Serves live tropical cyclone data from the US National Hurricane Center.

NOTE ON API KEYS: none are needed here. NHC's CurrentStorms.json is a free,
public, unauthenticated government feed. There is no official public API for
JTWC (Western Pacific / Indian Ocean / Southern Hemisphere storms) — no key
exists to request. This backend is honest about that limitation: storms
outside NHC's coverage (Atlantic + Eastern/Central Pacific) are simply not
returned by /api/storms.
"""

import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("PORT", 5000))
CORS_ORIGIN = os.getenv("CORS_ORIGIN", "*")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_MS", 300000)) / 1000
NHC_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"

app = FastAPI(title="StormEye AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN] if CORS_ORIGIN != "*" else ["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---- tiny in-memory cache (avoids hammering NHC on every page load) ----
_cache = {"data": None, "fetched_at": 0.0}


def to_category(classification: str, wind_kt: float):
    """Saffir-Simpson-ish classifier from NHC's classification code + wind."""
    c = (classification or "").upper()
    if c in ("TD", "LO", "DB", "WV"):
        return "TD"
    if c in ("TS", "SS", "STS"):
        return "TS"

    # HU, PTC, EX or unknown — fall back to wind-speed thresholds (knots)
    if wind_kt >= 137:
        return 5
    if wind_kt >= 113:
        return 4
    if wind_kt >= 96:
        return 3
    if wind_kt >= 83:
        return 2
    if wind_kt >= 64:
        return 1
    if wind_kt >= 34:
        return "TS"
    return "TD"


def basin_from_id(storm_id: str) -> str:
    prefix = (storm_id or "")[:2].lower()
    return {
        "al": "Atlantic",
        "ep": "Eastern Pacific",
        "cp": "Central Pacific",
    }.get(prefix, "Unknown basin")


def normalize_storm(raw: dict) -> dict:
    wind_kt = float(raw.get("intensity") or 0)
    wind_kmh = round(wind_kt * 1.852)
    pressure = raw.get("pressure")
    pressure = float(pressure) if pressure not in (None, "") else None
    cat = to_category(raw.get("classification"), wind_kt)
    advisory = raw.get("publicAdvisory") or {}
    movement_speed = raw.get("movementSpeed")

    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "lat": raw.get("latitudeNumeric"),
        "lng": raw.get("longitudeNumeric"),
        "cat": cat,
        "wind": wind_kmh,
        "windKt": wind_kt,
        "pressure": pressure,
        "basin": basin_from_id(raw.get("id")),
        "classification": raw.get("classification"),
        "movementDir": raw.get("movementDir"),
        "movementSpeedKmh": round(movement_speed * 1.852) if movement_speed is not None else None,
        "lastUpdate": raw.get("lastUpdate"),
        "advisoryUrl": advisory.get("url"),
        "source": "NHC",
    }


async def fetch_storms():
    now = time.time()
    if _cache["data"] is not None and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _cache["data"], True, _cache["fetched_at"]

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            NHC_URL,
            headers={"User-Agent": "StormEyeAI/1.0 (educational project)"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"NHC feed returned {resp.status_code}")

    payload = resp.json()
    raw_storms = payload.get("activeStorms") or []
    storms = [
        normalize_storm(s)
        for s in raw_storms
        if s.get("latitudeNumeric") is not None and s.get("longitudeNumeric") is not None
    ]

    _cache["data"] = storms
    _cache["fetched_at"] = now
    return storms, False, now


@app.get("/api/storms")
async def get_storms():
    try:
        storms, cached, fetched_at = await fetch_storms()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach the NHC feed: {exc}")

    return {
        "ok": True,
        "count": len(storms),
        "cached": cached,
        "fetchedAt": datetime.fromtimestamp(fetched_at, tz=timezone.utc).isoformat(),
        "coverage": "NHC: Atlantic + Eastern/Central Pacific only",
        "storms": storms,
    }


@app.get("/health")
async def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
