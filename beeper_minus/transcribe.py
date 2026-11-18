import asyncio
import json
import logging
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from .db import get_kv, now_ms, rows, set_kv

log = logging.getLogger(__name__)


async def run(*cmd):
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await p.communicate()
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} exited with {p.returncode}: {err.decode(errors='replace')[-500:]}")


def whisper_cpp(cfg):
    model = cfg["model"]
    languages = cfg.get("languages") or []

    async def whisper(wav, lang):
        with tempfile.TemporaryDirectory() as d:
            await run(cfg.get("command", "whisper-cli"), "-m", model, "-l", lang, "-np", "-oj", "-of", f"{d}/out", "-f", wav)
            out = json.loads(Path(f"{d}/out.json").read_text(errors="replace"))
        return out["result"]["language"], "".join(s["text"] for s in out["transcription"]).strip()

    async def transcribe(wav):
        lang, text = await whisper(wav, "auto")
        if languages and lang not in languages:
            # Short clips sometimes get detected as another language.
            lang, text = await whisper(wav, languages[0])
        return lang, text

    return transcribe


def openai(cfg):
    """Any OpenAI-compatible /audio/transcriptions endpoint (OpenAI, Groq, a local server...)."""
    url = cfg.get("url", "https://api.openai.com/v1/audio/transcriptions")
    key = Path(cfg["api_key_file"]).read_text().strip() if cfg.get("api_key_file") else None
    language = cfg.get("language")

    async def transcribe(wav):
        form = aiohttp.FormData()
        form.add_field("model", cfg.get("model", "whisper-1"))
        if language:
            form.add_field("language", language)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        # shortcut: uploads 16 kHz wav (~2 MB/min), so OpenAI's 25 MB cap is ~13 min; encode to opus if longer notes matter.
        with open(wav, "rb") as f:
            form.add_field("file", f, filename="audio.wav", content_type="audio/wav")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as http:
                async with http.post(url, data=form, headers=headers) as r:
                    if r.status >= 400:
                        raise RuntimeError(f"{url}: {r.status} {(await r.text())[:500]}")
                    out = await r.json()
        return out.get("language") or language, out["text"].strip()

    return transcribe


ENGINES = {"whisper-cpp": whisper_cpp, "openai": openai}


def audio_attachment(msg):
    return next((a for a in msg.get("attachments") or [] if a.get("type") == "audio" or a.get("isVoiceNote")), None)


def format_note(row, chat):
    who = f"**{row['sender'] or '?'}**"
    if chat.get("type") == "group":
        who += f" in *{chat.get('title', '?')}*"
    sent = datetime.fromtimestamp(row["sent_at"] / 1000).astimezone().strftime("%a %H:%M")
    secs = row["duration"]
    quoted = "\n".join(f"> {line}" for line in (row["text"] or "(silence)").splitlines())
    return f"🎙 {who} · {chat.get('network', '?')} · {sent} · {secs // 60}:{secs % 60:02} · {row['language']}\n{quoted}"


def ts(msg):
    return datetime.fromisoformat(msg["timestamp"])


def alias(msg):
    # A voice note keeps its sender and send time when it gets new IDs (see db.py).
    return f"{int(ts(msg).timestamp())}/{''.join((msg.get('senderName') or '?').split())}"


