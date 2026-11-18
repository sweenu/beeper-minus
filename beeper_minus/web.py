import logging
import urllib.parse
from pathlib import Path

from aiohttp import web
from yarl import URL

from .beeper import describe
from .db import now_ms, rows

log = logging.getLogger(__name__)

# The browser reaches Beeper only through these, never e.g. /v1/app (login) or /v1/bridges.
PROXIED = ("/v1/accounts", "/v1/chats", "/v1/messages", "/v1/assets/serve", "/v1/app/setup", "/v1/bridges")
# Connecting a network goes through login sessions. Logins may be listed (to add one to this server),
# but nothing else: .../logins/{id}/remove takes a network off your account.
LOGINS = "/logins"
# Replaces the account's recovery key, so only allowed while the account has none yet.
RESET = "/v1/app/setup/verification/recovery-key/reset"
PASS_REQ = ("Content-Type", "Range", "Accept-Encoding", "If-None-Match", "If-Modified-Since")
PASS_RESP = ("Content-Type", "Content-Length", "Content-Encoding", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified", "Cache-Control")


def make_app(beeper, db, transcriber, static_dir, save_token, hosts=("localhost", "127.0.0.1", "::1"), users=None):
    routes = web.RouteTableDef()

    async def body(request, *required):
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="invalid JSON")
        if not isinstance(data, dict) or any(k not in data for k in required):
            raise web.HTTPBadRequest(text=f"expected fields: {', '.join(required)}")
        return data

    def field(data, key, kind):
        v = data[key]
        # bool is an int subclass, so reject it explicitly.
        if not isinstance(v, kind) or isinstance(v, bool) or (isinstance(v, str) and not v.strip()):
            raise web.HTTPBadRequest(text=f"invalid {key}")
        return v

    @routes.post("/api/beeper/v1/app/setup/{step:response|register}")
    async def sign_in(request):
        data = await body(request)
        async with beeper.http.post(f"{beeper.url}/v1/app/setup/{request.match_info['step']}", json=data) as r:
            if r.status >= 400 or r.content_type != "application/json":
                return web.Response(status=r.status, body=await r.read(), content_type=r.content_type)
            result = await r.json()
        # Beeper returns the account token once; keep it server-side, the browser never needs it.
        token = (result.get("matrix") or {}).pop("accessToken", None)
        if token:
            save_token(token)
        return web.json_response(result)

    @routes.route("*", "/api/beeper/{tail:.*}")
    async def proxy(request):
        path = request.raw_path[len("/api/beeper"):]
        plain = urllib.parse.unquote(path).lower()
        if not path.startswith(PROXIED) or ".." in plain or (plain.startswith("/v1/bridges") and LOGINS in plain.split("?")[0] and request.method != "GET"):
            raise web.HTTPForbidden()
        # Beeper may match routes case-insensitively or decoded, so compare the same way.
        if RESET in urllib.parse.unquote(path).lower() and (await beeper.call("GET", "/v1/app/setup"))["state"] != "needs-cross-signing-setup":
            raise web.HTTPForbidden(text="the recovery key can only be created while setting up encryption")
        headers = {k: request.headers[k] for k in PASS_REQ if k in request.headers}
        data = await request.read() if request.can_read_body else None
        async with beeper.raw.request(request.method, URL(beeper.url + path, encoded=True), headers=headers, data=data) as r:
            headers = {k: r.headers[k] for k in PASS_RESP if k in r.headers}
            # Attachments are served from this origin: an .html or .svg someone sends you must not run as the app.
            headers |= {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}
            resp = web.StreamResponse(status=r.status, headers=headers)
            await resp.prepare(request)
            async for chunk in r.content.iter_chunked(1 << 16):
                await resp.write(chunk)
            await resp.write_eof()
            return resp

    @routes.get("/api/config")
    async def config(request):
        return web.json_response({"transcription": transcriber is not None, "token": "Authorization" in beeper.http.headers})

    async def chat_info(chat_id):
        chat = await beeper.chat(chat_id)
        return chat.get("title"), chat.get("network")

    # --- snooze ---

    @routes.get("/api/snoozes")
    async def list_snoozes(request):
        return web.json_response(rows(db, "select * from snoozes order by until"))

    @routes.post("/api/snoozes")
    async def snooze(request):
        data = await body(request, "chatID", "until")
        chat_id, until = field(data, "chatID", str), field(data, "until", int)
        title, network = await chat_info(chat_id)
        await beeper.archive(chat_id, True)
        db.execute("insert or replace into snoozes values (?, ?, ?, ?, ?)", (chat_id, title, network, until, now_ms()))
        return web.json_response({"ok": True})

    @routes.delete("/api/snoozes/{chat_id}")
    async def unsnooze(request):
        chat_id = request.match_info["chat_id"]
        await beeper.archive(chat_id, False)
        db.execute("delete from snoozes where chat_id = ?", (chat_id,))
        return web.json_response({"ok": True})

    # --- send later ---

    @routes.get("/api/scheduled")
    async def list_scheduled(request):
        return web.json_response(rows(db, "select * from scheduled order by send_at"))

    @routes.post("/api/scheduled")
    async def schedule(request):
        data = await body(request, "chatID", "text", "sendAt")
        chat_id, text, send_at = field(data, "chatID", str), field(data, "text", str), field(data, "sendAt", int)
        title, network = await chat_info(chat_id)
        cur = db.execute(
            "insert into scheduled (chat_id, chat_title, network, text, send_at, created_at) values (?, ?, ?, ?, ?, ?)",
            (chat_id, title, network, text, send_at, now_ms()),
        )
        return web.json_response(rows(db, "select * from scheduled where id = ?", cur.lastrowid)[0])

    @routes.patch("/api/scheduled/{id:\\d+}")
    async def edit_scheduled(request):
        data = await body(request)
        sets = {}
        if "text" in data:
            sets["text"] = field(data, "text", str)
        if "sendAt" in data:
            sets["send_at"] = field(data, "sendAt", int)
        # Editing is how a failed message gets retried.
        cur = db.execute(
            f"update scheduled set {''.join(f'{k} = ?, ' for k in sets)}error = null where id = ?",
            (*sets.values(), int(request.match_info["id"])),
        )
        if not cur.rowcount:
            raise web.HTTPNotFound()
        return web.json_response(rows(db, "select * from scheduled where id = ?", int(request.match_info["id"]))[0])

    @routes.delete("/api/scheduled/{id:\\d+}")
    async def unschedule(request):
        db.execute("delete from scheduled where id = ?", (int(request.match_info["id"]),))
        return web.json_response({"ok": True})

    # --- transcriptions ---

    @routes.get("/api/transcriptions")
    async def list_transcriptions(request):
        chat_id = request.query.get("chatID")
        if chat_id:
            return web.json_response(rows(db, "select * from transcriptions where chat_id = ? order by sent_at", chat_id))
        limit = min(int(request.query.get("limit", "50")), 500)
        return web.json_response(rows(db, "select * from transcriptions order by created_at desc limit ?", limit))

    @routes.post("/api/transcriptions")
    async def transcribe(request):
        if transcriber is None:
            raise web.HTTPNotFound(text="transcription is not configured")
        data = await body(request, "chatID", "messageID")
        chat_id, message_id = field(data, "chatID", str), field(data, "messageID", str)
        msg = await beeper.call("GET", f"/v1/chats/{chat_id}/messages/{message_id}")
        try:
            return web.json_response(await transcriber.transcribe(msg))
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e))

    # --- web app ---

    index = Path(static_dir) / "index.html"

    @routes.get("/")
    async def home(request):
        # Nix store files all date from 1970, so a browser revalidating would be told its old copy is current.
        return web.Response(body=index.read_bytes(), content_type="text/html", headers={"Cache-Control": "no-store"})

    @web.middleware
    async def known_host(request, handler):
        # Blocks DNS rebinding: another website pointing its own name at this server's address.
        if request.url.host not in hosts:
            raise web.HTTPForbidden(text=f"unknown host {request.host}: add it to web.allowed_hosts")
        return await handler(request)

    @web.middleware
    async def allowed_user(request, handler):
        # tailscale serve sets this header for tailnet users and strips any value a client sends.
        login = request.headers.get("Tailscale-User-Login", "").lower()
        if users and login not in users:
            raise web.HTTPForbidden(text=f"{login or 'this request'} is not in web.allowed_users (open it through tailscale serve)")
        return await handler(request)

    app = web.Application(middlewares=[known_host, allowed_user, same_origin, errors])
    app.add_routes(routes)
    app.router.add_static("/", static_dir)
    return app


@web.middleware
async def same_origin(request, handler):
    # Any website you visit can make your browser send it requests; only the app's own page may change things.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site, origin = request.headers.get("Sec-Fetch-Site"), request.headers.get("Origin")
        if site not in (None, "same-origin", "none") or (origin and urllib.parse.urlsplit(origin).netloc != request.host):
            raise web.HTTPForbidden(text="cross-site request refused")
    return await handler(request)


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        # Surface Beeper/engine failures to the web app instead of a bare 500.
        log.warning("%s %s: %s", request.method, request.path, describe(e))
        status = getattr(e, "status", 502)
        raise web.HTTPBadGateway(text=f"{status}: {getattr(e, 'message', None) or describe(e)}")


async def serve(app, host, port):
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("web app on http://%s:%d", host, port)
