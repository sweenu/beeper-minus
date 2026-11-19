import urllib.parse
from datetime import datetime, timezone

import aiohttp


def describe(e):
    # Never repr() aiohttp errors: it includes the request headers, so the Beeper token.
    return f"{type(e).__name__}: {e}"


def ts(msg):
    return datetime.fromisoformat(msg["timestamp"])


class Beeper:
    """Minimal client for the Beeper Client API served by Beeper Server/Desktop."""

    def __init__(self, url, token=None):
        self.url = url.rstrip("/")
        self.mcp_url = f"{self.url}/v0/mcp"  # Beeper's own MCP
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        # The web proxy passes bodies through untouched, compressed or not.
        self.raw = aiohttp.ClientSession(auto_decompress=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=30))
        if token:
            self.set_token(token)

    def set_token(self, token):
        for session in (self.http, self.raw):
            session.headers["Authorization"] = f"Bearer {token}"

    async def call(self, method, path, body=None, **query):
        url = self.url + urllib.parse.quote(path)
        # Lists repeat their key (chatIDs=a&chatIDs=b), as Beeper's own client sends them.
        params = [(k, str(x).lower() if isinstance(x, bool) else str(x)) for k, v in query.items() if v is not None for x in (v if isinstance(v, list) else [v])]
        async with self.http.request(method, url, json=body, params=params) as r:
            r.raise_for_status()
            if r.content_type != "application/json":
                return None
            return await r.json()

    async def chat(self, chat_id):
        return await self.call("GET", f"/v1/chats/{chat_id}")

    async def send(self, chat_id, text):
        return await self.call("POST", f"/v1/chats/{chat_id}/messages", {"text": text})

    async def download(self, att_id):
        """Where to read an attachment from, once Beeper has it."""
        result = await self.call("POST", "/v1/assets/download", {"url": att_id})
        # A media file the network no longer has (old messages, often) comes back as {"error": ...}.
        if not result.get("srcURL"):
            raise RuntimeError(f"Beeper couldn't download the attachment: {result.get('error') or 'no reason given'}")
        return result["srcURL"]

    async def asset(self, att_id, max_bytes):
        """An attachment's content, or None if it is bigger than max_bytes."""
        src = await self.download(att_id)
        url = urllib.parse.urlparse(src)
        if url.scheme == "file":  # Beeper Desktop hands out paths
            with open(urllib.parse.unquote(url.path), "rb") as f:
                data = f.read(max_bytes + 1)
        else:
            data = b""
            async with self.http.get(src) as r:
                r.raise_for_status()
                async for chunk in r.content.iter_chunked(1 << 16):
                    data += chunk
                    if len(data) > max_bytes:
                        break
        return None if len(data) > max_bytes else data

    async def chats_since(self, since):
        """Chats with activity after `since`."""
        chats, cursor = [], {}
        while True:
            page = await self.call("GET", "/v1/chats/search", lastActivityAfter=since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), limit=200, **cursor)
            chats += page["items"]
            if not page.get("hasMore"):
                return chats
            cursor = {"cursor": page["oldestCursor"], "direction": "before"}

    async def messages_since(self, chat_id, since, limit=None):
        """A chat's messages after `since`, newest first; only the newest `limit` if given."""
        found, cursor = [], {}
        while True:
            page = await self.call("GET", f"/v1/chats/{chat_id}/messages", **cursor)
            found += [m for m in page["items"] if ts(m) > since]
            oldest = min((ts(m) for m in page["items"]), default=None)
            if not page.get("hasMore") or oldest is None or oldest <= since or (limit and len(found) >= limit):
                return sorted(found, key=ts, reverse=True)[:limit]
            cursor = {"cursor": page["oldestCursor"], "direction": "before"}

    async def close(self):
        await self.http.close()
        await self.raw.close()