class Transcriber:
    def __init__(self, cfg, beeper, db):
        self.beeper = beeper
        self.db = db
        self.engine = ENGINES[cfg["engine"]](cfg.get(cfg["engine"], {}))
        self.note_chat_id = cfg.get("notes_to_self_chat_id")
        # Never auto-transcribe older messages: linking an account backfills its history,
        # and the live feed reports every old message as new.
        self.max_age = timedelta(days=cfg.get("max_age_days", 3))
        self.lock = asyncio.Lock()  # one transcription at a time: the engine may hog a GPU
        self.queue = asyncio.Queue()
        self.busy = False

    async def fetch(self, src, dest):
        url = urllib.parse.urlparse(src)
        if url.scheme == "file":
            return urllib.parse.unquote(url.path)
        # Beeper Server proxies media over its own HTTP API instead of handing out paths.
        async with self.beeper.http.get(src) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in r.content.iter_chunked(1 << 16):
                    f.write(chunk)
        return dest

    async def transcribe(self, msg, chat=None):
        """Transcribe a message's audio, store it, and post it to Note to self if configured."""
        att = audio_attachment(msg)
        if att is None:
            raise ValueError("message has no audio")
        src = (await self.beeper.call("POST", "/v1/assets/download", {"url": att["id"]}))["srcURL"]
        async with self.lock:
            # The worker and the web app can both ask for the same note: do it once.
            done = rows(self.db, "select * from transcriptions where chat_id = ? and message_id = ?", msg["chatID"], msg["id"])
            if done:
                return done[0]
            with tempfile.TemporaryDirectory() as d:
                path = await self.fetch(src, f"{d}/media")
                wav = f"{d}/audio.wav"
                # whisper-cli can't read m4a/caf (Signal, iMessage), so always decode.
                await run("ffmpeg", "-loglevel", "error", "-i", path, "-ar", "16000", "-ac", "1", wav)
                # Bridges rarely fill in the attachment duration; 16 kHz mono s16 is 32000 B/s.
                secs = round(Path(wav).stat().st_size / 32000)
                lang, text = await self.engine(wav)
        chat = chat or await self.beeper.chat(msg["chatID"])
        sent_at = int(ts(msg).timestamp() * 1000)
        row = {
            "chat_id": msg["chatID"],
            "message_id": msg["id"],
            "alias": alias(msg),
            "chat_title": chat.get("title"),
            "network": chat.get("network"),
            "sender": msg.get("senderName"),
            "sent_at": sent_at,
            "language": lang,
            "duration": secs,
            "text": text,
            "created_at": now_ms(),
        }
        # Post first: if it fails, no row is stored and the note is retried instead of never posted.
        if self.note_chat_id:
            await self.beeper.send(self.note_chat_id, format_note(row, chat))
        self.db.execute(f"insert or replace into transcriptions ({', '.join(row)}) values ({', '.join('?' * len(row))})", tuple(row.values()))
        log.info("transcribed %s/%s (%s)", row["chat_id"], row["message_id"], lang)
        return row

    # --- automatic transcription of incoming voice notes ---

    def wanted(self, msg):
        if msg.get("isSender") or msg.get("isDeleted") or ts(msg) < datetime.now(timezone.utc) - self.max_age:
            return False
        att = audio_attachment(msg)
        # An existing transcript means Beeper already did the job.
        if att is None or not (att.get("isVoiceNote") or msg.get("type") == "VOICE") or att.get("transcription"):
            return False
        return not rows(self.db, "select 1 from transcriptions where (chat_id = ? and message_id = ?) or alias = ?", msg["chatID"], msg["id"], alias(msg))

    def mark_retry(self, msg):
        # "retry" holds the oldest failed message, so the next catch-up reaches back to it.
        t = ts(msg) - timedelta(seconds=1)
        old = get_kv(self.db, "retry")
        if old is None or t < datetime.fromisoformat(old):
            set_kv(self.db, "retry", t.isoformat())

    def catch_up_since(self):
        now = datetime.now(timezone.utc)
        times = [datetime.fromisoformat(t) for t in (get_kv(self.db, "alive"), get_kv(self.db, "retry")) if t]
        # First run: no backlog, so switching from another transcriber doesn't repost old notes.
        return max(now - self.max_age, min(times)) if times else now

    async def catch_up(self, since):
        set_kv(self.db, "retry", None)  # failures from here on re-add it
        chats, cursor = [], {}
        while True:
            page = await self.beeper.call("GET", "/v1/chats/search", lastActivityAfter=since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), limit=200, **cursor)
            chats += page["items"]
            if not page.get("hasMore"):
                break
            cursor = {"cursor": page["oldestCursor"], "direction": "before"}
        found = []
        for chat in chats:
            cursor = {}
            while True:
                page = await self.beeper.call("GET", f"/v1/chats/{chat['id']}/messages", **cursor)
                found += [m for m in page["items"] if ts(m) > since]
                oldest = min((ts(m) for m in page["items"]), default=None)
                if not page.get("hasMore") or oldest is None or oldest <= since:
                    break
                cursor = {"cursor": page["oldestCursor"], "direction": "before"}
        for msg in sorted(found, key=ts):
            self.queue.put_nowait(msg)
        log.info("catch-up since %s: scanned %d chats", f"{since:%Y-%m-%d %H:%M}", len(chats))

    async def worker(self):
        while True:
            self.busy = False
            msg = await self.queue.get()
            self.busy = True
            try:
                if not self.wanted(msg):
                    continue
                chat = await self.beeper.chat(msg["chatID"])
                # Archived chats are ignored, unless they're only snoozed.
                if chat.get("isArchived") and not rows(self.db, "select 1 from snoozes where chat_id = ?", msg["chatID"]):
                    continue
                await self.transcribe(msg, chat)
            except Exception as e:
                log.warning("failed %s/%s: %r", msg.get("chatID"), msg.get("id"), e)
                try:
                    self.mark_retry(msg)
                except (KeyError, ValueError):
                    pass  # no usable timestamp: nothing to reach back to

    async def listen(self):
        since = self.catch_up_since()  # read before connecting: every event below moves "alive" forward
        url = self.beeper.url.replace("http", "ws", 1) + "/v1/ws"
        async with self.beeper.http.ws_connect(url, max_msg_size=0, heartbeat=30) as ws:
            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    break
                event = json.loads(raw.data)
                if event["type"] == "ready":
                    await ws.send_json({"type": "subscriptions.set", "chatIDs": ["*"]})
                elif event["type"] == "subscriptions.updated":
                    # Delivery is at-most-once with no replay, so reconcile over HTTP once subscribed.
                    await self.catch_up(since)
                elif event["type"] == "message.upserted":
                    for msg in event.get("entries") or []:
                        self.queue.put_nowait(msg)
                # Only move forward once everything older is handled, so a stop mid-queue is retried.
                if self.queue.empty() and not self.busy:
                    set_kv(self.db, "alive", datetime.now(timezone.utc).isoformat())

    async def run(self):
        self.worker_task = asyncio.create_task(self.worker())
        while True:
            try:
                await self.listen()
            except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("websocket: %r, reconnecting", e)
            except Exception:
                # A malformed event or page must not take the scheduler and web app down with it.
                log.exception("websocket")
            await asyncio.sleep(10)
