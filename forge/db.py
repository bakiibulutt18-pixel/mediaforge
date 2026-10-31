"""Kanal başına SQLite durum deposu: işler, yayınlar, kaynaklar, maliyet, metrik, olay, kota."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  topic TEXT,
  status TEXT NOT NULL DEFAULT 'new',   -- new|running|ready|queued|published|failed|rejected
  attempts INTEGER NOT NULL DEFAULT 0,
  quality REAL,
  error TEXT,
  due_ts INTEGER,
  payload TEXT NOT NULL DEFAULT '{}',
  created_ts INTEGER NOT NULL,
  updated_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER,
  platform TEXT NOT NULL,
  external_id TEXT,
  url TEXT,
  title TEXT,
  kind TEXT,
  published_ts INTEGER,
  scheduled_ts INTEGER,
  status TEXT NOT NULL DEFAULT 'published'
);
CREATE TABLE IF NOT EXISTS metrics (
  post_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  item_id INTEGER NOT NULL,
  url TEXT,
  title TEXT,
  published TEXT,
  accessed TEXT,
  reliability REAL,
  reliability_label TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS claims (
  item_id INTEGER NOT NULL,
  claim TEXT,
  source_ids TEXT,
  status TEXT            -- dogrulandi|tek_kaynak|celiski|dogrulanamadi
);
CREATE TABLE IF NOT EXISTS costs (
  ts INTEGER NOT NULL,
  item_id INTEGER,
  category TEXT NOT NULL,
  usd REAL NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS events (
  ts INTEGER NOT NULL,
  level TEXT NOT NULL,
  msg TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quota (
  key TEXT NOT NULL,
  period TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (key, period)
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
"""


def now() -> int:
    return int(time.time())


def month_key(ts: int | None = None) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts or now()))


def day_key(ts: int | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts or now()))


