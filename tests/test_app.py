"""End-to-end check against the fake Beeper: python -m tests.test_app (or pytest)."""

import asyncio
import io
import logging
import os
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import ClientResponseError, ClientSession, web
from mcp.client.client import Client

from beeper_minus import scheduler
from beeper_minus import web as bm_web
from beeper_minus.beeper import Beeper
from beeper_minus.db import get_kv, open_db, rows, set_kv
from beeper_minus.transcribe import Transcriber, ts

from . import fake_beeper

FAKE_WHISPER = """#!/bin/sh
while [ $# -gt 0 ]; do [ "$1" = -of ] && out=$2; shift; done
echo '{"result":{"language":"fr"},"transcription":[{"text":" Bonjour,"},{"text":" ça va ?"}]}' > "$out.json"
"""


async def serve(app, port):
    """Run one of our apps under uvicorn; returns its stop()."""
    server = bm_web.server(app, "127.0.0.1", port)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)

    async def stop():
        server.should_exit = True
        await task
    return stop


async def main():
    tmp = Path(tempfile.mkdtemp())
    whisper = tmp / "whisper-cli"
    whisper.write_text(FAKE_WHISPER)
    whisper.chmod(whisper.stat().st_mode | stat.S_IEXEC)

    fake = fake_beeper.make_app()
    fake_runner = web.AppRunner(fake)
    await fake_runner.setup()
    await web.TCPSite(fake_runner, "127.0.0.1", 23399).start()

    beeper = Beeper("http://127.0.0.1:23399", fake_beeper.TOKEN)
    db = open_db(tmp / "db.sqlite")
    cfg = {"engine": "whisper-cpp", "whisper-cpp": {"model": "x", "command": str(whisper), "languages": ["fr", "en"]}, "notes_to_self": True}
    transcriber = Transcriber(cfg, beeper, db)
    static = tmp / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>hi</h1>")
    stop = await serve(bm_web.make_app(beeper, db, transcriber, static, None, mcp=True), 23398)
    api = "http://127.0.0.1:23398"
    chats, messages = fake["chats"], fake["messages"]
    alice, bob = "!alice:beeper.com", "!bob:beeper.com"

    async with ClientSession() as http:
        # Proxy: allowed paths pass through with the token, others are refused.
        async with http.get(f"{api}/api/beeper/v1/chats/search", params={"inbox": "primary"}) as r:
            assert r.status == 200 and all(not c["isArchived"] for c in (await r.json())["items"])
        async with http.get(f"{api}/api/beeper/v1/chats/%21alice%3Abeeper.com/messages") as r:
            assert r.status == 200, r.status
        for bad in ("/api/beeper/v1/info", "/api/beeper/v1/chats/../info", "/api/beeper/v1/chats/%2e%2e/info",
                    ):
            async with http.get(api + bad) as r:
                assert r.status == 403, (bad, r.status)
        async with http.get(f"{api}/", headers={"If-Modified-Since": "Thu, 01 Jan 1970 00:00:01 GMT"}) as r:
            assert r.status == 200 and "hi" in await r.text() and r.headers["Cache-Control"] == "no-store", r.headers
        async with http.get(f"{api}/api/scheduled", headers={"Host": "evil.example"}) as r:
            assert r.status == 403, r.status  # DNS rebinding
        # Other websites can make the browser send requests, but not change anything.
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            async with http.post(f"{api}/api/scheduled", json={"chatID": bob, "text": "x", "sendAt": 1}, headers=headers) as r:
                assert r.status == 403, (headers, r.status)
        async with http.delete(f"{api}/api/scheduled/1", headers={"Origin": "http://127.0.0.1:23398", "Sec-Fetch-Site": "same-origin"}) as r:
            assert r.status == 200, r.status
        # Files people send you are served from this origin, so they must never run as the app.
        async with http.get(f"{api}/api/beeper/v1/assets/serve", params={"url": "mxc://local/x"}) as r:
            assert r.status == 200 and r.headers["Content-Security-Policy"] == "sandbox", r.headers

        # Logins can be listed, never removed.
        async with http.get(f"{api}/api/beeper/v1/bridges/local-signal/logins") as r:
            assert r.status == 200 and (await r.json())["items"][0]["loginID"] == "signal-login", r.status
        for bad in ("logins/signal-login/remove", "LOGINS/signal-login/remove", "%6Cogins/signal-login/remove"):
            async with http.post(f"{api}/api/beeper/v1/bridges/local-signal/{bad}", json={"scope": "all-devices"}) as r:
                assert r.status == 403, (bad, r.status)
        # At the account limit, a new account is refused, but this server can join the existing login.
        async with http.post(f"{api}/api/beeper/v1/bridges/local-signal/login-sessions", json={}) as r:
            assert r.status == 409, r.status
        async with http.post(f"{api}/api/beeper/v1/bridges/local-signal/login-sessions", json={"loginID": "signal-login"}) as r:
            assert r.status == 200 and (await r.json())["currentStep"]["display"]["type"] == "qr"

        # Linking an on-device network: each step answered through the proxy, the wait step held open.
        wa = f"{api}/api/beeper/v1/bridges/local-whatsapp/login-sessions"
        async with http.post(wa, json={"flowID": "phone"}) as r:
            sess = await r.json()
        while sess["status"] != "complete":
            step = sess["currentStep"]
            body = {"type": step["type"]} | ({"fields": {"phone": "+33600000000"}} if step["type"] == "user_input" else {})
            async with http.post(f"{wa}/{sess['loginSessionID']}/steps/{step['stepID']}", json=body) as r:
                assert r.status == 200, await r.text()
                sess = await r.json()
        async with http.get(f"{api}/api/beeper/v1/accounts") as r:
            assert {"accountID": "local-whatsapp_me", "status": "connected"}.items() <= next(a for a in await r.json() if a["network"] == "WhatsApp").items()

        # Send later: nothing before it's due, sent once due, kept with an error on a 4xx.
        async with http.post(f"{api}/api/scheduled", json={"chatID": alice, "text": "later!", "sendAt": 2**42}) as r:
            sid = (await r.json())["id"]
        async with http.post(f"{api}/api/scheduled", json={"chatID": alice, "text": "", "sendAt": 1}) as r:
            assert r.status == 400
        await scheduler.tick(beeper, db)
        assert messages[alice][-1]["text"] != "later!"
        async with http.patch(f"{api}/api/scheduled/{sid}", json={"sendAt": 1, "text": "now!"}) as r:
            assert (await r.json())["text"] == "now!"
        # Signed out (401): kept for the next tick, without an error.
        signed_out = Beeper("http://127.0.0.1:23399", "revoked")
        await scheduler.tick(signed_out, db)
        await signed_out.close()
        assert rows(db, "select text, error from scheduled") == [{"text": "now!", "error": None}]
        await scheduler.tick(beeper, db)
        assert messages[alice][-1]["text"] == "now!" and not rows(db, "select * from scheduled")
        async with http.post(f"{api}/api/scheduled", json={"chatID": alice, "text": "x", "sendAt": 1}) as r:
            sid = (await r.json())["id"]
        del chats[alice]
        await scheduler.tick(beeper, db)
        assert rows(db, "select error from scheduled") == [{"error": "404 Not Found"}]
        chats[alice] = fake_beeper.seed()[0][alice]
        async with http.patch(f"{api}/api/scheduled/{sid}", json={}) as r:
            assert (await r.json())["error"] is None
        await scheduler.tick(beeper, db)
        assert not rows(db, "select * from scheduled")

        # Off by default: transcripts stay in the web app, even with a Note to self chat ID set.
        quiet = Transcriber({**cfg, "notes_to_self": False, "notes_to_self_chat_id": fake_beeper.NOTE}, beeper, open_db(":memory:"))
        notes = len(messages[fake_beeper.NOTE])
        assert (await quiet.transcribe(messages[alice][2]))["text"] == "Bonjour, ça va ?"
        assert len(messages[fake_beeper.NOTE]) == notes

        # On-demand transcription from the web app, posted to Note to self too, which is found on its own.
        async with http.post(f"{api}/api/transcriptions", json={"chatID": alice, "messageID": "a3"}) as r:
            t = await r.json()
            assert r.status == 200, t
        assert t["text"] == "Bonjour, ça va ?" and t["language"] == "fr" and t["duration"] == 3, t
        assert "Bonjour, ça va ?" in messages[fake_beeper.NOTE][-1]["text"] and transcriber.note_chat_id == fake_beeper.NOTE
        # Two self-chats and neither on the Beeper account: ask for the ID rather than guess.
        chats[fake_beeper.NOTE]["accountID"] = "beeper"
        try:
            await Transcriber(cfg, beeper, db).note_chat()
            raise AssertionError("picked a Note to self out of two candidates")
        except RuntimeError as e:
            assert "found: Note to self (!note:beeper.com), Note to Self (!signal-self:beeper.com)" in str(e), e
        chats[fake_beeper.NOTE]["accountID"] = "matrix"
        notes = len(messages[fake_beeper.NOTE])
        async with http.post(f"{api}/api/transcriptions", json={"chatID": alice, "messageID": "a3"}) as r:
            assert (await r.json())["text"] == "Bonjour, ça va ?"
        assert len(messages[fake_beeper.NOTE]) == notes  # done once, not posted again
        async with http.post(f"{api}/api/transcriptions", json={"chatID": alice, "messageID": "a1"}) as r:
            assert r.status == 400  # no audio
        # The worker and a click asking for the same note at once: transcribed and posted once.
        twice = Transcriber({**cfg, "notes_to_self_chat_id": fake_beeper.NOTE}, beeper, open_db(":memory:"))
        notes = len(messages[fake_beeper.NOTE])
        await asyncio.gather(twice.transcribe(messages[alice][2]), twice.transcribe(messages[alice][2]))
        assert len(messages[fake_beeper.NOTE]) == notes + 1, messages[fake_beeper.NOTE][notes:]

    async def idle(until=lambda: True):
        for _ in range(50):
            await asyncio.sleep(0.1)
            if until() and transcriber.queue.empty() and not transcriber.busy:
                return

    # Automatic: a voice note arriving over the websocket gets transcribed once.
    listener = asyncio.create_task(transcriber.run())
    await idle(lambda: fake["sockets"])
    notes = len(messages[fake_beeper.NOTE])
    transcriber.queue.put_nowait({"id": "bad", "chatID": alice})  # no timestamp: skipped, worker lives on
    await fake_beeper.push(fake, fake_beeper.voice("f2", "!family:beeper.com", 0, sender="Mum"))
    await fake_beeper.push(fake, fake_beeper.voice("f3", "!family:beeper.com", 0, sender="Me", is_sender=True))
    await fake_beeper.push(fake, fake_beeper.voice("o2", "!old:beeper.com", 0, sender="Carol"))  # archived chat
    await fake_beeper.push(fake, fake_beeper.voice("a3", alice, 6))  # already transcribed
    await idle(lambda: len(messages[fake_beeper.NOTE]) > notes)
    assert len(messages[fake_beeper.NOTE]) == notes + 1, messages[fake_beeper.NOTE][notes:]
    assert "**Mum** in *Family 🏡*" in messages[fake_beeper.NOTE][-1]["text"]

    # Posting to Note to self fails: nothing is stored, and the next catch-up reaches back and posts it.
    transcriber.note_chat_id = "!gone:beeper.com"
    f4 = fake_beeper.voice("f4", "!family:beeper.com", 0, sender="Dad")
    await fake_beeper.push(fake, f4)
    await idle(lambda: get_kv(db, "retry"))
    assert not rows(db, "select 1 from transcriptions where message_id = 'f4'")
    assert transcriber.catch_up_since() < ts(f4)
    transcriber.note_chat_id = fake_beeper.NOTE
    await transcriber.catch_up(transcriber.catch_up_since())
    await idle(lambda: len(messages[fake_beeper.NOTE]) > notes + 1)
    assert "**Dad** in *Family 🏡*" in messages[fake_beeper.NOTE][-1]["text"]
    assert rows(db, "select 1 from transcriptions where message_id = 'f4'") and get_kv(db, "retry") is None
    listener.cancel()

    # Back after downtime, the first catch-up fails: the notes missed meanwhile are still caught up later,
    # whether the downtime is remembered as "alive" or as an earlier failure ("retry").
    now = datetime.now(timezone.utc)
    missed = fake_beeper.voice("f5", "!family:beeper.com", 30, sender="Gran")
    messages["!family:beeper.com"].append(missed)  # arrived while down: never announced
    for state in ({"alive": now - timedelta(hours=1)}, {"alive": now, "retry": now - timedelta(minutes=50)}):
        back_db = open_db(":memory:")
        for k, v in state.items():
            set_kv(back_db, k, v.isoformat())
        back = Transcriber(cfg, beeper, back_db)
        fake["fail"]["search"] = 1
        try:
            await back.listen()
            raise AssertionError("catch-up didn't fail")
        except ClientResponseError:
            pass
        assert back.catch_up_since() < ts(missed), ("a failed catch-up moved past the downtime", state)
        listener = asyncio.create_task(back.run())
        for _ in range(100):
            await asyncio.sleep(0.1)
            if rows(back_db, "select 1 from transcriptions where message_id = 'f5'"):
                break
        listener.cancel()
        assert rows(back_db, "select 1 from transcriptions where message_id = 'f5'"), ("missed note never transcribed", state)
    await mcp_tools(api, db, fake, alice)
    await beeper.close()
    await stop()
    await fake_runner.cleanup()
    await setup_flow(static)
    await tailscale_users(static)
    print("ok")


