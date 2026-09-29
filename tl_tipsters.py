"""Tipster ingestion: reads public prediction tables, normalises every tip to (market, pick),
matches it to a real fixture and counts how many independent sources agree."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from tl_picks import norm

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/128.0 Safari/537.36", "Accept-Language": "en"}
TIMEOUT = 20

SOURCES = {
    "statarea.com": "https://www.statarea.com/predictions",
    "zulubet.com": "https://www.zulubet.com/",
    "soccervital.com": "https://www.soccervital.com/",
    "prosoccer.gr": "https://www.prosoccer.gr/en/football/predictions/",
    "bettingclosed.com": "https://www.bettingclosed.com/predictions/date-matches/today/bet-type/1x2",
}


def _get(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return BeautifulSoup(r.text, "lxml")
    except requests.RequestException:
        return None


def _result_tip(tip: str):
    """Map a 1X2-style tip to (market, pick)."""
    t = re.sub(r"[^12xX]", "", tip or "").upper()
    return {
        "1": ("Match result", "Home win"), "X": ("Match result", "Draw"), "2": ("Match result", "Away win"),
        "1X": ("Double chance", "Home or draw"), "X2": ("Double chance", "Draw or away"),
        "12": ("Double chance", "Home or away"),
    }.get(t)


def _split_teams(text: str):
    parts = re.split(r"\s+[-–]\s+|\s+vs?\s+", text.replace("\xa0", " ").strip(), maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (None, None)


def _rec(src, d, home, away, market_pick):
    if not (home and away and market_pick):
        return None
    return {"source": src, "date": d, "home": home, "away": away, "market": market_pick[0], "pick": market_pick[1]}


# ---------------- per-site parsers ----------------
def statarea(d: date) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    s = _get("https://www.statarea.com/predictions" if d == today
             else f"https://www.statarea.com/predictions/date/{d.isoformat()}/competition")
    out = []
    if not s:
        return out
    for m in s.select("div.match"):
        tip = m.select_one(".tip .value")
        h, a = m.select_one(".hostteam .name"), m.select_one(".guestteam .name")
        hdr = m.select_one(".teams .ownheader")
        md = re.search(r"\d{4}-\d{2}-\d{2}", hdr.get_text() if hdr else "")
        r = _rec("statarea.com", md.group(0) if md else d.isoformat(),
                 h and h.get_text(strip=True), a and a.get_text(strip=True), _result_tip(tip and tip.get_text(strip=True)))
        if r:
            out.append(r)
    return out


def zulubet(d: date) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    s = _get("https://www.zulubet.com/" if d == today else f"https://www.zulubet.com/tips-{d:%d-%m-%Y}.html")
    out = []
    if not s:
        return out
    for tr in s.select("table.content_table tr"):
        td = tr.find_all("td", recursive=False)
        if len(td) < 8:
            continue
        home, away = _split_teams(td[1].get_text(" ", strip=True))
        dm = re.match(r"(\d{2})-(\d{2})", td[0].get_text(strip=True))
        md = f"{d.year}-{dm.group(2)}-{dm.group(1)}" if dm else d.isoformat()
        r = _rec("zulubet.com", md, home, away, _result_tip(td[6].get_text(strip=True)))
        if r:
            out.append(r)
    return out


def soccervital(d: date) -> list[dict]:
    if d != datetime.now(timezone.utc).date():
        return []  # the site only publishes the current day
    s = _get("https://www.soccervital.com/")
    out = []
    if not s:
        return out
    for tr in s.find_all("tr"):
        td = tr.find_all("td")
        if len(td) < 9 or not re.match(r"\d\d:\d\d", td[0].get_text(strip=True)):
            continue
        home, away = td[1].get_text(" ", strip=True), td[2].get_text(" ", strip=True)
        for mp in (_result_tip(td[6].get_text(strip=True)),
                   {"O": ("Over/Under goals", "Over 2.5"), "U": ("Over/Under goals", "Under 2.5")}.get(td[7].get_text(strip=True).upper()),
                   (("Correct score", td[8].get_text(strip=True).replace(":", "-"))
                    if re.match(r"^\d+:\d+$", td[8].get_text(strip=True)) else None)):
            r = _rec("soccervital.com", d.isoformat(), home, away, mp)
            if r:
                out.append(r)
    return out


def prosoccer(d: date) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    if d == today:
        url = "https://www.prosoccer.gr/en/football/predictions/"
    elif d == today + timedelta(days=1):
        url = "https://www.prosoccer.gr/en/football/predictions/tomorrow.html"
    else:
        return []
    s = _get(url)
    out = []
    if not s:
        return out
    for tr in s.find_all("tr"):
        td = tr.find_all("td", recursive=False)
        if len(td) < 12:
            continue
        home, away = _split_teams(td[2].get_text(" ", strip=True).title())
        score = td[10].get_text(strip=True)
        for mp in (_result_tip(td[6].get_text(strip=True)),
                   ("Correct score", score) if re.match(r"^\d+-\d+$", score) else None):
            r = _rec("prosoccer.gr", d.isoformat(), home, away, mp)
            if r:
                out.append(r)
    return out


def bettingclosed(d: date) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    key = {today: "today", today + timedelta(days=1): "tomorrow", today - timedelta(days=1): "yesterday"}.get(d)
    if not key:
        return []
    s = _get(f"https://www.bettingclosed.com/predictions/date-matches/{key}/bet-type/1x2")
    out = []
    if not s:
        return out
    for tr in s.find_all("tr"):
        h, a, p = tr.select_one("td.teamAmatch"), tr.select_one("td.teamBmatch"), tr.select_one("td.predMt")
        if not (h and a and p):
            continue
        r = _rec("bettingclosed.com", d.isoformat(), h.get_text(" ", strip=True), a.get_text(" ", strip=True),
                 _result_tip(p.get_text(strip=True)))
        if r:
            out.append(r)
    return out


PARSERS = [statarea, zulubet, soccervital, prosoccer, bettingclosed]


def fetch_day(d: date) -> tuple[list[dict], dict]:
    """All tips for a date, plus a per-source count (0 = nothing returned)."""
    recs, status = [], {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda f: (f.__name__, f(d)), PARSERS))
    for name, rs in results:
        status[name] = len(rs)
        recs.extend(rs)
    return recs, status


def manual_editions(folder: Path) -> list[dict]:
    """Picks researched by hand from tipster articles (picks_eNNN.json): one record per agreeing source."""
    out = []
    for f in sorted(folder.glob("picks_e*.json")):
        ed = json.loads(f.read_text(encoding="utf-8"))
        for p in ed.get("topTen", []):
            for src in p.get("sources", []):
                pick = re.sub(r"\s*\(.*?\)", "", p["pick"]).replace("BTTS: ", "BTTS ").replace("–", "-")
                pick = re.sub(r"^(Over|Under) ([\d.]+) goals$", r"\1 \2", pick)
                out.append({"source": src, "date": p.get("date"), "home": p["home"], "away": p["away"],
                            "market": p["market"], "pick": pick})
    return out


# ---------------- matching tips to fixtures ----------------
_ALIAS = [(r"\butd\b", "united"), (r"\bst\b", "saint"), (r"\bdep\b", "deportivo"), (r"\bath\b", "athletic")]
_DROP = re.compile(r"\b(club|cd|ca|fk|sk|if|ik|bk|ac|as|ss|us|sv|vfb|vfl|tsg|cf|fc|sc|afc|de|la|el|the|u\d\d)\b")


def _key(name: str) -> str:
    s = norm(name)
    for a, b in _ALIAS:
        s = re.sub(a, b, s)
    return re.sub(r"\s+", " ", _DROP.sub(" ", s)).strip()


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb)
    return max(jac, SequenceMatcher(None, a, b).ratio())


def attach(tips: pd.DataFrame, fixtures: pd.DataFrame, threshold: float = 0.72) -> pd.DataFrame:
    """Add match_id to each tip by matching both team names to a fixture on the same or adjacent date."""
    if tips.empty or fixtures.empty:
        return tips.assign(match_id=pd.Series(dtype="float"))
    fx = fixtures[["id", "match_date", "home", "away"]].copy()
    fx["hk"], fx["ak"] = fx["home"].map(_key), fx["away"].map(_key)
    # per date: rows plus a token index, so each tip is only compared with fixtures sharing a word
    by_date = {}
    for d, g in fx.groupby("match_date"):
        rows = list(zip(g["id"], g["hk"], g["ak"]))
        idx = {}
        for i, (_, fh, fa) in enumerate(rows):
            for tok in set((fh + " " + fa).split()):
                if len(tok) > 2:
                    idx.setdefault(tok, set()).add(i)
        by_date[d] = (rows, idx)
    ids = []
    cache = {}
    for _, t in tips.iterrows():
        ck = (t["date"], t["home"], t["away"])
        if ck in cache:
            ids.append(cache[ck])
            continue
        hk, ak = _key(t["home"]), _key(t["away"])
        best, best_s = None, 0.0
        try:
            d0 = date.fromisoformat(str(t["date"])[:10])
        except ValueError:
            d0 = None
        days = [d0 + timedelta(days=o) for o in (0, 1, -1)] if d0 else []
        for d in days:
            entry = by_date.get(d.isoformat())
            if entry is None:
                continue
            rows, idx = entry
            cand = set()
            for tok in set((hk + " " + ak).split()):
                cand |= idx.get(tok, set())
            if not cand:  # no shared word: fall back to prefix-based candidates
                cand = {i for i, (_, fh, fa) in enumerate(rows) if fh[:3] == hk[:3] or fa[:3] == ak[:3]}
            for i in cand:
                fid, fh, fa = rows[i]
                s = min(_sim(hk, fh), _sim(ak, fa))
                if s > best_s:
                    best, best_s = fid, s
            if best_s >= 0.95:
                break
        mid = best if best_s >= threshold else None
        cache[ck] = mid
        ids.append(mid)
    return tips.assign(match_id=ids)


def consensus(tips: pd.DataFrame) -> pd.DataFrame:
    """One row per (match, market, pick): how many distinct sources agree, out of those tipping that market."""
    t = tips.dropna(subset=["match_id"]).copy()
    t["source"] = t["source"].str.replace(r"\.(com|gr|net|live|co\.uk)$", "", regex=True)  # one vote per site
    t = t.drop_duplicates(["match_id", "market", "pick", "source"])
    if t.empty:
        return pd.DataFrame(columns=["match_id", "market", "pick", "agree", "checked", "sources"])
    checked = t.groupby(["match_id", "market"])["source"].nunique().rename("checked")
    agree = (t.groupby(["match_id", "market", "pick"])
               .agg(agree=("source", "nunique"), sources=("source", lambda s: ", ".join(sorted(set(s)))))
               .reset_index())
    out = agree.join(checked, on=["match_id", "market"])
    return out.sort_values(["agree", "checked"], ascending=[False, True])
