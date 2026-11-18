"""A tiny stand-in for Beeper Server's Client API, for tests and UI work: python -m tests.fake_beeper [--setup]"""

import asyncio
import sys
import copy
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

TOKEN = "test-token"
NOTE = "!note:beeper.com"
AUDIO = Path(__file__).parent / "voice.ogg"


def iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def voice(id, chat, minutes_ago, sender="Alice", is_sender=False):
    return {
        "id": id, "chatID": chat, "accountID": "whatsapp", "senderID": sender, "senderName": sender,
        "sortKey": id, "timestamp": iso(minutes_ago), "type": "VOICE", "isSender": is_sender,
        "attachments": [{"type": "audio", "id": f"mxc://local/{id}", "isVoiceNote": True, "mimeType": "audio/ogg"}],
    }


def text(id, chat, minutes_ago, body, sender="Alice", is_sender=False):
    return {
        "id": id, "chatID": chat, "accountID": "whatsapp", "senderID": sender, "senderName": "You" if is_sender else sender,
        "sortKey": id, "timestamp": iso(minutes_ago), "type": "TEXT", "text": body, "isSender": is_sender,
    }


def chat(id, title, network, minutes_ago, type="single", archived=False, unread=0):
    return {
        "id": id, "accountID": network.lower(), "network": network, "title": title, "type": type,
        "unreadCount": unread, "isArchived": archived, "lastActivity": iso(minutes_ago),
        "participants": {"items": [], "hasMore": False, "total": 2},
    }


def seed():
    chats = {
        c["id"]: c for c in [
            chat("!alice:beeper.com", "Alice", "WhatsApp", 5, unread=2),
            chat("!family:beeper.com", "Family 🏡", "Signal", 30, type="group"),
            chat("!bob:beeper.com", "Bob Martin", "Telegram", 120),
            chat("!old:beeper.com", "Old project", "Slack", 6000, type="group", archived=True),
            # Only you in it, on your Beeper account; Signal's own self-chat must not be mistaken for it.
            {**chat(NOTE, "Note to self", "Beeper", 9000), "accountID": "matrix", "participants": {"items": [{"id": "me", "isSelf": True}], "hasMore": False, "total": 1}},
            {**chat("!signal-self:beeper.com", "Note to Self", "Signal", 9500), "participants": {"items": [{"id": "me", "isSelf": True}], "hasMore": False, "total": 1}},
        ]
    }
    messages = {cid: [] for cid in chats}
    messages["!alice:beeper.com"] = [
        text("a1", "!alice:beeper.com", 60, "Are you coming tonight?"),
        text("a2", "!alice:beeper.com", 50, "Yes! What time?", is_sender=True),
        voice("a3", "!alice:beeper.com", 6),
        text("a4", "!alice:beeper.com", 5, "Listen to this ☝️"),
    ]
    messages["!family:beeper.com"] = [text("f1", "!family:beeper.com", 30, "Sunday lunch at noon", sender="Mum")]
    messages["!bob:beeper.com"] = [text("b1", "!bob:beeper.com", 120, "Thanks for the help")]
    messages["!old:beeper.com"] = [text("o1", "!old:beeper.com", 6000, "Shipped 🎉", sender="Carol")]
    return chats, messages


RECOVERY_KEY = "EsTc 1234 abcd"
NEW_ACCOUNT_CODE = "000000"  # sign-in code that leads to account creation
PUBLIC = ("/v1/app/setup", "/v1/app/setup/start", "/v1/app/setup/email", "/v1/app/setup/response", "/v1/app/setup/register")