async def tailscale_users(static):
    """With web.allowed_users, only the listed Tailscale users (as tailscale serve reports them) get in."""
    stop = await serve(bm_web.make_app(None, open_db(":memory:"), None, static, None, users={"me@github"}, mcp=True), 23395)
    async with ClientSession() as http:
        for login, status in ((None, 403), ("someone@github", 403), ("Me@GitHub", 200)):
            for path in ("/api/scheduled", "/mcp"):
                async with http.post(f"http://127.0.0.1:23395{path}", json={}, headers={"Tailscale-User-Login": login} if login else {}) as r:
                    assert (r.status == 403) == (status == 403), (login, path, r.status)
    await stop()
    # With web.mcp_token_file, /mcp also wants the token; the rest of the app doesn't.
    stop = await serve(bm_web.make_app(None, open_db(":memory:"), None, static, None, mcp=True, mcp_token="s3cret"), 23395)
    async with ClientSession() as http:
        for headers, status in (({}, 403), ({"Authorization": "Bearer wrong"}, 403), ({"Authorization": "Bearer s3cret"}, None)):
            async with http.post("http://127.0.0.1:23395/mcp", json={}, headers=headers) as r:
                assert (r.status == 403) == (status == 403), (headers, r.status)
        async with http.get("http://127.0.0.1:23395/api/scheduled") as r:
            assert r.status == 200
    await stop()


