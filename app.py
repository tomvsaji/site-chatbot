from __future__ import annotations

import json
import secrets
import sqlite3
from http.cookies import SimpleCookie
import http.client
import ipaddress
import mimetypes
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
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
# Keep the existing single-inference default; no inference behavior changes.
MAX_MODEL_REQUESTS = int(os.getenv("MAX_MODEL_REQUESTS", "1"))
TRUSTED_PROXY_CIDRS = tuple(
    ipaddress.ip_network(value.strip())
    for value in os.getenv("TRUSTED_PROXY_CIDRS", "").split(",") if value.strip()
)
MAX_RATE_LIMIT_CLIENTS = int(os.getenv("MAX_RATE_LIMIT_CLIENTS", "4096"))
GLOBAL_REQUESTS_PER_MINUTE = int(os.getenv("GLOBAL_REQUESTS_PER_MINUTE", "10"))
GLOBAL_REQUESTS_PER_HOUR = int(os.getenv("GLOBAL_REQUESTS_PER_HOUR", "100"))
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "https://chat.srv1619516.hstgr.cloud,https://tomvsaji.com,https://www.tomvsaji.com",
    ).split(",")
    if origin.strip()
}
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "10000"))
MAX_HTTP_CONNECTIONS = int(os.getenv("MAX_HTTP_CONNECTIONS", "32"))
REQUEST_READ_TIMEOUT = float(os.getenv("REQUEST_READ_TIMEOUT", "15"))
RESPONSE_WRITE_TIMEOUT = float(os.getenv("RESPONSE_WRITE_TIMEOUT", "15"))
TURNSTILE_REQUIRED = os.getenv("TURNSTILE_REQUIRED", "false").lower() in {"true", "1", "yes"}
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")
TURNSTILE_EXPECTED_HOSTNAMES = {
    host.strip() for host in os.getenv(
        "TURNSTILE_EXPECTED_HOSTNAMES", "tomvsaji.com,www.tomvsaji.com"
    ).split(",") if host.strip()
}
TURNSTILE_EXPECTED_ACTION = os.getenv("TURNSTILE_EXPECTED_ACTION", "site_chat")
TURNSTILE_TIMEOUT = float(os.getenv("TURNSTILE_TIMEOUT", "5"))
PUBLIC_SERVICE_ERROR = "The assistant is temporarily unavailable. Please try again shortly."
if min(MAX_BODY_BYTES, MAX_HTTP_CONNECTIONS, REQUEST_READ_TIMEOUT, RESPONSE_WRITE_TIMEOUT, MAX_MODEL_REQUESTS, TURNSTILE_TIMEOUT) <= 0:
    raise ValueError("Resource limits must be positive")
OUT_OF_SCOPE_ANSWER = "I couldn't find that in Tom's published site data."
INDEX: HybridIndex | None = None
MODEL_CAPACITY = threading.BoundedSemaphore(MAX_MODEL_REQUESTS)


def get_index() -> HybridIndex:
    global INDEX
    if INDEX is None:
        INDEX = HybridIndex(load_chunks(DATA_DIR))
    return INDEX


class SlidingWindowLimiter:
    def __init__(self, per_minute: int, per_hour: int, max_clients: int = MAX_RATE_LIMIT_CLIENTS):
        self.per_minute = per_minute
        self.per_hour = per_hour
        if min(per_minute, per_hour, max_clients) < 1:
            raise ValueError("Quota settings must be positive")
        self.max_clients = max_clients
        self.attempts: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        with self.lock:
            # Never evict active clients: churn must not reset their quotas.
            expired = [key for key, values in self.attempts.items()
                       if not values or values[-1] <= current - 3600]
            for key in expired:
                del self.attempts[key]
            if client not in self.attempts:
                if len(self.attempts) >= self.max_clients:
                    return False, max(1, int(3601 - (current - min(
                        values[-1] for values in self.attempts.values()))))
                self.attempts[client] = deque()
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
            return True, 0


RATE_LIMITER = SlidingWindowLimiter(REQUESTS_PER_MINUTE, REQUESTS_PER_HOUR)
GLOBAL_LIMITER = SlidingWindowLimiter(
    GLOBAL_REQUESTS_PER_MINUTE, GLOBAL_REQUESTS_PER_HOUR, max_clients=1
)


def client_key(address: str) -> str:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def admit_inference(client: str) -> tuple[bool, int]:
    allowed, retry = RATE_LIMITER.allow(client_key(client))
    if not allowed:
        return allowed, retry
    return GLOBAL_LIMITER.allow("global")


def allowed_request(client: str) -> bool:
    """Compatibility wrapper used by lightweight callers and tests."""
    return RATE_LIMITER.allow(client)[0]