def make_app(state="ready"):
    app = web.Application()
    chats, messages = seed()
    app["chats"], app["messages"], app["sockets"] = chats, messages, []
    # Sign-in and encryption setup, as GET /v1/app/setup reports it.
    app["setup"] = {"state": state, "e2ee": {}, "verification": None}
    routes = web.RouteTableDef()

    @web.middleware
    async def auth(request, handler):
        public = request.path in PUBLIC and app["setup"]["state"] == "needs-login"
        if not public and request.headers.get("Authorization") != f"Bearer {TOKEN}":
            raise web.HTTPUnauthorized()
        return await handler(request)

    setup = app["setup"]

    def session(next_state):
        setup["state"] = next_state
        return {"matrix": {"accessToken": TOKEN, "userID": "@me:beeper.com", "deviceID": "DEV", "homeserver": "https://matrix.beeper.com"}, "session": dict(setup)}

    @routes.get("/v1/app/setup")
    async def setup_state(request):
        return web.json_response(setup)

    @routes.post("/v1/app/setup/start")
    async def setup_start(request):
        return web.json_response({"setupRequestID": "req1", "signInMethods": ["email"]})

    @routes.post("/v1/app/setup/email")
    async def setup_email(request):
        assert (await request.json())["setupRequestID"] == "req1"
        return web.Response(status=204)

    @routes.post("/v1/app/setup/response")
    async def setup_response(request):
        if (await request.json())["response"] == NEW_ACCOUNT_CODE:
            return web.json_response({"registrationRequired": True, "leadToken": "lead", "setupRequestID": "req1", "usernameSuggestions": ["me42"], "copy": {}})
        return web.json_response(session("needs-verification"))

    @routes.post("/v1/app/setup/register")
    async def setup_register(request):
        body = await request.json()
        assert body["acceptTerms"] is True and body["leadToken"] == "lead" and body["username"]
        return web.json_response(session("needs-cross-signing-setup"))

    @routes.post("/v1/app/setup/verification/recovery-key")
    async def recovery_key(request):
        if (await request.json())["recoveryKey"] != RECOVERY_KEY:
            return web.json_response({"message": "The recovery key is invalid", "code": "invalid_recovery_code"}, status=400)
        setup["state"] = "ready"
        return web.json_response({"session": setup})

    @routes.post("/v1/app/setup/verification/recovery-key/reset")
    async def reset(request):
        return web.json_response({"recoveryKey": RECOVERY_KEY, "session": setup})

    @routes.post("/v1/app/setup/verification/recovery-key/reset/confirm")
    async def reset_confirm(request):
        assert (await request.json())["recoveryKey"] == RECOVERY_KEY
        setup["state"] = "ready"
        return web.json_response({"session": setup})

    @routes.post("/v1/app/setup/verifications")
    async def verify_start(request):
        # The other device "accepts" straight away.
        setup["verification"] = {"id": "v1", "state": "ready", "direction": "outgoing", "methods": ["sas"], "purpose": "login", "availableActions": ["cancel", "sas.start"], "otherDevice": {"id": "PHONE", "name": "Beeper on iPhone"}}
        return web.json_response(setup["verification"])

    @routes.post("/v1/app/setup/verifications/{id}/{action:sas/start|sas/confirm|cancel}")
    async def verify_action(request):
        v, action = setup["verification"], request.match_info["action"]
        if action == "sas/start":
            v.update(state="sas_ready", availableActions=["sas.confirm", "cancel"], sas={"emojis": "🐶 🔑 🎸 🌵 🚀 ☂️ 🍕", "decimals": "1234 5678 9012"})
        elif action == "sas/confirm":
            v.update(state="done", availableActions=[])
            setup["state"] = "ready"
        else:
            v.update(state="cancelled", availableActions=[], error={"code": "user", "reason": "Cancelled"})
        return web.json_response(v)

    def find(chat_id):
        if chat_id not in chats:
            raise web.HTTPNotFound()
        return chats[chat_id]

    def with_preview(c):
        return {**c, "preview": (messages[c["id"]] or [None])[-1]}

    def bridge(id, name, provider, flows, accounts=()):
        return {"id": id, "displayName": name, "provider": provider, "status": "available", "type": id.removeprefix("local-"),
                "network": name.lower(), "supportsMultipleAccounts": False, "accounts": list(accounts), "flows": flows}

    def account(bridge, status="connected"):
        return {"accountID": f"{bridge['id']}_me", "network": bridge["displayName"], "status": status, "user": {"id": "me", "fullName": "Me", "isSelf": True},
                "bridge": {"id": bridge["id"], "provider": bridge["provider"], "type": bridge["type"]}}

    # Each flow is a script of login steps; the last one connects the account.
    qr = [{"type": "display_and_wait", "instructions": "Scan this QR code with WhatsApp on your phone", "display": {"type": "qr", "data": f"2@fake-qr-{n}"}} for n in (1, 2)]
    phone = [
        {"type": "user_input", "instructions": "Enter your WhatsApp phone number", "fields": [{"id": "phone", "label": "Phone number", "type": "tel", "placeholder": "+33 6 12 34 56 78"}]},
        {"type": "display_and_wait", "instructions": "In WhatsApp, open Linked devices, tap Link with phone number, and type this code", "display": {"type": "code", "data": "FAKE-CODE"}},
    ]
    cookies = [{"type": "cookies", "url": "https://www.linkedin.com/login", "instructions": "Sign in to LinkedIn, then paste these cookies", "fields": [{"id": "li_at", "name": "li_at", "type": "cookie"}, {"id": "jsession", "name": "JSESSIONID", "type": "cookie"}]}]
    discord = bridge("discordgo", "Discord", "cloud", [])
    discord["accounts"].append(account(discord, "connection_required"))
    bridges = app["bridges"] = {b["id"]: b for b in [
        bridge("local-whatsapp", "WhatsApp", "local", {"qr": ("QR code", qr), "phone": ("Pairing code", phone)}),
        bridge("local-linkedin", "LinkedIn", "local", {"cookies": ("Browser cookies", cookies)}),
        discord,
    ]}
    sessions = {}

    def public(b):
        return {k: v for k, v in b.items() if k != "flows"} | {"activeAccountCount": len(b["accounts"])}

    @routes.get("/v1/accounts")
    async def accounts(request):
        return web.json_response([a for b in bridges.values() for a in b["accounts"]])

    @routes.get("/v1/bridges")
    async def list_bridges(request):
        return web.json_response({"items": [public(b) for b in bridges.values()]})

    @routes.get("/v1/bridges/{id}/login-flows")
    async def login_flows(request):
        b = bridges[request.match_info["id"]]
        return web.json_response({"items": [{"id": k, "name": name} for k, (name, _) in b["flows"].items()]})

    def view(sess):
        out = {k: sess[k] for k in ("bridgeID", "loginSessionID", "status", "accountID", "account", "error") if sess.get(k)}
        if sess["status"].startswith("waiting"):
            out["currentStep"] = sess["steps"][0] | {"stepID": f"step{len(sess['steps'])}"}
        return web.json_response(out)

    def advance(sess):
        sess["steps"].pop(0)
        if sess["steps"]:
            sess["status"] = {"user_input": "waiting_for_input", "cookies": "waiting_for_cookies", "display_and_wait": "waiting_for_display"}[sess["steps"][0]["type"]]
            return
        b = bridges[sess["bridgeID"]]
        b["accounts"] = [a for a in b["accounts"] if a["accountID"] != f"{b['id']}_me"] + [account(b)]
        sess.update(status="complete", account=b["accounts"][-1])

    @routes.post("/v1/bridges/{id}/login-sessions")
    async def login_start(request):
        b = bridges[request.match_info["id"]]
        body = await request.json() if request.can_read_body else {}
        flow = body.get("flowID") or next(iter(b["flows"]))
        sess = {"bridgeID": b["id"], "loginSessionID": f"ls{len(sessions) + 1}", "accountID": body.get("accountID"), "steps": [dict(x) for x in b["flows"][flow][1]]}
        sessions[sess["loginSessionID"]] = sess
        sess["steps"].insert(0, {})
        advance(sess)
        return view(sess)

    @routes.get("/v1/bridges/{id}/login-sessions/{sid}")
    async def login_get(request):
        return view(sessions[request.match_info["sid"]])

    @routes.delete("/v1/bridges/{id}/login-sessions/{sid}")
    async def login_cancel(request):
        sessions[request.match_info["sid"]]["status"] = "cancelled"
        return web.json_response({"bridgeID": request.match_info["id"], "loginSessionID": request.match_info["sid"], "status": "cancelled"})

    @routes.post("/v1/bridges/{id}/login-sessions/{sid}/steps/{step}")
    async def login_step(request):
        sess, body = sessions[request.match_info["sid"]], await request.json()
        step = sess["steps"][0] if sess["status"].startswith("waiting") else None
        if not step or request.match_info["step"] != f"step{len(sess['steps'])}" or body.get("type") != step["type"]:
            return web.json_response({"message": "Login step does not match the current session state", "code": "INVALID_LOGIN_STEP"}, status=409)
        if step["type"] == "display_and_wait":
            await asyncio.sleep(1)  # like Beeper: answers once the phone scanned the code (or the code refreshed)
        elif any(not (body.get("fields") or {}).get(f["id"]) for f in step["fields"]):
            return web.json_response({"message": "Missing field", "code": "invalid_parameter"}, status=400)
        elif (body.get("fields") or {}).get("phone") == "0":
            sess.update(status="failed", error={"message": "That number isn't on WhatsApp", "code": "login_failed"})
            return view(sess)
        advance(sess)
        return view(sess)

    @routes.get("/v1/chats")
    async def list_chats(request):
        return web.json_response({"items": [with_preview(c) for c in chats.values()], "hasMore": False})

    @routes.get("/v1/chats/search")
    async def search(request):
        inbox = request.query.get("inbox")
        items = [with_preview(c) for c in chats.values() if inbox is None or (inbox == "archive") == c["isArchived"]]
        items.sort(key=lambda c: c["lastActivity"], reverse=True)
        return web.json_response({"items": items, "hasMore": False})

    @routes.get("/v1/chats/{id}")
    async def get_chat(request):
        return web.json_response(find(request.match_info["id"]))

    @routes.post("/v1/chats/{id}/archive")
    async def archive(request):
        find(request.match_info["id"])["isArchived"] = (await request.json())["archived"]
        return web.Response(status=200)

    @routes.get("/v1/chats/{id}/messages")
    async def list_messages(request):
        find(request.match_info["id"])
        return web.json_response({"items": list(reversed(messages[request.match_info["id"]])), "hasMore": False})

    @routes.get("/v1/chats/{id}/messages/{mid}")
    async def get_message(request):
        for m in messages[find(request.match_info["id"])["id"]]:
            if m["id"] == request.match_info["mid"]:
                return web.json_response(m)
        raise web.HTTPNotFound()

    @routes.post("/v1/chats/{id}/messages")
    async def send(request):
        c = find(request.match_info["id"])
        body = await request.json()
        m = text(f"s{time.time_ns()}", c["id"], 0, body["text"], is_sender=True)
        messages[c["id"]].append(m)
        c["lastActivity"] = m["timestamp"]
        return web.json_response({"chatID": c["id"], "pendingMessageID": m["id"]})

    @routes.post("/v1/assets/download")
    async def download(request):
        return web.json_response({"srcURL": str(request.url.with_path("/v1/assets/serve").with_query(url=(await request.json())["url"]))})

    @routes.get("/v1/assets/serve")
    async def serve(request):
        return web.FileResponse(AUDIO, headers={"Content-Type": "audio/ogg"})

    @routes.get("/v1/ws")
    async def ws(request):
        sock = web.WebSocketResponse()
        await sock.prepare(request)
        app["sockets"].append(sock)
        await sock.send_json({"type": "ready"})
        async for msg in sock:
            if msg.json().get("type") == "subscriptions.set":
                await sock.send_json({"type": "subscriptions.updated"})
        return sock

    app.middlewares.append(auth)
    app.add_routes(routes)
    return app


async def push(app, msg):
    """Deliver a new message like Beeper does: store it and announce it on every socket."""
    app["messages"][msg["chatID"]].append(msg)
    for sock in app["sockets"]:
        await sock.send_json({"type": "message.upserted", "entries": [copy.deepcopy(msg)]})


if __name__ == "__main__":
    # --setup starts signed out, to try the web app's setup screen.
    web.run_app(make_app("needs-login" if "--setup" in sys.argv else "ready"), host="127.0.0.1", port=23373)