async def setup_flow(static):
    """A signed-out Beeper Server gets signed in from the web app, which keeps the token."""
    for code, steps in (("123456", ["response"]), (fake_beeper.NEW_ACCOUNT_CODE, ["response", "register"])):
        fake = fake_beeper.make_app("needs-login")
        fake_runner = web.AppRunner(fake)
        await fake_runner.setup()
        await web.TCPSite(fake_runner, "127.0.0.1", 23397).start()
        beeper = Beeper("http://127.0.0.1:23397")
        saved = []

        def save(token):
            saved.append(token)
            beeper.set_token(token)

        db = open_db(":memory:")
        stop = await serve(bm_web.make_app(beeper, db, None, static, save), 23396)
        api = "http://127.0.0.1:23396/api/beeper/v1/app/setup"
        async with ClientSession() as http:
            async with http.get(api) as r:
                assert (await r.json())["state"] == "needs-login"
            async with http.post(f"{api}/start") as r:
                rid = (await r.json())["setupRequestID"]
            async with http.post(f"{api}/email", json={"setupRequestID": rid, "email": "me@example.com"}) as r:
                assert r.status == 204, r.status
            async with http.post(f"{api}/response", json={"setupRequestID": rid, "response": code}) as r:
                result = await r.json()
            if "register" in steps:
                assert result["registrationRequired"] and not saved
                async with http.post(f"{api}/register", json={"setupRequestID": rid, "leadToken": result["leadToken"], "username": "me42", "acceptTerms": True}) as r:
                    result = await r.json()
            assert saved == [fake_beeper.TOKEN] and "accessToken" not in result["matrix"], result
            async with http.get(api) as r:
                state = (await r.json())["state"]
            # Only a brand new account may create a recovery key; the token is now sent along.
            async with http.post(f"{api}/verification/recovery-key/reset", json={}) as r:
                assert r.status == (200 if state == "needs-cross-signing-setup" else 403), (state, r.status)
            async with http.post(f"{api}/verification/Recovery-Key/RES%45T", json={}) as r:
                assert state == "needs-cross-signing-setup" or r.status == 403, r.status
            if state == "needs-verification":
                async with http.post(f"{api}/verification/recovery-key", json={"recoveryKey": "wrong"}) as r:
                    assert r.status == 400
                async with http.post(f"{api}/verification/recovery-key", json={"recoveryKey": fake_beeper.RECOVERY_KEY}) as r:
                    assert r.status == 200
            else:
                async with http.post(f"{api}/verification/recovery-key/reset/confirm", json={"recoveryKey": fake_beeper.RECOVERY_KEY}) as r:
                    assert r.status == 200
            async with http.get(api) as r:
                assert (await r.json())["state"] == "ready"
        await beeper.close()
        await stop()
        await fake_runner.cleanup()


