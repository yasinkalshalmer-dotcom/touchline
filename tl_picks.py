"""Matching tipster picks to matches and grading them against final scores."""
import re
import unicodedata

_STRIP = re.compile(r"\b(w|women|fc|cf|sc|afc|the)\b")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = _STRIP.sub("", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def same_team(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    return bool(a and b and (a == b or a.startswith(b) or b.startswith(a)))


def grade(market: str, pick: str, hs, as_):
    """True if the pick landed, False if it missed, None if it can't be graded."""
    if hs is None or as_ is None or hs != hs or as_ != as_:  # missing or NaN
        return None
    hs, as_ = int(hs), int(as_)
    tot = hs + as_
    if market == "Match result":
        if pick.lower().startswith("home win"):
            return hs > as_
        if pick.lower().startswith("away win"):
            return as_ > hs
        if pick.lower().startswith("draw"):
            return hs == as_
        return None
    if market == "Over/Under goals":
        m = re.search(r"(over|under)\s*([\d.]+)", pick, re.I)
        if not m:
            return None
        return tot > float(m.group(2)) if m.group(1).lower() == "over" else tot < float(m.group(2))
    if market == "BTTS":
        both = hs > 0 and as_ > 0
        return both if "yes" in pick.lower() else (not both if "no" in pick.lower() else None)
    if market == "Correct score":
        m = re.search(r"(\d+)\s*[-–]\s*(\d+)", pick)
        return (int(m.group(1)) == hs and int(m.group(2)) == as_) if m else None
    return None
