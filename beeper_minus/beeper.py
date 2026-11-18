import urllib.parse

import aiohttp


def describe(e):
    # Never repr() aiohttp errors: it includes the request headers, so the Beeper token.
    return f"{type(e).__name__}: {e}"


class Beeper:
    """Minimal client for the Beeper Client API served by Beeper Server/Desktop."""

    def __init__(self, url, token=None):
        self.url = url.rstrip("/")
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
        params = [(k, str(v).lower() if isinstance(v, bool) else str(v)) for k, v in query.items() if v is not None]
        async with self.http.request(method, url, json=body, params=params) as r:
            r.raise_for_status()
            if r.content_type != "application/json":
                return None
            return await r.json()

    async def chat(self, chat_id):
        return await self.call("GET", f"/v1/chats/{chat_id}")

    async def send(self, chat_id, text):
        return await self.call("POST", f"/v1/chats/{chat_id}/messages", {"text": text})

    async def close(self):
        await self.http.close()
        await self.raw.close()
