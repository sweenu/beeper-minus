import asyncio
import logging
import os
import sys
import tomllib
from pathlib import Path

from . import scheduler, web
from .beeper import Beeper
from .db import open_db
from .transcribe import Transcriber


async def run(cfg):
    b, w = cfg.get("beeper", {}), cfg.get("web", {})
    state_dir = Path(cfg.get("state_dir") or os.environ.get("STATE_DIRECTORY") or ".")
    db = open_db(state_dir / "beeper-minus.db")
    # Without a configured token, the web app's setup screen signs Beeper Server in and saves one here.
    saved = state_dir / "beeper-token"
    token = Path(b["token_file"]).read_text().strip() if b.get("token_file") else saved.exists() and saved.read_text().strip()
    if not token and not w.get("enable", True):
        sys.exit("No Beeper token: set beeper.token_file, or enable the web app to sign in from its setup screen.")
    beeper = Beeper(b.get("url", "http://127.0.0.1:23373"), token)
    signed_in = asyncio.Event()
    if token:
        signed_in.set()
    else:
        logging.warning("no Beeper token yet: send later and transcription wait for the web app's setup screen")

    def save_token(token):
        fd = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        beeper.set_token(token)
        signed_in.set()

    async def after_sign_in(task):
        await signed_in.wait()
        await task

    t = cfg.get("transcription", {})
    transcriber = Transcriber(t, beeper, db) if t.get("engine") else None

    tasks = [after_sign_in(scheduler.run(beeper, db))]
    if transcriber and t.get("auto", False):
        tasks.append(after_sign_in(transcriber.run()))
    if w.get("enable", True):
        static = os.environ.get("BEEPER_MINUS_STATIC") or Path(__file__).parent.parent / "web" / "dist"
        host = w.get("host", "127.0.0.1")
        hosts = {h.lower() for h in ("localhost", "127.0.0.1", "::1", host, *w.get("allowed_hosts", []))}
        users = {u.lower() for u in w.get("allowed_users", [])}
        app = web.make_app(beeper, db, transcriber, static, save_token, hosts, users)
        await web.serve(app, host, w.get("port", 8099))
    await asyncio.gather(*tasks)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BEEPER_MINUS_CONFIG", "config.toml")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
