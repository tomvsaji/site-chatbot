from __future__ import annotations

import json
import secrets
import sqlite3
from http.cookies import SimpleCookie
import ipaddress
import mimetypes
import os
import re
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import agent
from rag import load_chunks
from vector_store import (
    EMBEDDING_MODEL,
    QDRANT_COLLECTION,
    HybridIndex,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
PORT = int(os.getenv("PORT", "8080"))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://llm:8080/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "LiquidAI/LFM3-1.7B")
SITE_NAME = os.getenv("SITE_NAME", "Ask My Site")
MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", "800"))
REQUESTS_PER_MINUTE = int(os.getenv("REQUESTS_PER_MINUTE", "3"))
REQUESTS_PER_HOUR = int(os.getenv("REQUESTS_PER_HOUR", "20"))
MAX_MODEL_REQUESTS = int(os.getenv("MAX_MODEL_REQUESTS", "1"))
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "https://chat.srv1619516.hstgr.cloud,https://tomvsaji.com,https://www.tomvsaji.com",
    ).split(",")
    if origin.strip()
}
OUT_OF_SCOPE_ANSWER = "I couldn't find that in Tom's published site data."
INDEX: HybridIndex | None = None
MODEL_CAPACITY = threading.BoundedSemaphore(MAX_MODEL_REQUESTS)


def get_index() -> HybridIndex:
    global INDEX
    if INDEX is None:
        INDEX = HybridIndex(load_chunks(DATA_DIR))
    return INDEX


class SlidingWindowLimiter:
    def __init__(self, per_minute: int, per_hour: int):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.attempts: defaultdict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        with self.lock:
            attempts = self.attempts[client]
            while attempts and attempts[0] <= current - 3600:
                attempts.popleft()
            recent_minute = sum(timestamp > current - 60 for timestamp in attempts)
            if recent_minute >= self.per_minute:
                minute_attempts = [
                    timestamp for timestamp in attempts if timestamp > current - 60
                ]
                retry_after = max(1, int(61 - (current - minute_attempts[0])))
                return False, retry_after
            if len(attempts) >= self.per_hour:
                retry_after = max(1, int(3601 - (current - attempts[0])))
                return False, retry_after
            attempts.append(current)
            if len(self.attempts) > 1024:
                expired = [
                    key
                    for key, values in self.attempts.items()
                    if not values or values[-1] <= current - 3600
                ]
                for key in expired:
                    self.attempts.pop(key, None)
            return True, 0


RATE_LIMITER = SlidingWindowLimiter(REQUESTS_PER_MINUTE, REQUESTS_PER_HOUR)


def allowed_request(client: str) -> bool:
    """Compatibility wrapper used by lightweight callers and tests."""
    return RATE_LIMITER.allow(client)[0]


def client_ip(forwarded_for: str | None, peer: str) -> str:
    candidate = (forwarded_for or "").split(",", 1)[0].strip() or peer
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        try:
            return str(ipaddress.ip_address(peer))
        except ValueError:
            return "unknown"


def answer_question(question: str, session: str) -> dict:
    return agent.run_agent(question, session, get_index())


class Handler(BaseHTTPRequestHandler):
    server_version = "SiteChat/1.0"
    protocol_version = "HTTP/1.1"

    def _security_headers(self) -> None:
        if getattr(self, "new_session_token", None):
            self.send_header("Set-Cookie", f"site_chat_session={self.new_session_token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={agent.MEMORY_TTL_DAYS * 86400}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'",
        )

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(
        self, status: int, body: dict, extra_headers: dict[str, str] | None = None
    ) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self._cors_headers()
        self._security_headers()
        self.end_headers()
        self.wfile.write(encoded)

    def _start_sse(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self._cors_headers()
        self._security_headers()
        self.end_headers()

    def _sse(self, event: str, body: dict) -> bool:
        encoded = (
            f"event: {event}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
        ).encode()
        try:
            self.wfile.write(encoded)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _conversation(self, reset=False):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            token = cookies["site_chat_session"].value if "site_chat_session" in cookies else ""
        except Exception:
            token = ""
        valid = bool(re.fullmatch(r"[0-9a-f]{64}", token))
        if reset and valid:
            agent.store().delete(agent.session_key(token))
        self.new_session_token = None
        if reset or not valid:
            token = secrets.token_hex(32)
            self.new_session_token = token
        self.new_session_token = token
        return agent.session_key(token)

    def _stream_chat(self, question: str, session: str) -> None:
        self._start_sse()
        try:
            def emit(event, body):
                if not self._sse(event, body):
                    raise RuntimeError("Client disconnected.")
            result = agent.run_agent(question, session, get_index(), emit)
            self._sse("final", result)
            self._sse("done", {})
        except RuntimeError as error:
            self._sse("error", {"error": str(error)})
        finally:
            self.close_connection = True

    def do_OPTIONS(self) -> None:
        if urlparse(self.path).path not in {"/api/chat", "/api/chat/stream", "/api/conversation/reset"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        origin = self.headers.get("Origin", "")
        if origin not in ALLOWED_ORIGINS:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed."})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self._security_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            index = get_index()
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "documents": len(index.chunks),
                    "model": LLM_MODEL,
                    "retrieval": "agent-hybrid",
                    "context_tokens": agent.CONTEXT,
                    "max_session_turns": agent.MAX_TURNS,
                    "memory": "private-summary-and-hybrid-archive",
                    "embedding_model": EMBEDDING_MODEL,
                    "vector_collection": QDRANT_COLLECTION,
                },
            )
            return

        relative = "index.html" if path == "/" else path.lstrip("/")
        requested = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in requested.parents and requested != STATIC_DIR.resolve():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if not requested.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return

        content = requested.read_bytes()
        content_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/chat", "/api/chat/stream", "/api/conversation/reset"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 10_000:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or not isinstance(body.get("question", ""), str):
                raise ValueError
            question = body.get("question", "").strip()
        except (ValueError, json.JSONDecodeError, AttributeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request."})
            return

        if path != "/api/conversation/reset" and (not question or len(question) > MAX_QUESTION_CHARS):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Question must be 1–{MAX_QUESTION_CHARS} characters."},
            )
            return

        if not MODEL_CAPACITY.acquire(blocking=False):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "The assistant is busy. Please try again shortly."},
                {"Retry-After": "15"},
            )
            return

        client = client_ip(
            self.headers.get("X-Forwarded-For"), self.client_address[0]
        )
        allowed, retry_after = RATE_LIMITER.allow(client)
        if not allowed:
            MODEL_CAPACITY.release()
            self._json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "Please wait before asking again."},
                {"Retry-After": str(retry_after)},
            )
            return

        if path == "/api/conversation/reset":
            try:
                self._conversation(reset=True)
                self._json(HTTPStatus.OK, {"status": "cleared"})
            except (sqlite3.Error, OSError):
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Conversation storage is temporarily unavailable. Please try again."})
            finally:
                MODEL_CAPACITY.release()
            return
        session = self._conversation()

        if path == "/api/chat/stream":
            try:
                self._stream_chat(question, session)
            finally:
                MODEL_CAPACITY.release()
            return

        try:
            result = answer_question(question, session)
        except RuntimeError as error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
            return
        finally:
            MODEL_CAPACITY.release()
        self._json(HTTPStatus.OK, result)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)


if __name__ == "__main__":
    index = get_index()
    print(
        f"{SITE_NAME} listening on :{PORT}; indexed {len(index.chunks)} "
        f"document chunks in Qdrant collection {QDRANT_COLLECTION}",
        flush=True,
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
