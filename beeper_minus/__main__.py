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
    b = cfg.get("beeper", {})
    beeper = Beeper(b.get("url", "http://127.0.0.1:23373"), Path(b["token_file"]).read_text().strip())
    state_dir = Path(cfg.get("state_dir") or os.environ.get("STATE_DIRECTORY") or ".")
    db = open_db(state_dir / "beeper-minus.db")

    t = cfg.get("transcription", {})
    transcriber = Transcriber(t, beeper, db) if t.get("engine") else None

    tasks = [scheduler.run(beeper, db)]
    if transcriber and t.get("auto", False):
        tasks.append(transcriber.run())
    w = cfg.get("web", {})
    if w.get("enable", True):
        static = os.environ.get("BEEPER_MINUS_STATIC") or w.get("static_dir") or Path(__file__).parent.parent / "web" / "dist"
        app = web.make_app(beeper, db, transcriber, static)
        await web.serve(app, w.get("host", "127.0.0.1"), w.get("port", 8099))
    await asyncio.gather(*tasks)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BEEPER_MINUS_CONFIG", "config.toml")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
