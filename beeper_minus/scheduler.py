import asyncio
import logging

import aiohttp

from .beeper import describe
from .db import now_ms, rows

log = logging.getLogger(__name__)


def permanent(e):
    # A 4xx won't fix itself by retrying; anything else (Beeper down, 5xx, signed out, rate limited) is retried next tick.
    return isinstance(e, aiohttp.ClientResponseError) and e.status < 500 and e.status not in (401, 403, 408, 429)


async def tick(beeper, db):
    now = now_ms()
    for s in rows(db, "select * from snoozes where until <= ?", now):
        try:
            await beeper.archive(s["chat_id"], False)
        except Exception as e:
            log.warning("unsnooze %s: %s", s["chat_id"], describe(e))
            if not permanent(e):
                continue
        db.execute("delete from snoozes where chat_id = ?", (s["chat_id"],))
        log.info("unsnoozed %s", s["chat_id"])
    for m in rows(db, "select * from scheduled where send_at <= ? and error is null order by send_at", now):
        try:
            await beeper.send(m["chat_id"], m["text"])
        except Exception as e:
            log.warning("send %s to %s: %s", m["id"], m["chat_id"], describe(e))
            if permanent(e):
                # Keep the message so it isn't lost: the web app shows the error and lets you edit or delete it.
                db.execute("update scheduled set error = ? where id = ?", (f"{e.status} {e.message}", m["id"]))
            continue
        db.execute("delete from scheduled where id = ?", (m["id"],))
        log.info("sent scheduled message %s to %s", m["id"], m["chat_id"])


async def run(beeper, db, interval=15):
    while True:
        try:
            await tick(beeper, db)
        except Exception as e:
            log.exception("scheduler: %s", describe(e))
        await asyncio.sleep(interval)
