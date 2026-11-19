import contextlib
import hmac
import logging
import urllib.parse
from pathlib import Path

import aiohttp
import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from yarl import URL

from . import scheduler
from .beeper import describe
from .db import rows
from .tools import make_mcp

log = logging.getLogger(__name__)
# Stateless MCP logs "Terminating session" after every request.
logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)

# The browser reaches Beeper only through these, never e.g. /v1/app (login) or /v1/bridges.
PROXIED = ("/v1/accounts", "/v1/chats", "/v1/messages", "/v1/assets/serve", "/v1/app/setup", "/v1/bridges")
# Connecting a network goes through login sessions. Logins may be listed (to add one to this server),
# but nothing else: .../logins/{id}/remove takes a network off your account.
LOGINS = "/logins"
# Replaces the account's recovery key, so only allowed while the account has none yet.
RESET = "/v1/app/setup/verification/recovery-key/reset"
MCP_PATHS = ("/mcp", "/mcp/")
PASS_REQ = ("Content-Type", "Range", "Accept-Encoding", "If-None-Match", "If-Modified-Since")
PASS_RESP = ("Content-Type", "Content-Length", "Content-Encoding", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified", "Cache-Control")


def make_app(beeper, db, transcriber, static_dir, save_token, hosts=("localhost", "127.0.0.1", "::1"), users=None, mcp=False, mcp_token=None):
    async def body(request, *required):
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict) or any(k not in data for k in required):
            raise HTTPException(400, f"expected fields: {', '.join(required)}")
        return data

    def field(data, key, kind):
        v = data[key]
        # bool is an int subclass, so reject it explicitly.
        if not isinstance(v, kind) or isinstance(v, bool) or (isinstance(v, str) and not v.strip()):
            raise HTTPException(400, f"invalid {key}")
        return v

    async def sign_in(request):
        data = await body(request)
        step = request.url.path.rsplit("/", 1)[1]
        async with beeper.http.post(f"{beeper.url}/v1/app/setup/{step}", json=data) as r:
            if r.status >= 400 or r.content_type != "application/json":
                return Response(await r.read(), r.status, media_type=r.content_type)
            result = await r.json()
        # Beeper returns the account token once; keep it server-side, the browser never needs it.
        token = (result.get("matrix") or {}).pop("accessToken", None)
        if token:
            save_token(token)
        return JSONResponse(result)

    async def proxy(request):
        # Forward the path exactly as sent (still percent-encoded), with its query.
        path = request.scope["raw_path"].decode()[len("/api/beeper"):]
        if request.scope["query_string"]:
            path += "?" + request.scope["query_string"].decode()
        plain = urllib.parse.unquote(path).lower()
        if not path.startswith(PROXIED) or ".." in plain or (plain.startswith("/v1/bridges") and LOGINS in plain.split("?")[0] and request.method != "GET"):
            raise HTTPException(403)
        # Beeper may match routes case-insensitively or decoded, so compare the same way.
        if RESET in plain and (await beeper.call("GET", "/v1/app/setup"))["state"] != "needs-cross-signing-setup":
            raise HTTPException(403, "the recovery key can only be created while setting up encryption")
        headers = {k: request.headers[k] for k in PASS_REQ if k in request.headers}
        r = await beeper.raw.request(request.method, URL(beeper.url + path, encoded=True), headers=headers, data=await request.body() or None)
        headers = {k: r.headers[k] for k in PASS_RESP if k in r.headers}
        # Attachments are served from this origin: an .html or .svg someone sends you must not run as the app.
        headers |= {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}

        async def stream():
            try:
                async for chunk in r.content.iter_chunked(1 << 16):
                    yield chunk
            finally:
                r.release()

        return StreamingResponse(stream(), r.status, headers)

    async def config(request):
        return JSONResponse({"transcription": transcriber is not None, "token": "Authorization" in beeper.http.headers})

    # --- send later ---

    async def list_scheduled(request):
        return JSONResponse(rows(db, "select * from scheduled order by send_at"))

    async def schedule(request):
        data = await body(request, "chatID", "text", "sendAt")
        return JSONResponse(await scheduler.add(beeper, db, field(data, "chatID", str), field(data, "text", str), field(data, "sendAt", int)))

    async def edit_scheduled(request):
        data = await body(request)
        text = field(data, "text", str) if "text" in data else None
        send_at = field(data, "sendAt", int) if "sendAt" in data else None
        row = scheduler.edit(db, request.path_params["id"], text, send_at)
        if row is None:
            raise HTTPException(404)
        return JSONResponse(row)

    async def unschedule(request):
        db.execute("delete from scheduled where id = ?", (request.path_params["id"],))
        return JSONResponse({"ok": True})

    # --- transcriptions ---

    async def list_transcriptions(request):
        chat_id = request.query_params.get("chatID")
        if chat_id:
            return JSONResponse(rows(db, "select * from transcriptions where chat_id = ? order by sent_at", chat_id))
        limit = min(int(request.query_params.get("limit", "50")), 500)
        return JSONResponse(rows(db, "select * from transcriptions order by created_at desc limit ?", limit))

    async def transcribe(request):
        if transcriber is None:
            raise HTTPException(404, "transcription is not configured")
        data = await body(request, "chatID", "messageID")
        chat_id, message_id = field(data, "chatID", str), field(data, "messageID", str)
        msg = await beeper.call("GET", f"/v1/chats/{chat_id}/messages/{message_id}")
        try:
            return JSONResponse(await transcriber.transcribe(msg))
        except ValueError as e:
            raise HTTPException(400, str(e))

    # --- web app ---

    index = Path(static_dir) / "index.html"

    async def home(request):
        # Nix store files all date from 1970, so a browser revalidating would be told its old copy is current.
        return Response(index.read_bytes(), media_type="text/html", headers={"Cache-Control": "no-store"})

    routes = [
        Route("/api/beeper/v1/app/setup/response", sign_in, methods=["POST"]),
        Route("/api/beeper/v1/app/setup/register", sign_in, methods=["POST"]),
        Route("/api/beeper/{tail:path}", proxy, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]),
        Route("/api/config", config),
        Route("/api/scheduled", list_scheduled),
        Route("/api/scheduled", schedule, methods=["POST"]),
        Route("/api/scheduled/{id:int}", edit_scheduled, methods=["PATCH"]),
        Route("/api/scheduled/{id:int}", unschedule, methods=["DELETE"]),
        Route("/api/transcriptions", list_transcriptions),
        Route("/api/transcriptions", transcribe, methods=["POST"]),
        Route("/", home),
    ]
    lifespan = None
    if mcp:
        mcp_server = make_mcp(beeper, db, transcriber)
        # Host, user and origin checks are done by guard() below, for the whole app.
        mcp_app = mcp_server.streamable_http_app(stateless_http=True, transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        # Some clients add a trailing slash; without this route it would reach the static files (405).
        routes += [Route(path, mcp_app.routes[0].endpoint) for path in MCP_PATHS]

        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with mcp_server.session_manager.run():
                yield
    else:
        async def mcp_off(request):
            raise HTTPException(404, "MCP is off: set web.mcp = true")
        routes += [Route(path, mcp_off, methods=["GET", "POST", "DELETE"]) for path in MCP_PATHS]

    routes.append(Mount("/", StaticFiles(directory=static_dir)))
    # Failures talking to Beeper or the transcription engine go to the web app, instead of a bare 500.
    failures = dict.fromkeys((aiohttp.ClientError, RuntimeError, OSError), bad_gateway)
    app = Starlette(routes=routes, exception_handlers=failures, lifespan=lifespan)
    return guard(app, hosts, users, mcp_token)


def refused(request, hosts, users, mcp_token=None):
    # Blocks DNS rebinding: another website pointing its own name at this server's address.
    if request.url.hostname not in hosts:
        return f"unknown host {request.headers.get('host')}: add it to web.allowed_hosts"
    # tailscale serve sets this header for tailnet users and strips any value a client sends.
    login = request.headers.get("Tailscale-User-Login", "").lower()
    if users and login not in users:
        return f"{login or 'this request'} is not in web.allowed_users (open it through tailscale serve)"
    if mcp_token and request.url.path in MCP_PATHS and not hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {mcp_token}".encode()):
        return "wrong or missing MCP token (Authorization: Bearer ..., see web.mcp_token_file)"
    # Any website you visit can make your browser send it requests; only the app's own page may change things.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site, origin = request.headers.get("Sec-Fetch-Site"), request.headers.get("Origin")
        if site not in (None, "same-origin", "none") or (origin and urllib.parse.urlsplit(origin).netloc != request.headers.get("host")):
            return "cross-site request refused"


def guard(app, hosts, users, mcp_token=None):
    async def asgi(scope, receive, send):
        if scope["type"] == "http" and (reason := refused(Request(scope), hosts, users, mcp_token)):
            return await PlainTextResponse(reason, 403)(scope, receive, send)
        await app(scope, receive, send)
    return asgi


async def bad_gateway(request, e):
    log.warning("%s %s: %s", request.method, request.url.path, describe(e))
    status = getattr(e, "status", 502)
    return PlainTextResponse(f"{status}: {getattr(e, 'message', None) or describe(e)}", 502)


def server(app, host, port):
    # log_config=None keeps our logging setup.
    return uvicorn.Server(uvicorn.Config(app, host, port, log_config=None, access_log=False))
