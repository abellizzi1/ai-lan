import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg
import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from markdown_it import MarkdownIt
import nh3

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
markdown = MarkdownIt("commonmark", {"html": False}).enable("table")


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
            CREATE TABLE IF NOT EXISTS conversations (
              id UUID PRIMARY KEY,
              user_id BIGINT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              source_id TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (user_id, source_id)
            );
            CREATE TABLE IF NOT EXISTS conversation_messages (
              id BIGSERIAL PRIMARY KEY,
              conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
              role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
              content TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS conversation_messages_chat_id_idx
              ON conversation_messages (conversation_id, id);
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


def parse_chat_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")


def present_message(row) -> dict:
    message = {"role": row["role"], "content": row["content"]}
    if row["role"] == "assistant":
        message["html"] = nh3.clean(markdown.render(row["content"]))
    return message


@app.get("/v1/chats")
async def list_chats(request: Request):
    user = await current_user(request)
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, title, updated_at FROM conversations
            WHERE user_id=$1 ORDER BY updated_at DESC
        """, user["id"])
    return [{"id": str(row["id"]), "title": row["title"], "updated_at": row["updated_at"].isoformat()} for row in rows]


@app.post("/v1/chats", status_code=201)
async def create_chat(request: Request):
    user = await current_user(request)
    chat_id = uuid4()
    async with app.state.db.acquire() as conn:
        await conn.execute("INSERT INTO conversations (id, user_id, title) VALUES ($1, $2, 'New chat')", chat_id, user["id"])
    return {"id": str(chat_id), "title": "New chat", "messages": []}


@app.post("/v1/chats/import", status_code=201)
async def import_chat(request: Request):
    user = await current_user(request)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid conversation")
    source_id = data.get("source_id")
    messages = data.get("messages")
    title = data.get("title")
    if (not isinstance(source_id, str) or not source_id or len(source_id) > 120
            or not isinstance(title, str) or not isinstance(messages, list) or len(messages) > 500
            or any(not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                   or not isinstance(m.get("content"), str) or len(m["content"]) > 20000 for m in messages)):
        raise HTTPException(status_code=400, detail="Invalid conversation")
    chat_id = uuid4()
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            saved_id = await conn.fetchval("""
                INSERT INTO conversations (id, user_id, title, source_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, source_id) DO NOTHING RETURNING id
            """, chat_id, user["id"], title[:120] or "New chat", source_id)
            if saved_id:
                await conn.executemany("""
                    INSERT INTO conversation_messages (conversation_id, role, content)
                    VALUES ($1, $2, $3)
                """, [(chat_id, m["role"], m["content"]) for m in messages])
            else:
                saved_id = await conn.fetchval(
                    "SELECT id FROM conversations WHERE user_id=$1 AND source_id=$2", user["id"], source_id
                )
    return {"id": str(saved_id)}


@app.get("/v1/chats/{chat_id}")
async def get_chat(chat_id: str, request: Request):
    user = await current_user(request)
    parsed_id = parse_chat_id(chat_id)
    async with app.state.db.acquire() as conn:
        chat = await conn.fetchrow(
            "SELECT id, title FROM conversations WHERE id=$1 AND user_id=$2", parsed_id, user["id"]
        )
        if not chat:
            raise HTTPException(status_code=404, detail="Conversation not found")
        rows = await conn.fetch("""
            SELECT role, content FROM conversation_messages
            WHERE conversation_id=$1 ORDER BY id
        """, parsed_id)
    return {"id": str(chat["id"]), "title": chat["title"], "messages": [present_message(row) for row in rows]}


@app.delete("/v1/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, request: Request):
    user = await current_user(request)
    async with app.state.db.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM conversations WHERE id=$1 AND user_id=$2", parse_chat_id(chat_id), user["id"]
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@app.post("/v1/chats/{chat_id}/messages")
async def send_chat_message(chat_id: str, request: Request):
    user = await current_user(request)
    parsed_id = parse_chat_id(chat_id)
    data = await request.json()
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str) or not content.strip() or len(content) > 20000:
        raise HTTPException(status_code=400, detail="Enter a message of at most 20,000 characters")
    content = content.strip()
    async with app.state.db.acquire() as conn:
        chat = await conn.fetchrow(
            "SELECT id, title FROM conversations WHERE id=$1 AND user_id=$2", parsed_id, user["id"]
        )
        if not chat:
            raise HTTPException(status_code=404, detail="Conversation not found")
        previous = await conn.fetch("""
            SELECT role, content FROM conversation_messages
            WHERE conversation_id=$1 ORDER BY id
        """, parsed_id)
    await charge(user["id"])
    payload = {"model": DEFAULT_MODEL, "messages": [
        {"role": row["role"], "content": row["content"]} for row in previous
    ] + [{"role": "user", "content": content}]}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            upstream = await client.post(
                f"{PROVIDER_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {PROVIDER_API_KEY}"},
                json=payload,
            )
    except httpx.HTTPError:
        await log_request(user["id"], DEFAULT_MODEL, 502, request.client.host if request.client else None)
        raise HTTPException(status_code=502, detail="Inference provider could not be reached")
    await log_request(user["id"], DEFAULT_MODEL, upstream.status_code, request.client.host if request.client else None)
    try:
        result = upstream.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Provider returned a non-JSON response")
    if upstream.status_code >= 400:
        upstream_error = result.get("error") if isinstance(result, dict) else None
        detail = upstream_error.get("message") if isinstance(upstream_error, dict) else None
        raise HTTPException(status_code=upstream.status_code, detail=detail or "Provider rejected the request")
    choices = result.get("choices") if isinstance(result, dict) else None
    first_choice = choices[0] if isinstance(choices, list) and choices else None
    message = first_choice.get("message") if isinstance(first_choice, dict) else None
    reply = message.get("content") if isinstance(message, dict) else None
    if not isinstance(reply, str):
        raise HTTPException(status_code=502, detail="Provider returned an unexpected response")
    title = content[:60] if not previous else chat["title"]
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""
                INSERT INTO conversation_messages (conversation_id, role, content)
                VALUES ($1, $2, $3)
            """, [(parsed_id, "user", content), (parsed_id, "assistant", reply)])
            await conn.execute(
                "UPDATE conversations SET title=$2, updated_at=now() WHERE id=$1", parsed_id, title
            )
    return {"title": title, "messages": [
        {"role": "user", "content": content},
        present_message({"role": "assistant", "content": reply}),
    ]}


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
