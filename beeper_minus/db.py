import sqlite3
import time

SCHEMA = """
create table if not exists transcriptions (
  chat_id text not null, message_id text not null,
  -- The same voice note gets a new chat/message ID when a network moves between cloud
  -- and on-device, but keeps its sender and send time.
  alias text, chat_title text, network text, sender text, sent_at integer,
  language text, duration integer, text text, created_at integer,
  primary key (chat_id, message_id)
);
create index if not exists transcriptions_alias on transcriptions (alias);
create index if not exists transcriptions_created on transcriptions (created_at);
create table if not exists snoozes (
  chat_id text primary key, chat_title text, network text, until integer not null, created_at integer
);
create table if not exists scheduled (
  id integer primary key, chat_id text not null, chat_title text, network text,
  text text not null, send_at integer not null, error text, created_at integer
);
create table if not exists kv (key text primary key, value text);
"""


def now_ms():
    return int(time.time() * 1000)


def open_db(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def rows(db, sql, *args):
    return [dict(r) for r in db.execute(sql, args)]


def get_kv(db, key):
    row = db.execute("select value from kv where key = ?", (key,)).fetchone()
    return row and row[0]


def set_kv(db, key, value):
    if value is None:
        db.execute("delete from kv where key = ?", (key,))
    else:
        db.execute("insert or replace into kv values (?, ?)", (key, value))