class DB:
    def __init__(self, channel: str, path=None):
        self.channel = channel
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or (config.STATE_DIR / f"{channel}.db")
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.commit()
        self.conn.close()

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- items ---------------------------------------------------------
    def new_item(self, kind: str, topic: str = "", payload: dict | None = None, status="new", due_ts=None) -> int:
        t = now()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO items(kind,topic,status,due_ts,payload,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?)",
                (kind, topic, status, due_ts, json.dumps(payload or {}, ensure_ascii=False), t, t),
            )
            return cur.lastrowid

    def get_item(self, item_id: int) -> dict:
        r = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        return d

    def update_item(self, item_id: int, *, payload_update: dict | None = None, **fields):
        sets, vals = [], []
        if payload_update is not None:
            cur = self.get_item(item_id)["payload"]
            cur.update(payload_update)
            sets.append("payload=?")
            vals.append(json.dumps(cur, ensure_ascii=False))
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(v)
        sets.append("updated_ts=?")
        vals.append(now())
        vals.append(item_id)
        with self.tx() as c:
            c.execute(f"UPDATE items SET {','.join(sets)} WHERE id=?", vals)

    def items(self, status: str | None = None, kind: str | None = None, limit: int = 200) -> list[dict]:
        q, args = "SELECT * FROM items WHERE 1=1", []
        if status:
            q += " AND status=?"
            args.append(status)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(q, args):
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def due_items(self, kind: str, ts: int | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM items WHERE kind=? AND status='queued' AND due_ts<=? ORDER BY due_ts", (kind, ts or now())
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def recent_topics(self, limit: int = 80) -> list[str]:
        rows = self.conn.execute(
            "SELECT topic FROM items WHERE topic IS NOT NULL AND topic!='' AND status IN "
            "('new','running','ready','queued','published') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["topic"] for r in rows]

    def count_since(self, kind: str, since_ts: int, statuses=("published", "ready", "queued", "running")) -> int:
        marks = ",".join("?" * len(statuses))
        r = self.conn.execute(
            f"SELECT COUNT(*) n FROM items WHERE kind=? AND created_ts>=? AND status IN ({marks})",
            (kind, since_ts, *statuses),
        ).fetchone()
        return r["n"]

    # -- posts / metrics -----------------------------------------------
    def add_post(self, *, item_id, platform, external_id, url, title, kind, published_ts=None, scheduled_ts=None, status="published") -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO posts(item_id,platform,external_id,url,title,kind,published_ts,scheduled_ts,status) VALUES(?,?,?,?,?,?,?,?,?)",
                (item_id, platform, external_id, url, title, kind, published_ts or now(), scheduled_ts, status),
            )
            return cur.lastrowid

    def posts(self, limit: int = 300) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,))]

    def add_metric(self, post_id: int, data: dict):
        with self.tx() as c:
            c.execute("INSERT INTO metrics(post_id,ts,data) VALUES(?,?,?)", (post_id, now(), json.dumps(data)))

    def latest_metrics(self) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for r in self.conn.execute("SELECT post_id,ts,data FROM metrics ORDER BY ts"):
            out[r["post_id"]] = {"ts": r["ts"], **json.loads(r["data"])}
        return out

    # -- sources / claims ----------------------------------------------
    def add_sources(self, item_id: int, sources: list[dict]):
        with self.tx() as c:
            c.execute("DELETE FROM sources WHERE item_id=?", (item_id,))
            for s in sources:
                c.execute(
                    "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)",
                    (item_id, s.get("url"), s.get("title"), s.get("published"), s.get("accessed"),
                     s.get("reliability"), s.get("reliability_label"), s.get("note")),
                )

    def add_claims(self, item_id: int, claims: list[dict]):
        with self.tx() as c:
            c.execute("DELETE FROM claims WHERE item_id=?", (item_id,))
            for cl in claims:
                c.execute(
                    "INSERT INTO claims VALUES(?,?,?,?)",
                    (item_id, cl.get("claim"), json.dumps(cl.get("source_ids", [])), cl.get("status")),
                )

    # -- cost / budget -------------------------------------------------
    def add_cost(self, category: str, usd: float, item_id: int | None = None, note: str = ""):
        with self.tx() as c:
            c.execute("INSERT INTO costs VALUES(?,?,?,?,?)", (now(), item_id, category, float(usd), note))

    def month_cost(self) -> float:
        start = int(time.mktime(time.strptime(month_key() + "-01", "%Y-%m-%d")))
        r = self.conn.execute("SELECT COALESCE(SUM(usd),0) s FROM costs WHERE ts>=?", (start,)).fetchone()
        return float(r["s"])

    def item_cost(self, item_id: int) -> float:
        r = self.conn.execute("SELECT COALESCE(SUM(usd),0) s FROM costs WHERE item_id=?", (item_id,)).fetchone()
        return float(r["s"])

    # -- events --------------------------------------------------------
    def log(self, level: str, msg: str):
        print(f"[{self.channel}] {level.upper()}: {msg}", flush=True)
        if level in ("warn", "error") and os.environ.get("GITHUB_ACTIONS"):
            print(f"::{'error' if level == 'error' else 'warning'}::[{self.channel}] {msg[:300]}", flush=True)
        with self.tx() as c:
            c.execute("INSERT INTO events VALUES(?,?,?)", (now(), level, msg[:2000]))

    def events(self, limit: int = 100, level: str | None = None) -> list[dict]:
        if level:
            rows = self.conn.execute("SELECT * FROM events WHERE level=? ORDER BY ts DESC LIMIT ?", (level, limit))
        else:
            rows = self.conn.execute("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # -- quota ---------------------------------------------------------
    def quota_used(self, key: str, period: str) -> int:
        r = self.conn.execute("SELECT used FROM quota WHERE key=? AND period=?", (key, period)).fetchone()
        return r["used"] if r else 0

    def quota_add(self, key: str, period: str, n: int = 1):
        with self.tx() as c:
            c.execute(
                "INSERT INTO quota(key,period,used) VALUES(?,?,?) ON CONFLICT(key,period) DO UPDATE SET used=used+?",
                (key, period, n, n),
            )
