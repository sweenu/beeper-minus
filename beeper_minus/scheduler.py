import asyncio
import logging

import aiohttp

from .beeper import describe
from .db import now_ms, rows

log = logging.getLogger(__name__)


def permanent(e):
    # A 4xx won't fix itself by retrying; anything else (Beeper down, 5xx, signed out, rate limited) is retried next tick.
    return isinstance(e, aiohttp.ClientResponseError) and e.status < 500 and e.status not in (401, 403, 408, 429)


async def add(beeper, db, chat_id, text, send_at):
    chat = await beeper.chat(chat_id)
    cur = db.execute(
        "insert into scheduled (chat_id, chat_title, network, text, send_at, created_at) values (?, ?, ?, ?, ?, ?)",
        (chat_id, chat.get("title"), chat.get("network"), text, send_at, now_ms()),
    )
    return rows(db, "select * from scheduled where id = ?", cur.lastrowid)[0]


def edit(db, id, text=None, send_at=None):
    """Returns the updated row, or None if there is no such message."""
    sets = {k: v for k, v in (("text", text), ("send_at", send_at)) if v is not None}
    # Editing is how a failed message gets retried.
    cur = db.execute(f"update scheduled set {''.join(f'{k} = ?, ' for k in sets)}error = null where id = ?", (*sets.values(), id))
    return cur.rowcount and rows(db, "select * from scheduled where id = ?", id)[0] or None


async def tick(beeper, db):
    now = now_ms()
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