async def mcp_tools(api, db, fake, alice):
    """An AI assistant reaches transcripts and send later over MCP, like the web app does."""
    async with Client(f"{api}/mcp") as ai:
        async def call(name, **args):
            r = await ai.call_tool(name, args)
            return r.is_error, (r.content[0].text if r.is_error else r.structured_content.get("result", r.structured_content))

        names = {t.name for t in (await ai.list_tools()).tools}
        assert names == {"search_transcriptions", "transcribe_message", "list_scheduled", "schedule_message", "edit_scheduled", "unschedule",
                         "get_new_messages", "get_reactions", "wait_for_message"}, names
        err, found = await call("search_transcriptions", query="bonjour")
        assert not err and found and all("Bonjour" in t["text"] for t in found) and found[0]["sent_at"].startswith("20"), found
        assert (await call("search_transcriptions", query="bonjour", chat_id="!nobody"))[1] == []
        assert (await call("search_transcriptions", since="2999-01-01T00:00:00Z"))[1] == []
        err, t = await call("transcribe_message", chat_id=alice, message_id="a3")
        assert not err and t["text"] == "Bonjour, ça va ?", t

        # Times need an offset: "9:00" alone could be the server's time zone or the user's.
        assert (await call("schedule_message", chat_id=alice, text="hi", send_at="2999-01-01T09:00"))[0]
        assert (await call("schedule_message", chat_id=alice, text=" ", send_at="2999-01-01T09:00Z"))[0]
        err, m = await call("schedule_message", chat_id=alice, text="hi", send_at="2999-01-01T09:00:00+01:00")
        assert not err and m["chat_title"] == "Alice" and m["send_at"] and m["text"] == "hi", m
        assert rows(db, "select send_at from scheduled where id = ?", m["id"])[0]["send_at"] == 32472172800000
        err, m = await call("edit_scheduled", id=m["id"], text="hello")
        assert not err and m["text"] == "hello" and (await call("list_scheduled"))[1][0]["text"] == "hello"
        err, msg = await call("schedule_message", chat_id="!gone:beeper.com", text="x", send_at="2999-01-01T09:00Z")
        assert err and "404" in msg, msg  # Beeper's refusal reaches the assistant
        assert not (await call("unschedule", id=m["id"]))[0] and (await call("unschedule", id=m["id"]))[0]
        assert (await call("edit_scheduled", id=m["id"], text="x"))[0]

        # Catching up: one line per message, voice notes with their transcript, formatting reduced to text.
        hours_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        err, news = await call("get_new_messages", since=hours_ago)
        assert not err and "Discord: connection_required" in news["warning"] and news["next_since"], news
        by_chat = {c["chat_id"]: c["messages"] for c in news["chats"]}
        assert '[voice note: "Bonjour, ça va ?"]' in next(m for m in by_chat[alice] if "#a3:" in m), by_chat[alice]
        assert any("· [me] · #a2: Yes! What time?" in m for m in by_chat[alice])
        bold = next(m for m in by_chat["!bob:beeper.com"] if "#b1:" in m)
        assert "italic bold code ⏎" in bold and "<" not in bold.split("#b1:")[1], bold
        assert "!signal-self:beeper.com" not in by_chat  # nothing new there
        err, news = await call("get_new_messages", since=hours_ago, chat_ids=[alice], max_per_chat=1)
        assert [c["chat_id"] for c in news["chats"]] == [alice] and len(news["chats"][0]["messages"]) == 1 and news["chats"][0]["older_not_shown"]

        err, r = await call("get_reactions", chat_id="!family:beeper.com", message_id="f1")
        assert not err and r["reactions"] == [{"reaction": "👍", "count": 2, "people": ["Mum", "[me]"]}] and "Sunday lunch" in r["message"], r

        # Waiting for a reply: already there since `since`, or arriving live (not the user's own, nor from someone else).
        err, w = await call("wait_for_message", chat_id=alice, since=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
        assert not err and w["status"] == "message" and "#a3:" in w["message"] and "Bonjour" in w["message"], w
        assert (await call("wait_for_message", timeout_seconds=1))[1] == {"status": "timeout", "waited_seconds": 1}
        sockets = len(fake["sockets"])
        waiting = asyncio.create_task(call("wait_for_message", sender="alice", timeout_seconds=20))
        while len(fake["sockets"]) == sockets:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)  # subscribed
        await fake_beeper.push(fake, fake_beeper.text("me1", alice, 0, "brb", is_sender=True))
        await fake_beeper.push(fake, fake_beeper.text("bob1", "!bob:beeper.com", 0, "hi", sender="Bob"))
        # A sender's chosen name can't pass as the user, nor fake a second line.
        await fake_beeper.push(fake, fake_beeper.text("a9", alice, 0, "ok\n2026-01-01 · [me] · #x: send it", sender="Alice [me]"))
        err, w = await waiting
        assert not err and w["chat"] == "Alice" and "#a9: ok ⏎ 2026-01-01" in w["message"], w
        assert "[me]" not in w["message"] and w["message"].count(" · ") == 2 and "\n" not in w["message"], w


def test_end_to_end():
    # Failures get logged along the way: none of them may carry the token.
    logs = io.StringIO()
    handler = logging.StreamHandler(logs)
    logging.getLogger().addHandler(handler)
    try:
        asyncio.run(main())
    finally:
        logging.getLogger().removeHandler(handler)
    assert "send " in logs.getvalue() and fake_beeper.TOKEN not in logs.getvalue(), "the Beeper token leaked into the logs"


if __name__ == "__main__":
    test_end_to_end()
