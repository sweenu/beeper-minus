"""A tiny stand-in for Beeper Server's Client API, for tests and UI work: python -m tests.fake_beeper"""

import asyncio
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
            chat(NOTE, "Note to self", "Beeper", 9000),
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


def make_app():
    app = web.Application()
    chats, messages = seed()
    app["chats"], app["messages"], app["sockets"] = chats, messages, []
    routes = web.RouteTableDef()

    @web.middleware
    async def auth(request, handler):
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            raise web.HTTPUnauthorized()
        return await handler(request)

    def find(chat_id):
        if chat_id not in chats:
            raise web.HTTPNotFound()
        return chats[chat_id]

    def with_preview(c):
        return {**c, "preview": (messages[c["id"]] or [None])[-1]}

    @routes.get("/v1/accounts")
    async def accounts(request):
        return web.json_response([{"accountID": "whatsapp", "network": "WhatsApp", "status": "connected", "user": {"id": "me", "isSelf": True}, "bridge": {}}])

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
    web.run_app(make_app(), host="127.0.0.1", port=23373)
