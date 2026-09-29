"""Touchline: global football scanner with live tipster consensus.  Run:  streamlit run app.py"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

import tl_tipsters as tipsters
from tl_db import connect
from tl_geo import CONF_LABEL
from tl_ingest import refresh
from tl_picks import grade

st.set_page_config(page_title="Touchline", page_icon="⚽", layout="wide")

HERE = Path(__file__).resolve().parent
CONF_ORDER = ["uefa", "caf", "afc", "concacaf", "conmebol", "ofc", "int"]
CONF_COLORS = {"UEFA": "#2A78D6", "CAF": "#EB6834", "AFC": "#E34948", "CONCACAF": "#6A5ACD",
               "CONMEBOL": "#1BAF7A", "OFC": "#EDA100", "International": "#8A8F98"}
FINISHED = {"FT", "AET", "Pen", "AP", "PEN"}
MARKETS = ["Match result", "Double chance", "Over/Under goals", "BTTS", "Correct score"]


def stamp(minutes: int) -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d%H") + str(now.minute // minutes)


@st.cache_data(ttl=600, show_spinner="Updating fixtures and results…")
def load_matches(_stamp: str) -> tuple[pd.DataFrame, str, pd.DataFrame]:
    refresh()
    con = connect()
    m = pd.read_sql_query(
        "SELECT m.*, c.name AS competition, COALESCE(m.country_name, c.country_code) AS country, c.confederation, c.gender "
        "FROM matches m JOIN competitions c ON c.id = m.competition_id", con)
    last = con.execute("SELECT MAX(fetched_at) FROM fetch_log").fetchone()[0] or ""
    site = pd.read_sql_query("SELECT match_id, source, market, pick FROM site_tips", con)
    return m, last, site


@st.cache_data(ttl=3600, show_spinner="Reading tipster sites and matching their picks…")
def load_tips(_stamp: str, fixtures: pd.DataFrame, site_tips: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    today = datetime.now(timezone.utc).date()
    days = [today + timedelta(days=o) for o in range(-7, 4)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(tipsters.fetch_day, days))
    recs, status = [], {}
    for d, (rs, st_) in zip(days, results):
        recs.extend(rs)
        for k, v in st_.items():
            status[k] = status.get(k, 0) + v
    recs.extend(tipsters.manual_editions(HERE))
    tips = pd.DataFrame(recs, columns=["source", "date", "home", "away", "market", "pick"])
    tips = tipsters.attach(tips, fixtures)
    site = site_tips.merge(fixtures[["id", "match_date", "home", "away"]], left_on="match_id", right_on="id")
    if not site.empty:
        site = site.rename(columns={"match_date": "date"})[["source", "date", "home", "away", "market", "pick", "match_id"]]
        tips = pd.concat([tips, site], ignore_index=True)
        status["soccervista"] = len(site)
    return tips, tipsters.consensus(tips), status


matches, last_fetch, site_tips_df = load_matches(stamp(10))
tips, cons, src_status = load_tips(stamp(60), matches[["id", "match_date", "home", "away"]], site_tips_df)

# ---------- sidebar ----------
with st.sidebar:
    st.title("⚽ Touchline")
    tz = ZoneInfo(st.selectbox("Timezone", ["Africa/Nairobi", "UTC", "Europe/London", "America/New_York", "Asia/Tokyo"]))
    gender = st.segmented_control("Gender", ["All", "Men", "Women"], default="All")
    confs = st.multiselect("Confederation", [CONF_LABEL[c] for c in CONF_ORDER], placeholder="All confederations")
    countries = st.multiselect("Country", sorted(matches["country"].dropna().unique()), placeholder="All countries")
    search = st.text_input("Search", placeholder="Team or competition")
    if last_fetch:
        mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(last_fetch)).total_seconds() // 60)
        st.caption(f"Scores updated {mins} min ago · fotmob.com")
    st.caption(f"Tips read from {sum(1 for v in src_status.values() if v)} of {len(src_status)} sites · hourly")
    if st.button("Refresh now", width="stretch"):
        refresh(force=True)
        load_matches.clear()
        load_tips.clear()
        st.rerun()
    st.divider()
    st.caption("Tipster opinion, not odds or betting advice.  \nResponsible Gambling Kenya · 1199")


def filt(df: pd.DataFrame) -> pd.DataFrame:
    if gender and gender != "All":
        df = df[df["gender"] == gender.lower()]
    if confs:
        df = df[df["confederation"].isin([k for k, v in CONF_LABEL.items() if v in confs])]
    if countries:
        df = df[df["country"].isin(countries)]
    if search:
        s = search.lower()
        df = df[df["home"].str.lower().str.contains(s, regex=False) | df["away"].str.lower().str.contains(s, regex=False)
                | df["competition"].str.lower().str.contains(s, regex=False)]
    return df


def local(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    k = pd.to_datetime(df["kickoff_utc"], utc=True, errors="coerce").dt.tz_convert(tz)
    df["Time"] = k.dt.strftime("%H:%M")
    df["Kick-off"] = k.dt.strftime("%a %d %b %H:%M")
    df["Conf"] = df["confederation"].map(CONF_LABEL)
    return df


def score(r) -> str:
    return f"{int(r.home_score)}–{int(r.away_score)}" if pd.notna(r.home_score) and pd.notna(r.away_score) else ""


# consensus joined to fixtures
cm = cons.merge(matches, left_on="match_id", right_on="id", how="inner") if not cons.empty else pd.DataFrame()
now_utc = pd.Timestamp.now(tz="UTC")
if not cm.empty:
    ko = pd.to_datetime(cm["kickoff_utc"], utc=True, errors="coerce")
    cm["upcoming"] = (cm["status"] == "NS") & (ko > now_utc)
    cm["graded"] = [grade(mk, pk, h, a) if s in FINISHED else None
                    for mk, pk, h, a, s in zip(cm["market"], cm["pick"], cm["home_score"], cm["away_score"], cm["status"])]
qual = cm[cm["agree"] >= 2] if not cm.empty else cm


def best_pick_by_match(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    top = df.sort_values(["agree", "checked"], ascending=[False, True]).drop_duplicates("match_id")
    return top.set_index("match_id").apply(lambda r: f"{r['pick']} · {r['agree']}/{r['checked']}", axis=1)


def date_opts(dates):
    today = datetime.now(tz).date().isoformat()
    counts = matches.groupby("match_date").size().to_dict()
    return {d: f"{datetime.fromisoformat(d):%a %d %b}{' (today)' if d == today else ''} · {counts.get(d, 0)}" for d in dates}


all_dates = sorted(matches["match_date"].unique())
today_iso = datetime.now(timezone.utc).date().isoformat()

t_picks, t_fx, t_res, t_scan, t_src = st.tabs(["🎯 Consensus picks", "📅 Fixtures", "🏁 Results", "🌍 Scanner", "📡 Sources"])

# ---------- Consensus picks: upcoming only ----------
with t_picks:
    up = filt(qual[qual["upcoming"]]) if not qual.empty else qual
    c1, c2 = st.columns([3, 1])
    mk = c1.pills("Market", MARKETS, selection_mode="multi", label_visibility="collapsed")
    min_src = c2.segmented_control("Min. sources", [2, 3, 4], default=2, label_visibility="collapsed",
                                   format_func=lambda x: f"{x}+ agree")
    if not up.empty:
        if mk:
            up = up[up["market"].isin(mk)]
        up = up[up["agree"] >= (min_src or 2)]
    k = st.columns(4)
    k[0].metric("Upcoming picks", len(up))
    k[1].metric("Matches", up["match_id"].nunique() if not up.empty else 0)
    k[2].metric("Strongest", f"{up['agree'].max()}/{up.loc[up['agree'].idxmax(), 'checked']}" if not up.empty else "–")
    k[3].metric("Countries", up["country"].nunique() if not up.empty else 0)
    if up.empty:
        st.info("No upcoming match has 2+ tipster sites agreeing for these filters.")
    else:
        v = local(up).sort_values(["agree", "kickoff_utc"], ascending=[False, True])
        v["Match"] = v["home"] + " v " + v["away"]
        v["Agreement"] = v["agree"] / v["checked"]
        v["Sources"] = v["agree"].astype(str) + "/" + v["checked"].astype(str)
        st.dataframe(v[["Kick-off", "Match", "Conf", "country", "competition", "market", "pick", "Agreement", "Sources", "sources"]]
                     .rename(columns={"country": "Country", "competition": "Competition", "market": "Market",
                                      "pick": "Pick", "sources": "Who agrees"}),
                     hide_index=True, width="stretch", height=min(36 * len(v) + 40, 760),
                     column_config={"Agreement": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")})

# ---------- Fixtures ----------
with t_fx:
    fx_dates = [x for x in all_dates if x >= today_iso]
    opts = date_opts(fx_dates)
    c1, c2 = st.columns([2, 1])
    d = c1.selectbox("Fixture date", fx_dates, format_func=opts.get, key="fx_date", label_visibility="collapsed")
    only = c2.toggle("Only matches with consensus")
    day = local(filt(matches[matches["match_date"] == d]))
    bp = best_pick_by_match(qual)
    allbp = best_pick_by_match(cm)
    day["Consensus"] = day["id"].map(bp).fillna("")
    day["Top tip"] = day["id"].map(allbp).fillna("")
    day["Score"] = day.apply(score, axis=1)
    day["Home form"] = day["home_form"].fillna("").str[::-1]
    day["Away form"] = day["away_form"].fillna("")
    k = st.columns(4)
    k[0].metric("Matches", len(day))
    k[1].metric("To play", int((day["status"] == "NS").sum()))
    k[2].metric("Tipped", int((day["Top tip"] != "").sum()))
    k[3].metric("With consensus", int((day["Consensus"] != "").sum()))
    if only:
        day = day[day["Consensus"] != ""]
    day = day.assign(_c=day["Consensus"] != "", _t=day["Top tip"] != "").sort_values(["_c", "_t", "Time"], ascending=[False, False, True])
    st.dataframe(day[["Time", "Conf", "country", "competition", "Home form", "home", "Score", "away", "Away form", "status", "Consensus", "Top tip"]]
                 .rename(columns={"country": "Country", "competition": "Competition", "home": "Home", "away": "Away", "status": "Status"}),
                 hide_index=True, width="stretch", height=min(36 * len(day) + 40, 760))

# ---------- Results ----------
with t_res:
    res_dates = [x for x in all_dates if x <= today_iso]
    opts = date_opts(res_dates)
    default = len(res_dates) - 1
    if not ((matches["match_date"] == res_dates[-1]) & matches["status"].isin(FINISHED)).any() and default > 0:
        default -= 1
    d = st.selectbox("Result date", res_dates, index=default, format_func=opts.get, key="res_date", label_visibility="collapsed")
    done = local(filt(matches[(matches["match_date"] == d) & matches["status"].isin(FINISHED) & matches["home_score"].notna()]))
    n = len(done)
    hs, as_ = done["home_score"].astype(int), done["away_score"].astype(int)
    goals = int((hs + as_).sum())
    pct = lambda x: f"{100 * x / n:.0f}%" if n else "–"
    graded = qual[(qual["match_date"] == d) & qual["graded"].notna()] if not qual.empty else qual
    graded = graded[graded["match_id"].isin(done["id"])] if not graded.empty else graded
    k = st.columns(6)
    k[0].metric("Played", n)
    k[1].metric("Goals / match", f"{goals / n:.2f}" if n else "–")
    k[2].metric("Over 2.5", pct(((hs + as_) > 2).sum()))
    k[3].metric("Both scored", pct(((hs > 0) & (as_ > 0)).sum()))
    k[4].metric("Home · Draw · Away", f"{pct((hs > as_).sum())} · {pct((hs == as_).sum())} · {pct((hs < as_).sum())}")
    k[5].metric("Consensus picks landed", f"{int(graded['graded'].sum())}/{len(graded)}" if len(graded) else "–")
    if n:
        l, r = st.columns(2)
        byc = done.assign(G=hs + as_).groupby("Conf").agg(Matches=("id", "count"), Avg=("G", "mean")).reset_index()
        f = px.bar(byc, x="Conf", y="Matches", color="Conf", color_discrete_map=CONF_COLORS,
                   text=byc["Avg"].round(1).astype(str) + " g/m")
        f.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=0), showlegend=False, xaxis_title=None)
        l.plotly_chart(f, width="stretch")
        if len(graded):
            g = local(graded)
            g["Match"] = g["home"] + " v " + g["away"]
            g["Score"] = g.apply(score, axis=1)
            g["Outcome"] = g["graded"].map({True: "✅", False: "❌"})
            r.dataframe(g[["Outcome", "Match", "market", "pick", "agree", "checked", "Score"]]
                        .rename(columns={"market": "Market", "pick": "Pick", "agree": "Agree", "checked": "Of"}),
                        hide_index=True, width="stretch", height=250)
        else:
            r.info("No consensus picks were graded on this date.")
        done["Score"] = done.apply(score, axis=1)
        done["Pick"] = done["id"].map(best_pick_by_match(qual)).fillna("")
        st.dataframe(done.sort_values(["Pick", "Conf"], ascending=[False, True])
                     [["Time", "Conf", "country", "competition", "home", "Score", "away", "Pick"]]
                     .rename(columns={"country": "Country", "competition": "Competition", "home": "Home", "away": "Away",
                                      "Pick": "Consensus pick"}),
                     hide_index=True, width="stretch", height=min(36 * n + 40, 760))
    else:
        st.info("No finished matches for this date and filter.")

# ---------- Scanner ----------
with t_scan:
    opts = date_opts(all_dates)
    d = st.selectbox("Date", all_dates, index=all_dates.index(today_iso) if today_iso in all_dates else 0,
                     format_func=opts.get, key="scan_date", label_visibility="collapsed")
    day = filt(matches[matches["match_date"] == d])
    k = st.columns(5)
    k[0].metric("Matches", len(day))
    k[1].metric("Countries", day["country"].nunique())
    k[2].metric("Competitions", day["competition"].nunique())
    k[3].metric("Men", int((day["gender"] == "men").sum()))
    k[4].metric("Women", int((day["gender"] == "women").sum()))
    if not day.empty:
        by = (day.assign(Conf=day["confederation"].map(CONF_LABEL), Gender=day["gender"].str.title())
                 .groupby(["Conf", "Gender"]).size().reset_index(name="Matches"))
        f = px.bar(by, x="Conf", y="Matches", color="Gender", color_discrete_map={"Men": "#0E7A4F", "Women": "#B83E78"},
                   category_orders={"Conf": [CONF_LABEL[c] for c in CONF_ORDER]})
        f.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), legend_title=None, xaxis_title=None)
        st.plotly_chart(f, width="stretch")
    wk = filt(matches)
    tr = wk.groupby(["match_date", "gender"]).size().reset_index(name="Matches")
    tr["Day"] = pd.to_datetime(tr["match_date"]).dt.strftime("%a %d")
    f = px.bar(tr, x="Day", y="Matches", color="gender", color_discrete_map={"men": "#0E7A4F", "women": "#B83E78"})
    f.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0), legend_title=None, xaxis_title=None)
    st.plotly_chart(f, width="stretch")

# ---------- Sources: reliability ----------
with t_src:
    t = tips.dropna(subset=["match_id"]).merge(matches[["id", "status", "home_score", "away_score"]], left_on="match_id", right_on="id")
    t = t[t["status"].isin(FINISHED)]
    t["hit"] = [grade(mk, pk, h, a) for mk, pk, h, a in zip(t["market"], t["pick"], t["home_score"], t["away_score"])]
    t = t.dropna(subset=["hit"])
    raw = tips.groupby("source").agg(Tips=("pick", "count"), Matched=("match_id", lambda s: int(s.notna().sum())))
    if not t.empty:
        rel = t.groupby("source").agg(Graded=("hit", "count"), Landed=("hit", "sum"))
        rel["Hit rate"] = rel["Landed"] / rel["Graded"]
        raw = raw.join(rel, how="left")
    raw = raw.reset_index().rename(columns={"source": "Source"}).sort_values("Tips", ascending=False)
    st.dataframe(raw, hide_index=True, width="stretch",
                 column_config={"Hit rate": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")})
    st.caption("Hit rate = graded tips that landed over the last 7 days. Sites are read automatically every hour.")
