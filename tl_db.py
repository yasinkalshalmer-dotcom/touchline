"""SQLite store. The schema mirrors the planned PostgreSQL design so it can move over later."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "touchline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS competitions (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    country_code  TEXT,
    confederation TEXT,
    gender        TEXT,
    parent_id     INTEGER,
    rank          INTEGER
);
CREATE TABLE IF NOT EXISTS matches (
    id             INTEGER PRIMARY KEY,
    match_date     TEXT NOT NULL,
    kickoff_utc    TEXT,
    competition_id INTEGER REFERENCES competitions(id),
    home           TEXT,
    away           TEXT,
    home_id        INTEGER,
    away_id        INTEGER,
    status         TEXT,
    home_score     INTEGER,
    away_score     INTEGER,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_matches_date ON matches(match_date);
CREATE TABLE IF NOT EXISTS fetch_log (
    match_date TEXT PRIMARY KEY,
    fetched_at TEXT,
    n_matches  INTEGER
);
CREATE TABLE IF NOT EXISTS tipster_picks (
    edition    INTEGER,
    market     TEXT,
    home       TEXT,
    away       TEXT,
    league     TEXT,
    confederation TEXT,
    gender     TEXT,
    match_date TEXT,
    kickoff_utc TEXT,
    pick       TEXT,
    agree      INTEGER,
    checked    INTEGER,
    sources    TEXT,
    flag       TEXT,
    PRIMARY KEY (edition, market, home, away, pick)
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.executescript(SCHEMA)
    return con
