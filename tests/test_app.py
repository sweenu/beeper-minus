"""End-to-end check against the fake Beeper: python -m tests.test_app (or pytest)."""

import asyncio
import io
import logging
import os
import stat
import tempfile
from pathlib import Path

from aiohttp import ClientSession, web

from beeper_minus import scheduler
from beeper_minus import web as bm_web
from beeper_minus.beeper import Beeper
from beeper_minus.db import get_kv, open_db, rows
from beeper_minus.transcribe import Transcriber, ts

from . import fake_beeper

FAKE_WHISPER = """#!/bin/sh
while [ $# -gt 0 ]; do [ "$1" = -of ] && out=$2; shift; done
echo '{"result":{"language":"fr"},"transcription":[{"text":" Bonjour,"},{"text":" ça va ?"}]}' > "$out.json"
"""


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
    runner = web.AppRunner(bm_web.make_app(beeper, db, transcriber, static, None))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 23398).start()
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
        async with http.get(f"{api}/api/snoozes", headers={"Host": "evil.example"}) as r:
            assert r.status == 403, r.status  # DNS rebinding
        # Other websites can make the browser send requests, but not change anything.
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            async with http.post(f"{api}/api/snoozes", json={"chatID": bob, "until": 1}, headers=headers) as r:
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

        # Snooze archives now and unarchives once due.
        async with http.post(f"{api}/api/snoozes", json={"chatID": bob, "until": 1}) as r:
            assert r.status == 200
        assert chats[bob]["isArchived"]
        assert rows(db, "select chat_title from snoozes") == [{"chat_title": "Bob Martin"}]
        # Signed out (401): kept for the next tick, not dropped.
        signed_out = Beeper("http://127.0.0.1:23399", "revoked")
        await scheduler.tick(signed_out, db)
        await signed_out.close()
        assert chats[bob]["isArchived"] and rows(db, "select chat_id from snoozes") == [{"chat_id": bob}]
        await scheduler.tick(beeper, db)
        assert not chats[bob]["isArchived"] and not rows(db, "select * from snoozes")

        # Send later: nothing before it's due, sent once due, kept with an error on a 4xx.
        async with http.post(f"{api}/api/scheduled", json={"chatID": alice, "text": "later!", "sendAt": 2**42}) as r:
            sid = (await r.json())["id"]
        async with http.post(f"{api}/api/scheduled", json={"chatID": alice, "text": "", "sendAt": 1}) as r:
            assert r.status == 400
        await scheduler.tick(beeper, db)
        assert messages[alice][-1]["text"] != "later!"
        async with http.patch(f"{api}/api/scheduled/{sid}", json={"sendAt": 1, "text": "now!"}) as r:
            assert (await r.json())["text"] == "now!"
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
    await beeper.close()
    await runner.cleanup()
    await fake_runner.cleanup()
    await setup_flow(static)
    await tailscale_users(static)
    print("ok")


async def tailscale_users(static):
    """With web.allowed_users, only the listed Tailscale users (as tailscale serve reports them) get in."""
    runner = web.AppRunner(bm_web.make_app(None, open_db(":memory:"), None, static, None, users={"me@github"}))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 23395).start()
    async with ClientSession() as http:
        for login, status in ((None, 403), ("someone@github", 403), ("Me@GitHub", 200)):
            async with http.get("http://127.0.0.1:23395/api/snoozes", headers={"Tailscale-User-Login": login} if login else {}) as r:
                assert r.status == status, (login, r.status)
    await runner.cleanup()


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
        runner = web.AppRunner(bm_web.make_app(beeper, db, None, static, save))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 23396).start()
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
        await runner.cleanup()
        await fake_runner.cleanup()


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
