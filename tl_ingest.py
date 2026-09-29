"""Fixtures and results ingestion: every match FotMob lists for a date, worldwide, men's and women's."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from tl_db import connect
from tl_geo import confederation, gender

FOTMOB = "https://www.fotmob.com/api/data/matches?date={ymd}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36",
    "Accept": "application/json",
}
PICKS_DIR = Path(__file__).resolve().parent


def _status(s: dict) -> str:
    if s.get("cancelled"):
        return "Cancelled"
    if s.get("finished"):
        return (s.get("reason") or {}).get("short") or "FT"
    if s.get("started"):
        return "Live"
    return "NS"


def fetch_day(d: date) -> int:
    r = requests.get(FOTMOB.format(ymd=d.strftime("%Y%m%d")), headers=HEADERS, timeout=40)
    r.raise_for_status()
    data = r.json()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = connect()
    n = 0
    with con:
        for lg in data.get("leagues", []):
            name = lg.get("name") or ""
            if lg.get("isGroup") and lg.get("parentLeagueName"):
                name = f"{lg['parentLeagueName']}, Group {lg.get('groupName')}"
            cc = lg.get("ccode") or ""
            con.execute(
                "INSERT INTO competitions (id,name,country_code,confederation,gender,parent_id,rank) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                "country_code=excluded.country_code, confederation=excluded.confederation, "
                "gender=excluded.gender, parent_id=excluded.parent_id, rank=excluded.rank",
                (lg["id"], name, cc, confederation(cc, name), gender(name),
                 lg.get("parentLeagueId") or lg.get("primaryId"), lg.get("localRank")),
            )
            for m in lg.get("matches", []):
                s = m.get("status") or {}
                st = _status(s)
                played = s.get("started") or s.get("finished")
                con.execute(
                    "INSERT INTO matches (id,match_date,kickoff_utc,competition_id,home,away,home_id,away_id,"
                    "status,home_score,away_score,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET match_date=excluded.match_date, kickoff_utc=excluded.kickoff_utc, "
                    "status=excluded.status, home_score=excluded.home_score, away_score=excluded.away_score, "
                    "updated_at=excluded.updated_at",
                    (m["id"], d.isoformat(), s.get("utcTime"), lg["id"],
                     m["home"]["name"], m["away"]["name"], m["home"].get("id"), m["away"].get("id"), st,
                     m["home"].get("score") if played else None, m["away"].get("score") if played else None, now),
                )
                n += 1
        con.execute("INSERT INTO fetch_log VALUES (?,?,?) ON CONFLICT(match_date) DO UPDATE SET "
                    "fetched_at=excluded.fetched_at, n_matches=excluded.n_matches", (d.isoformat(), now, n))
    return n


def _max_age(d: date, today: date) -> timedelta:
    """How stale a day's data may get: finished days rarely change, today changes constantly."""
    if d < today - timedelta(days=2):
        return timedelta(hours=24)
    if abs((d - today).days) <= 1:
        return timedelta(minutes=10)
    return timedelta(hours=3)


def refresh(back: int = 7, ahead: int = 7, force: bool = False) -> list[str]:
    """Fetch every day in the window whose data is stale. Returns the dates fetched."""
    today = datetime.now(timezone.utc).date()
    con = connect()
    log = dict(con.execute("SELECT match_date, fetched_at FROM fetch_log").fetchall())
    fetched = []
    for off in range(-back, ahead + 1):
        d = today + timedelta(days=off)
        last = log.get(d.isoformat())
        if not force and last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < _max_age(d, today):
            continue
        try:
            fetch_day(d)
            fetched.append(d.isoformat())
        except requests.RequestException:
            continue
    load_picks()
    return fetched


def load_picks() -> int:
    """Load tipster-consensus editions (data/picks/eNNN.json) into tipster_picks."""
    con = connect()
    n = 0
    with con:
        for f in sorted(PICKS_DIR.glob("picks_e*.json")):
            ed = json.loads(f.read_text(encoding="utf-8"))
            for p in ed.get("topTen", []):
                con.execute(
                    "INSERT OR REPLACE INTO tipster_picks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ed["n"], p["market"], p["home"], p["away"], p.get("league"), p.get("conf"),
                     p.get("gender"), p.get("date"), p.get("time"), p["pick"], p["agree"], p["of"],
                     ", ".join(p.get("sources", [])), p.get("flag")),
                )
                n += 1
    return n


if __name__ == "__main__":
    print("fetched:", refresh(force=True))
