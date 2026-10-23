import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import asyncpg
import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_USERNAME = os.environ.get("GATEWAY_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["GATEWAY_API_KEY"]
PROVIDER_API_KEY = os.environ["PROVIDER_API_KEY"]
PROVIDER_API_BASE = os.environ["PROVIDER_API_BASE"].rstrip("/")
DEFAULT_MODEL = os.environ["DEFAULT_MODEL"]
DAILY_REQUEST_LIMIT = int(os.getenv("DAILY_REQUEST_LIMIT", "100"))
SESSION_COOKIE = "ai_gateway_session"
SESSION_DAYS = 14
password_hasher = PasswordHasher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with app.state.db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_request_usage (
              user_id BIGINT NOT NULL,
              usage_date DATE NOT NULL,
              requests INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (user_id, usage_date)
            );
            CREATE TABLE IF NOT EXISTS user_request_log (
              id BIGSERIAL PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              user_id BIGINT NOT NULL,
              model TEXT,
              status_code INTEGER NOT NULL,
              client_ip TEXT
            );
            CREATE TABLE IF NOT EXISTS app_users (
              id BIGSERIAL PRIMARY KEY,
              username TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              is_admin BOOLEAN NOT NULL DEFAULT FALSE,
              disabled BOOLEAN NOT NULL DEFAULT FALSE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS user_sessions (
              token_hash TEXT PRIMARY KEY,
              user_id BIGINT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
              expires_at TIMESTAMPTZ NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        await conn.execute("""
            INSERT INTO app_users (username, password_hash, is_admin)
            VALUES ($1, $2, TRUE)
            ON CONFLICT (username) DO NOTHING
        """, ADMIN_USERNAME, password_hasher.hash(ADMIN_PASSWORD))
    yield
    await app.state.db.close()


app = FastAPI(title="Private AI Gateway", lifespan=lifespan)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Please log in")
    async with app.state.db.acquire() as conn:
        user = await conn.fetchrow("""
          SELECT u.id, u.username, u.is_admin
          FROM user_sessions s JOIN app_users u ON u.id=s.user_id
          WHERE s.token_hash=$1 AND s.expires_at > now() AND u.disabled=FALSE
        """, token_hash(token))
    if not user:
        raise HTTPException(status_code=401, detail="Session expired; please log in again")
    return user


async def charge(user_id: int):
    async with app.state.db.acquire() as conn:
        used = await conn.fetchval("""
          INSERT INTO user_request_usage (user_id, usage_date, requests)
          VALUES ($1, $2, 1)
          ON CONFLICT (user_id, usage_date) DO UPDATE
            SET requests = user_request_usage.requests + 1
            WHERE user_request_usage.requests < $3
          RETURNING requests
        """, user_id, date.today(), DAILY_REQUEST_LIMIT)
    if used is None:
        raise HTTPException(status_code=429, detail="Daily request quota exceeded")
    return user_id


async def log_request(user_id: int, model: str | None, status: int, client_ip: str | None):
    async with app.state.db.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_request_log (user_id, model, status_code, client_ip) VALUES ($1, $2, $3, $4)",
            user_id, model, status, client_ip,
        )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/auth/login")
async def login(request: Request, response: Response):
    data = await request.json()
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))
    async with app.state.db.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM app_users WHERE username=$1 AND disabled=FALSE", username)
    try:
        if not user:
            raise VerifyMismatchError
        password_hasher.verify(user["password_hash"], password)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = secrets.token_urlsafe(32)
    async with app.state.db.acquire() as conn:
        await conn.execute("INSERT INTO user_sessions (token_hash,user_id,expires_at) VALUES ($1,$2,$3)", token_hash(token), user["id"], datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=False, max_age=SESSION_DAYS*86400, path="/")
    return {"username": user["username"], "is_admin": user["is_admin"]}


@app.post("/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with app.state.db.acquire() as conn:
            await conn.execute("DELETE FROM user_sessions WHERE token_hash=$1", token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.get("/auth/me")
async def whoami(request: Request):
    user = await current_user(request)
    return {"username": user["username"], "is_admin": user["is_admin"], "default_model": DEFAULT_MODEL}


@app.post("/auth/users", status_code=201)
async def create_user(request: Request):
    actor = await current_user(request)
    if not actor["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required")
    data = await request.json()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not username or len(username) > 80 or len(password) < 12:
        raise HTTPException(status_code=400, detail="Enter a username and a password of at least 12 characters")
    try:
        async with app.state.db.acquire() as conn:
            await conn.execute("INSERT INTO app_users (username,password_hash) VALUES ($1,$2)", username, password_hasher.hash(password))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="That username already exists")
    return {"username": username}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    user = await current_user(request)
    user_id = await charge(user["id"])
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Request body must be JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    if payload.get("stream"):
        raise HTTPException(status_code=400, detail="Streaming is not enabled in this starter stack")

    payload.setdefault("model", DEFAULT_MODEL)
    model = payload.get("model")
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            upstream = await client.post(
                f"{PROVIDER_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {PROVIDER_API_KEY}"},
                json=payload,
            )
        content_type = upstream.headers.get("content-type", "application/json")
        await log_request(user_id, model, upstream.status_code, request.client.host if request.client else None)
        if "application/json" in content_type:
            return JSONResponse(upstream.json(), status_code=upstream.status_code)
        return JSONResponse({"detail": "Upstream returned a non-JSON response"}, status_code=502)
    except httpx.HTTPError:
        await log_request(user_id, model, 502, request.client.host if request.client else None)
        raise HTTPException(status_code=502, detail="Hosted inference provider could not be reached")