def client_ip(forwarded_for: str | None, peer: str) -> str:
    def parse(value: str):
        if "%" in value:
            raise ValueError("Scoped addresses are not client identities")
        address = ipaddress.ip_address(value)
        return address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address

    try:
        address = parse(peer)
    except ValueError:
        return "0.0.0.0"  # Shared fail-safe identity; socket peers are normally valid.
    fallback = str(address)
    if not forwarded_for or not any(address in net for net in TRUSTED_PROXY_CIDRS):
        return fallback
    try:
        values = forwarded_for.split(",")
        if len(values) > 32:
            return fallback
        chain = [parse(value.strip()) for value in values]
    except ValueError:
        return fallback
    for hop in reversed(chain):
        if not any(address in net for net in TRUSTED_PROXY_CIDRS):
            break
        address = hop
    return str(address)


def answer_question(question: str, session: str) -> dict:
    return agent.run_agent(question, session, get_index())


def verify_turnstile(token) -> int:
    """Return 0, 403 (challenge rejected), or 503 (verification unavailable).

    Siteverify enforces single use. Never retry a used token or expose upstream
    error messages. Optional mode bypasses only requests with no token field.
    """
    if not TURNSTILE_REQUIRED and token is None:
        return 0
    if not TURNSTILE_SECRET_KEY or not TURNSTILE_EXPECTED_HOSTNAMES or not TURNSTILE_EXPECTED_ACTION:
        return HTTPStatus.SERVICE_UNAVAILABLE
    if not isinstance(token, str) or not token.strip() or len(token) > 2048:
        return HTTPStatus.FORBIDDEN
    request = urllib.request.Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=json.dumps({"secret": TURNSTILE_SECRET_KEY, "response": token}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TURNSTILE_TIMEOUT) as response:
            raw = response.read(16385)
        if len(raw) > 16384:
            return HTTPStatus.SERVICE_UNAVAILABLE
        result = json.loads(raw)
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        return HTTPStatus.SERVICE_UNAVAILABLE
    if (not isinstance(result, dict) or result.get("success") is not True
            or not isinstance(result.get("hostname"), str)
            or result["hostname"] not in TURNSTILE_EXPECTED_HOSTNAMES
            or result.get("action") != TURNSTILE_EXPECTED_ACTION
            or result.get("error-codes")):
        return HTTPStatus.FORBIDDEN
    return 0


class HeaderBudgetReader:
    """Bound the aggregate header block while Python parses individual lines."""
    def __init__(self, reader, remaining):
        self.reader = reader
        self.remaining = max(0, remaining)

    def readline(self, limit=-1):
        size = self.remaining + 1 if limit < 0 else min(limit, self.remaining + 1)
        line = self.reader.readline(size)
        self.remaining -= len(line)
        if self.remaining < 0:
            raise http.client.LineTooLong("header block")
        return line


