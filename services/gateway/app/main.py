import asyncio
import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from functools import wraps
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
request_locks: dict[int, asyncio.Lock] = {}


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
              client_ip TEXT,
              prompt_tokens BIGINT,
              completion_tokens BIGINT,
              total_tokens BIGINT
            );
            ALTER TABLE user_request_log
              ADD COLUMN IF NOT EXISTS prompt_tokens BIGINT,
              ADD COLUMN IF NOT EXISTS completion_tokens BIGINT,
              ADD COLUMN IF NOT EXISTS total_tokens BIGINT;
            CREATE INDEX IF NOT EXISTS user_request_log_user_id_id_idx
              ON user_request_log (user_id, id DESC);
            CREATE INDEX IF NOT EXISTS user_request_log_user_recent_tokens_idx
              ON user_request_log (user_id, created_at)
              WHERE total_tokens IS NOT NULL;
            CREATE TABLE IF NOT EXISTS app_users (
              id BIGSERIAL PRIMARY KEY,
              username TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              is_admin BOOLEAN NOT NULL DEFAULT FALSE,
              disabled BOOLEAN NOT NULL DEFAULT FALSE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            ALTER TABLE app_users ADD COLUMN IF NOT EXISTS token_limit BIGINT
              CHECK (token_limit > 0);
            ALTER TABLE app_users ADD COLUMN IF NOT EXISTS requests_blocked BOOLEAN
              NOT NULL DEFAULT FALSE;
            CREATE TABLE IF NOT EXISTS gateway_settings (
              id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
              token_limit BIGINT CHECK (token_limit > 0),
              requests_blocked BOOLEAN NOT NULL DEFAULT FALSE
            );
            ALTER TABLE gateway_settings ADD COLUMN IF NOT EXISTS requests_blocked BOOLEAN
              NOT NULL DEFAULT FALSE;
            INSERT INTO gateway_settings (id) VALUES (TRUE) ON CONFLICT DO NOTHING;
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
            CREATE INDEX IF NOT EXISTS conversations_user_id_updated_idx
              ON conversations (user_id, updated_at DESC, id DESC);
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


@app.middleware("http")
async def admin_no_store(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/auth/admin/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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


async def require_admin(request: Request):
    user = await current_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def serialize_user_requests(handler):
    @wraps(handler)
    async def wrapped(*args, **kwargs):
        request = kwargs.get("request")
        if request is None:
            request = next(arg for arg in args if isinstance(arg, Request))
        user = await current_user(request)
        lock = request_locks.setdefault(user["id"], asyncio.Lock())
        async with lock:
            return await handler(*args, **kwargs)
    return wrapped


async def charge(user_id: int):
    async with app.state.db.acquire() as conn:
        limits = await conn.fetchrow("""
          SELECT u.token_limit AS user_limit, s.token_limit AS global_limit,
                 u.requests_blocked AS user_blocked, s.requests_blocked AS global_blocked
          FROM app_users u CROSS JOIN gateway_settings s
          WHERE u.id=$1
        """, user_id)
        if limits and limits["global_blocked"]:
            raise HTTPException(status_code=403, detail="Requests are blocked for all users by an administrator")
        if limits and limits["user_blocked"]:
            raise HTTPException(status_code=403, detail="Your requests are blocked by an administrator")
        if limits and (limits["user_limit"] is not None or limits["global_limit"] is not None):
            used_tokens = await conn.fetchval("""
              SELECT COALESCE(SUM(total_tokens), 0)::bigint
              FROM user_request_log
              WHERE user_id=$1 AND created_at > now() - interval '1 hour'
                AND total_tokens IS NOT NULL
            """, user_id)
            if any(limit is not None and used_tokens >= limit
                   for limit in (limits["user_limit"], limits["global_limit"])):
                raise HTTPException(status_code=429, detail="Hourly token limit reached")
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


def token_counts(result: object) -> tuple[int | None, int | None, int | None]:
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return None, None, None

    def count(key: str) -> int | None:
        value = usage.get(key)
        return value if type(value) is int and 0 <= value <= 9223372036854775807 else None

    prompt = count("prompt_tokens")
    completion = count("completion_tokens")
    total = count("total_tokens")
    if total is None and prompt is not None and completion is not None:
        combined = prompt + completion
        total = combined if combined <= 9223372036854775807 else None
    return prompt, completion, total


async def log_request(user_id: int, model: str | None, status: int,
                      client_ip: str | None, result: object = None):
    prompt_tokens, completion_tokens, total_tokens = token_counts(result)
    async with app.state.db.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_request_log
               (user_id, model, status_code, client_ip, prompt_tokens, completion_tokens, total_tokens)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            user_id, model, status, client_ip, prompt_tokens, completion_tokens, total_tokens,
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
    await require_admin(request)
    data = await request.json()
    username = str(data.get("username", "")).strip()
    password = data.get("password")
    if not username or len(username) > 80 or not isinstance(password, str) or not password:
        raise HTTPException(status_code=400, detail="Enter a username (up to 80 characters) and a password")
    try:
        async with app.state.db.acquire() as conn:
            await conn.execute("INSERT INTO app_users (username,password_hash) VALUES ($1,$2)", username, password_hasher.hash(password))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="That username already exists")
    return {"username": username}


def checked_offset(offset: int) -> int:
    if offset < 0 or offset > 1_000_000:
        raise HTTPException(status_code=400, detail="Invalid page offset")
    return offset


async def require_existing_user(conn, user_id: int):
    if await conn.fetchval("SELECT 1 FROM app_users WHERE id=$1", user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")


def parse_token_limit(data: object) -> int | None:
    if not isinstance(data, dict) or "token_limit" not in data:
        raise HTTPException(status_code=400, detail="Provide token_limit as a positive integer or null")
    limit = data["token_limit"]
    if limit is not None and (type(limit) is not int or not 0 < limit <= 9223372036854775807):
        raise HTTPException(status_code=400, detail="Token limit must be a positive integer or null")
    return limit


async def requested_token_limit(request: Request) -> int | None:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Request body must be JSON")
    return parse_token_limit(data)


async def requested_block_state(request: Request) -> bool:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Request body must be JSON")
    if not isinstance(data, dict) or type(data.get("blocked")) is not bool:
        raise HTTPException(status_code=400, detail="Provide blocked as true or false")
    return data["blocked"]


@app.get("/auth/admin/request-block")
async def admin_request_block(request: Request):
    await require_admin(request)
    async with app.state.db.acquire() as conn:
        blocked = await conn.fetchval("SELECT requests_blocked FROM gateway_settings WHERE id=TRUE")
    return {"blocked": blocked}


@app.put("/auth/admin/request-block")
async def set_admin_request_block(request: Request):
    await require_admin(request)
    blocked = await requested_block_state(request)
    async with app.state.db.acquire() as conn:
        await conn.execute("UPDATE gateway_settings SET requests_blocked=$1 WHERE id=TRUE", blocked)
    return {"blocked": blocked}


@app.put("/auth/admin/users/{user_id}/request-block")
async def set_user_request_block(user_id: int, request: Request):
    await require_admin(request)
    blocked = await requested_block_state(request)
    async with app.state.db.acquire() as conn:
        await require_existing_user(conn, user_id)
        await conn.execute("UPDATE app_users SET requests_blocked=$1 WHERE id=$2", blocked, user_id)
    return {"blocked": blocked}


@app.get("/auth/admin/token-limit")
async def admin_token_limit(request: Request):
    await require_admin(request)
    async with app.state.db.acquire() as conn:
        limit = await conn.fetchval("SELECT token_limit FROM gateway_settings WHERE id=TRUE")
    return {"token_limit": limit}


@app.put("/auth/admin/token-limit")
async def set_admin_token_limit(request: Request):
    await require_admin(request)
    limit = await requested_token_limit(request)
    async with app.state.db.acquire() as conn:
        await conn.execute("UPDATE gateway_settings SET token_limit=$1 WHERE id=TRUE", limit)
    return {"token_limit": limit}


@app.put("/auth/admin/users/{user_id}/token-limit")
async def set_user_token_limit(user_id: int, request: Request):
    await require_admin(request)
    limit = await requested_token_limit(request)
    async with app.state.db.acquire() as conn:
        await require_existing_user(conn, user_id)
        await conn.execute("UPDATE app_users SET token_limit=$1 WHERE id=$2", limit, user_id)
    return {"token_limit": limit}


@app.get("/auth/admin/users")
async def admin_users(request: Request):
    await require_admin(request)
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.id, u.username, u.is_admin, u.disabled, u.created_at,
              u.token_limit, u.requests_blocked,
              COALESCE((SELECT SUM(requests) FROM user_request_usage q
                        WHERE q.user_id=u.id), 0)::bigint AS charged_requests,
              (SELECT COUNT(*) FROM user_request_log l
               WHERE l.user_id=u.id) AS logged_requests,
              (SELECT COUNT(*) FROM user_request_log l
               WHERE l.user_id=u.id AND l.total_tokens IS NOT NULL) AS measured_requests,
              COALESCE((SELECT SUM(prompt_tokens) FROM user_request_log l
                        WHERE l.user_id=u.id), 0)::bigint AS prompt_tokens,
              COALESCE((SELECT SUM(completion_tokens) FROM user_request_log l
                        WHERE l.user_id=u.id), 0)::bigint AS completion_tokens,
              COALESCE((SELECT SUM(total_tokens) FROM user_request_log l
                        WHERE l.user_id=u.id), 0)::bigint AS total_tokens,
              COALESCE((SELECT SUM(total_tokens) FROM user_request_log l
                        WHERE l.user_id=u.id
                          AND l.created_at > now() - interval '1 hour'
                          AND l.total_tokens IS NOT NULL), 0)::bigint
                AS hourly_tokens,
              (SELECT COUNT(*) FROM conversations c
               WHERE c.user_id=u.id) AS conversation_count,
              (SELECT COUNT(*) FROM conversation_messages m
               JOIN conversations c ON c.id=m.conversation_id
               WHERE c.user_id=u.id) AS message_count,
              (SELECT COUNT(*) FROM user_sessions s
               WHERE s.user_id=u.id AND s.expires_at > now()) AS active_sessions
            FROM app_users u ORDER BY lower(u.username), u.id
        """)
    return [dict(row) for row in rows]


@app.get("/auth/admin/users/{user_id}/usage")
async def admin_user_usage(user_id: int, request: Request):
    await require_admin(request)
    async with app.state.db.acquire() as conn:
        await require_existing_user(conn, user_id)
        request_days = await conn.fetch("""
            SELECT usage_date, requests FROM user_request_usage
            WHERE user_id=$1 ORDER BY usage_date DESC
        """, user_id)
        token_days = await conn.fetch("""
            SELECT (created_at AT TIME ZONE 'UTC')::date AS usage_date,
              COUNT(*) AS logged_requests,
              COUNT(*) FILTER (WHERE total_tokens IS NOT NULL) AS measured_requests,
              COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens,
              COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens,
              COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens
            FROM user_request_log WHERE user_id=$1
            GROUP BY 1 ORDER BY 1 DESC
        """, user_id)
        models = await conn.fetch("""
            SELECT model, COUNT(*) AS logged_requests,
              COUNT(*) FILTER (WHERE total_tokens IS NOT NULL) AS measured_requests,
              COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens,
              COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens,
              COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens
            FROM user_request_log WHERE user_id=$1
            GROUP BY model ORDER BY total_tokens DESC, model
        """, user_id)
    return {"request_days": [dict(row) for row in request_days],
            "token_days": [dict(row) for row in token_days],
            "models": [dict(row) for row in models]}


@app.get("/auth/admin/users/{user_id}/requests")
async def admin_user_requests(user_id: int, request: Request, offset: int = 0):
    await require_admin(request)
    offset = checked_offset(offset)
    async with app.state.db.acquire() as conn:
        await require_existing_user(conn, user_id)
        rows = await conn.fetch("""
            SELECT id, created_at, model, status_code, client_ip,
                   prompt_tokens, completion_tokens, total_tokens
            FROM user_request_log WHERE user_id=$1
            ORDER BY id DESC OFFSET $2 LIMIT 51
        """, user_id, offset)
    return {"items": [dict(row) for row in rows[:50]],
            "next_offset": offset + 50 if len(rows) > 50 else None}


@app.get("/auth/admin/users/{user_id}/conversations")
async def admin_user_conversations(user_id: int, request: Request, offset: int = 0):
    await require_admin(request)
    offset = checked_offset(offset)
    async with app.state.db.acquire() as conn:
        await require_existing_user(conn, user_id)
        rows = await conn.fetch("""
            SELECT c.id, c.title, c.source_id, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM conversation_messages m
                    WHERE m.conversation_id=c.id) AS message_count
            FROM conversations c WHERE c.user_id=$1
            ORDER BY c.updated_at DESC, c.id DESC OFFSET $2 LIMIT 51
        """, user_id, offset)
    return {"items": [dict(row) for row in rows[:50]],
            "next_offset": offset + 50 if len(rows) > 50 else None}


@app.get("/auth/admin/conversations/{chat_id}")
async def admin_conversation(chat_id: str, request: Request):
    await require_admin(request)
    parsed_id = parse_chat_id(chat_id)
    async with app.state.db.acquire() as conn:
        chat = await conn.fetchrow("""
            SELECT c.id, c.user_id, u.username, c.title, c.source_id,
                   c.created_at, c.updated_at
            FROM conversations c JOIN app_users u ON u.id=c.user_id
            WHERE c.id=$1
        """, parsed_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = await conn.fetch("""
            SELECT id, role, content, created_at FROM conversation_messages
            WHERE conversation_id=$1 ORDER BY id
        """, parsed_id)
    return {"conversation": dict(chat), "messages": [dict(row) for row in messages]}


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
@serialize_user_requests
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
    try:
        result = upstream.json()
    except ValueError:
        await log_request(user["id"], DEFAULT_MODEL, 502, request.client.host if request.client else None)
        raise HTTPException(status_code=502, detail="Provider returned a non-JSON response")
    await log_request(user["id"], DEFAULT_MODEL, upstream.status_code,
                      request.client.host if request.client else None, result)
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
@serialize_user_requests
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
    except httpx.HTTPError:
        await log_request(user_id, model, 502, request.client.host if request.client else None)
        raise HTTPException(status_code=502, detail="Hosted inference provider could not be reached")
    content_type = upstream.headers.get("content-type", "application/json")
    if "application/json" not in content_type:
        await log_request(user_id, model, 502, request.client.host if request.client else None)
        return JSONResponse({"detail": "Upstream returned a non-JSON response"}, status_code=502)
    try:
        result = upstream.json()
    except ValueError:
        await log_request(user_id, model, 502, request.client.host if request.client else None)
        return JSONResponse({"detail": "Upstream returned invalid JSON"}, status_code=502)
    await log_request(user_id, model, upstream.status_code,
                      request.client.host if request.client else None, result)
    return JSONResponse(result, status_code=upstream.status_code)
