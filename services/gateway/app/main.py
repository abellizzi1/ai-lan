import hashlib
import os
from contextlib import asynccontextmanager
from datetime import date

import asyncpg
import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

DATABASE_URL = os.environ["DATABASE_URL"]
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
PROVIDER_API_KEY = os.environ["PROVIDER_API_KEY"]
PROVIDER_API_BASE = os.environ["PROVIDER_API_BASE"].rstrip("/")
DAILY_REQUEST_LIMIT = int(os.getenv("DAILY_REQUEST_LIMIT", "100"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with app.state.db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS request_usage (
              api_key_hash TEXT NOT NULL,
              usage_date DATE NOT NULL,
              requests INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (api_key_hash, usage_date)
            );
            CREATE TABLE IF NOT EXISTS request_log (
              id BIGSERIAL PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              api_key_hash TEXT NOT NULL,
              model TEXT,
              status_code INTEGER NOT NULL,
              client_ip TEXT
            );
        """)
    yield
    await app.state.db.close()


app = FastAPI(title="Private AI Gateway", lifespan=lifespan)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def authorize_and_charge(key: str | None, request: Request) -> str:
    if not key or key != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    hashed = key_hash(key)
    async with app.state.db.acquire() as conn:
        used = await conn.fetchval("""
          INSERT INTO request_usage (api_key_hash, usage_date, requests)
          VALUES ($1, $2, 1)
          ON CONFLICT (api_key_hash, usage_date) DO UPDATE
            SET requests = request_usage.requests + 1
            WHERE request_usage.requests < $3
          RETURNING requests
        """, hashed, date.today(), DAILY_REQUEST_LIMIT)
    if used is None:
        raise HTTPException(status_code=429, detail="Daily request quota exceeded")
    return hashed


async def log_request(key_digest: str, model: str | None, status: int, client_ip: str | None):
    async with app.state.db.acquire() as conn:
        await conn.execute(
            "INSERT INTO request_log (api_key_hash, model, status_code, client_ip) VALUES ($1, $2, $3, $4)",
            key_digest, model, status, client_ip,
        )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, x_api_key: str | None = Header(default=None)):
    key_digest = await authorize_and_charge(x_api_key, request)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Request body must be JSON")
    if payload.get("stream"):
        raise HTTPException(status_code=400, detail="Streaming is not enabled in this starter stack")

    model = payload.get("model")
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            upstream = await client.post(
                f"{PROVIDER_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {PROVIDER_API_KEY}"},
                json=payload,
            )
        content_type = upstream.headers.get("content-type", "application/json")
        await log_request(key_digest, model, upstream.status_code, request.client.host if request.client else None)
        if "application/json" in content_type:
            return JSONResponse(upstream.json(), status_code=upstream.status_code)
        return JSONResponse({"detail": "Upstream returned a non-JSON response"}, status_code=502)
    except httpx.HTTPError:
        await log_request(key_digest, model, 502, request.client.host if request.client else None)
        raise HTTPException(status_code=502, detail="Hosted inference provider could not be reached")