class BoundedHTTPServer(ThreadingHTTPServer):
    """Reject overload before creating a worker; no unbounded executor queue."""
    request_queue_size = 32

    def __init__(self, *args, **kwargs):
        self.connection_slots = threading.BoundedSemaphore(MAX_HTTP_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.connection_slots.acquire(blocking=False):
            try:
                request.settimeout(0.2)
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Connection: close\r\nRetry-After: 15\r\n"
                    b"Content-Type: application/json\r\nContent-Length: 27\r\n"
                    b"X-Content-Type-Options: nosniff\r\n"
                    b"X-Frame-Options: DENY\r\nReferrer-Policy: no-referrer\r\n"
                    b"Content-Security-Policy: default-src 'self'; style-src 'self'; "
                    b"script-src 'self'; connect-src 'self'\r\n\r\n"
                    b'{"error":"Server is busy."}'
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connection_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()

    def handle_error(self, request, client_address):
        # No request data, tokens, exception details, or upstream responses.
        print("HTTP worker failed", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "SiteChat/1.0"
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_READ_TIMEOUT)
        self._read_expired = False
        # A socket inactivity timeout alone permits indefinitely trickled input.
        self._read_timer = threading.Timer(REQUEST_READ_TIMEOUT, self._expire_read)
        self._read_timer.daemon = True
        self._read_timer.start()

    def _expire_read(self):
        self._read_expired = True
        try:
            self.connection.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    def _input_complete(self):
        self._read_timer.cancel()
        self.connection.settimeout(RESPONSE_WRITE_TIMEOUT)

    def finish(self):
        self._read_timer.cancel()
        super().finish()

    def handle(self):
        try:
            super().handle()
        except (OSError, TimeoutError):
            self.close_connection = True

    def handle_expect_100(self):
        self.close_connection = True
        self._json(HTTPStatus.EXPECTATION_FAILED, {"error": "Expect is not supported."})
        return False

    def parse_request(self):
        reader = self.rfile
        self.rfile = HeaderBudgetReader(reader, 16384 - len(self.raw_requestline))
        try:
            parsed = super().parse_request()
        finally:
            self.rfile = reader
        if not parsed:
            return False
        # One request per connection also prevents unread-body desynchronization
        # on all rejection paths and bounds idle keep-alive resource use.
        self.close_connection = True
        if self.request_version == "HTTP/0.9":
            # Python accepts headerless GET requests with dict headers. Reject
            # before Message-only methods and send a framed error response.
            self.request_version = "HTTP/1.0"
            self.send_error(HTTPStatus.HTTP_VERSION_NOT_SUPPORTED)
            return False
        if self._read_expired or len(self.headers.get_all("Origin", [])) > 1:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request."})
            return False
        lengths = self.headers.get_all("Content-Length", [])
        if (self.headers.get_all("Transfer-Encoding") or len(lengths) > 1
                or (lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0]))):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request framing."})
            return False
        self.body_length = int(lengths[0]) if lengths else 0
        if self.body_length > MAX_BODY_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request is too large."})
            return False
        return True

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self._json(code, {"error": "Invalid HTTP request."})

    def _security_headers(self) -> None:
        if getattr(self, "new_session_token", None):
            self.send_header("Set-Cookie", f"site_chat_session={self.new_session_token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={agent.MEMORY_TTL_DAYS * 86400}")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'",
        )

    def _cors_headers(self) -> None:
        origin = getattr(self, "headers", {}).get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Expose-Headers", "Retry-After")
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
        except OSError:
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
        if not self._sse("status", {"message": "Preparing your conversation…"}):
            return
        try:
            def emit(event, body):
                if not self._sse(event, body):
                    raise RuntimeError("Client disconnected.")
            result = agent.run_agent(question, session, get_index(), emit)
            self._sse("final", result)
            self._sse("done", {})
        except agent.AgentError as error:
            self._sse("error", {"error": str(error)})
        except Exception:
            self._sse("error", {"error": PUBLIC_SERVICE_ERROR})
        finally:
            self.close_connection = True

    def do_OPTIONS(self) -> None:
        self._input_complete()
        if urlparse(self.path).path not in {"/api/chat", "/api/chat/stream", "/api/conversation/reset"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        origin = self.headers.get("Origin", "")
        if origin not in ALLOWED_ORIGINS:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed."})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self._security_headers()
        self.end_headers()

    def do_GET(self) -> None:
        self._input_complete()
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

        if (len(self.headers.get_all("Content-Type", [])) != 1
                or self.headers.get_content_type() != "application/json"
                or self.headers.get_content_charset("utf-8").lower() != "utf-8"):
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Use application/json with UTF-8."})
            return
        try:
            length = self.body_length
            if length <= 0:
                raise ValueError
            raw = self.rfile.read(length)
            self._input_complete()
            if len(raw) != length or self._read_expired:
                raise ValueError
            def reject_constant(value):
                raise ValueError("Invalid JSON constant")
            body = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
            if not isinstance(body, dict) or not isinstance(body.get("question", "" if path == "/api/conversation/reset" else None), str):
                raise ValueError
            question = body.get("question", "").strip()
            history = body.get("history", [])
            if not isinstance(history, list):
                raise ValueError
            if any(
                not isinstance(m, dict)
                or m.get("role") not in ("user", "assistant")
                or not isinstance(m.get("content"), str)
                for m in history
            ):
                raise ValueError
            # Legacy history is validated for compatibility but is never used
            # as private memory. The server session cookie determines history.
        except (ValueError, UnicodeError, RecursionError, OSError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request."})
            return

        if path != "/api/conversation/reset" and (not question or len(question) > MAX_QUESTION_CHARS):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Question must be 1–{MAX_QUESTION_CHARS} characters."},
            )
            return

        client = client_ip(
            ",".join(self.headers.get_all("X-Forwarded-For", [])), self.client_address[0]
        )
        # Reserve quotas before external verification, including failed attempts.
        # Origin and forwarding-header variations cannot create new quota keys.
        allowed, retry_after = admit_inference(client)
        if not allowed:
            self._json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "Please wait before asking again."},
                {"Retry-After": str(retry_after)},
            )
            return
        challenge_status = verify_turnstile(body.get("turnstile_token"))
        if challenge_status:
            self._json(
                challenge_status, {"error": "Challenge verification failed. Please try again."},
                {"Retry-After": "15"} if challenge_status == 503 else None,
            )
            return
        if not MODEL_CAPACITY.acquire(blocking=False):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "The assistant is busy. Please try again shortly."},
                {"Retry-After": "15"},
            )
            return

        if path == "/api/conversation/reset":
            try:
                self._conversation(reset=True)
                self._json(HTTPStatus.OK, {"status": "cleared"})
            except Exception:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": PUBLIC_SERVICE_ERROR}, {"Retry-After": "15"})
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
        except agent.AgentError as error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)}, {"Retry-After": "15"})
            return
        except Exception:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": PUBLIC_SERVICE_ERROR}, {"Retry-After": "15"})
            return
        finally:
            MODEL_CAPACITY.release()
        self._json(HTTPStatus.OK, result)

    def log_message(self, format: str, *args: object) -> None:
        # Base logging includes raw request paths; omit potential tokens/query data.
        pass


if __name__ == "__main__":
    index = get_index()
    print(
        f"{SITE_NAME} listening on :{PORT}; indexed {len(index.chunks)} "
        f"document chunks in Qdrant collection {QDRANT_COLLECTION}",
        flush=True,
    )
    BoundedHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
