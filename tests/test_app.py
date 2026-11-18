"""End-to-end check against the fake Beeper: python -m tests.test_app (or pytest)."""

import asyncio
import os
import stat
import tempfile
from pathlib import Path

from aiohttp import ClientSession, web

from beeper_minus import scheduler
from beeper_minus import web as bm_web
from beeper_minus.beeper import Beeper
from beeper_minus.db import open_db, rows
from beeper_minus.transcribe import Transcriber

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
    cfg = {"engine": "whisper-cpp", "whisper-cpp": {"model": "x", "command": str(whisper), "languages": ["fr", "en"]}, "notes_to_self_chat_id": fake_beeper.NOTE}
    transcriber = Transcriber(cfg, beeper, db)
    static = tmp / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>hi</h1>")
    runner = web.AppRunner(bm_web.make_app(beeper, db, transcriber, static))
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
        for bad in ("/api/beeper/v1/app/setup", "/api/beeper/v1/chats/../app/setup", "/api/beeper/v1/chats/%2e%2e/app"):
            async with http.get(api + bad) as r:
                assert r.status == 403, (bad, r.status)
        async with http.get(f"{api}/") as r:
            assert "hi" in await r.text()

        # Snooze archives now and unarchives once due.
        async with http.post(f"{api}/api/snoozes", json={"chatID": bob, "until": 1}) as r:
            assert r.status == 200
        assert chats[bob]["isArchived"]
        assert rows(db, "select chat_title from snoozes") == [{"chat_title": "Bob Martin"}]
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

        # On-demand transcription from the web app, posted to Note to self too.
        async with http.post(f"{api}/api/transcriptions", json={"chatID": alice, "messageID": "a3"}) as r:
            t = await r.json()
            assert r.status == 200, t
        assert t["text"] == "Bonjour, ça va ?" and t["language"] == "fr" and t["duration"] == 3, t
        assert "Bonjour, ça va ?" in messages[fake_beeper.NOTE][-1]["text"]
        async with http.post(f"{api}/api/transcriptions", json={"chatID": alice, "messageID": "a1"}) as r:
            assert r.status == 400  # no audio

    # Automatic: a voice note arriving over the websocket gets transcribed once.
    listener = asyncio.create_task(transcriber.run())
    for _ in range(50):
        await asyncio.sleep(0.1)
        if fake["sockets"] and transcriber.queue.empty() and not transcriber.busy:
            break
    notes = len(messages[fake_beeper.NOTE])
    await fake_beeper.push(fake, fake_beeper.voice("f2", "!family:beeper.com", 0, sender="Mum"))
    await fake_beeper.push(fake, fake_beeper.voice("f3", "!family:beeper.com", 0, sender="Me", is_sender=True))
    await fake_beeper.push(fake, fake_beeper.voice("o2", "!old:beeper.com", 0, sender="Carol"))  # archived chat
    await fake_beeper.push(fake, fake_beeper.voice("a3", alice, 6))  # already transcribed
    for _ in range(50):
        await asyncio.sleep(0.1)
        if len(messages[fake_beeper.NOTE]) > notes and transcriber.queue.empty() and not transcriber.busy:
            break
    assert len(messages[fake_beeper.NOTE]) == notes + 1, messages[fake_beeper.NOTE][notes:]
    assert "**Mum** in *Family 🏡*" in messages[fake_beeper.NOTE][-1]["text"]
    listener.cancel()
    await beeper.close()
    await runner.cleanup()
    await fake_runner.cleanup()
    print("ok")


def test_end_to_end():
    asyncio.run(main())


if __name__ == "__main__":
    test_end_to_end()
