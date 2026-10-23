"""SQLite history of runs, so later runs can measure review growth and rating changes."""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL, finished_at REAL, cities TEXT, args TEXT, lead_count INTEGER
);
CREATE TABLE IF NOT EXISTS snapshots (
    place_id TEXT, observed_at REAL, run_id INTEGER,
    rating REAL, review_count INTEGER, business_status TEXT,
    PRIMARY KEY (place_id, observed_at)
);
CREATE TABLE IF NOT EXISTS places (
    place_id TEXT PRIMARY KEY, name TEXT, city TEXT, first_seen REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS lead_history (
    run_id INTEGER, place_id TEXT, score REAL, data TEXT,
    PRIMARY KEY (run_id, place_id)
);
"""

DAY = 86400.0


class LeadDB:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def start_run(self, cities, args):
        cur = self.db.execute("INSERT INTO runs (started_at, cities, args) VALUES (?, ?, ?)",
                              (time.time(), ",".join(cities), json.dumps(args, default=str)))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id, lead_count):
        self.db.execute("UPDATE runs SET finished_at = ?, lead_count = ? WHERE id = ?",
                        (time.time(), lead_count, run_id))
        self.db.commit()

    def record_snapshot(self, run_id, place_id, name, city, observed_at, rating, review_count, status):
        """Store what Google reported. observed_at is when the API response was fetched, so
        re-running against cached data doesn't create fake 'no growth' snapshots."""
        self.db.execute("INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
                        (place_id, observed_at, run_id, rating, review_count, status))
        self.db.execute(
            "INSERT INTO places VALUES (?, ?, ?, ?, ?) ON CONFLICT(place_id) DO UPDATE SET"
            " name = excluded.name, city = excluded.city, last_seen = excluded.last_seen",
            (place_id, name, city, observed_at, observed_at))
        self.db.commit()

    def growth(self, place_id, now_ts, review_count, rating, min_days=7, prefer_days=28):
        """Compare against an earlier snapshot: the latest one at least prefer_days old,
        else the oldest one at least min_days old. Returns dict or None."""
        rows = self.db.execute(
            "SELECT observed_at, review_count, rating FROM snapshots"
            " WHERE place_id = ? AND observed_at <= ? ORDER BY observed_at",
            (place_id, now_ts - min_days * DAY)).fetchall()
        if not rows:
            return None
        older = [r for r in rows if r[0] <= now_ts - prefer_days * DAY]
        then_ts, then_count, then_rating = older[-1] if older else rows[0]
        months = (now_ts - then_ts) / (30.44 * DAY)
        velocity = None
        if review_count is not None and then_count is not None and months > 0:
            velocity = round((review_count - then_count) / months, 2)
        change = None
        if rating is not None and then_rating is not None:
            change = round(rating - then_rating, 2)
        return {"velocity": velocity, "rating_change": change, "since": then_ts,
                "months": round(months, 1), "reviews_then": then_count}

    def save_lead(self, run_id, place_id, score, data):
        self.db.execute("INSERT OR REPLACE INTO lead_history VALUES (?, ?, ?, ?)",
                        (run_id, place_id, score, json.dumps(data, default=str)))

    def commit(self):
        self.db.commit()

    def recent_runs(self, n=20):
        rows = self.db.execute(
            "SELECT id, started_at, finished_at, cities, args, lead_count FROM runs"
            " ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [{"id": r[0], "started_at": r[1], "finished_at": r[2], "cities": r[3],
                 "args": json.loads(r[4] or "{}"), "lead_count": r[5]} for r in rows]

    def latest_finished_run(self):
        row = self.db.execute("SELECT MAX(id) FROM runs WHERE finished_at IS NOT NULL").fetchone()
        return row[0] if row else None

    def leads_for_run(self, run_id):
        rows = self.db.execute("SELECT data FROM lead_history WHERE run_id = ? ORDER BY score DESC",
                               (run_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self):
        self.db.close()
