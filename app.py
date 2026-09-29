"""Touchline: global football scanner. Run with:  streamlit run app.py"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

from tl_db import connect
from tl_geo import CONF_LABEL
from tl_ingest import refresh
from tl_picks import grade, same_team

st.set_page_config(page_title="Touchline", page_icon="⚽", layout="wide")

CONF_ORDER = ["uefa", "caf", "afc", "concacaf", "conmebol", "ofc", "int"]
CONF_COLORS = {"UEFA": "#2A78D6", "CAF": "#EB6834", "AFC": "#E34948", "CONCACAF": "#6A5ACD",
               "CONMEBOL": "#1BAF7A", "OFC": "#EDA100", "International": "#8A8F98"}
FINISHED = {"FT", "AET", "Pen", "AP", "PEN"}


@st.cache_data(ttl=600, show_spinner="Updating fixtures and results…")
def load(stamp: str) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    refresh()
    con = connect()
    m = pd.read_sql_query(
        "SELECT m.*, c.name AS competition, c.country_code AS country, c.confederation, c.gender, c.rank "
        "FROM matches m JOIN competitions c ON c.id = m.competition_id", con)
    p = pd.read_sql_query("SELECT * FROM tipster_picks", con)
    last = con.execute("SELECT MAX(fetched_at) FROM fetch_log").fetchone()[0] or ""
    return m, p, last


def ten_minute_stamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d%H") + str(now.minute // 10)


matches, picks, last_fetch = load(ten_minute_stamp())

# ---------- sidebar filters ----------
with st.sidebar:
    st.title("⚽ Touchline")
    tz_name = st.selectbox("Timezone", ["Africa/Nairobi", "UTC", "Europe/London", "America/New_York", "Asia/Tokyo"])
    tz = ZoneInfo(tz_name)
    gender = st.segmented_control("Gender", ["All", "Men", "Women"], default="All")
    confs = st.multiselect("Confederation", [CONF_LABEL[c] for c in CONF_ORDER], placeholder="All confederations")
    countries = st.multiselect("Country code", sorted(matches["country"].dropna().unique()), placeholder="All countries")
    search = st.text_input("Search", placeholder="Team or competition")
    if last_fetch:
        mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(last_fetch)).total_seconds() // 60)
        st.caption(f"Data updated {mins} min ago · source: fotmob.com")
    if st.button("Refresh now", width="stretch"):
        refresh(force=True)
        load.clear()
        st.rerun()
    st.divider()
    st.caption("Tipster opinion, not odds or betting advice.  \nResponsible Gambling Kenya · responsiblegambling.or.ke · toll-free 1199")


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    if gender and gender != "All":
        df = df[df["gender"] == gender.lower()]
    if confs:
        keys = [k for k, v in CONF_LABEL.items() if v in confs]
        df = df[df["confederation"].isin(keys)]
    if countries:
        df = df[df["country"].isin(countries)]
    if search:
        s = search.lower()
        df = df[df["home"].str.lower().str.contains(s, regex=False) | df["away"].str.lower().str.contains(s, regex=False)
                | df["competition"].str.lower().str.contains(s, regex=False)]
    return df


def with_local_time(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    k = pd.to_datetime(df["kickoff_utc"], utc=True, errors="coerce")
    df["Time"] = k.dt.tz_convert(tz).dt.strftime("%H:%M")
    df["Conf"] = df["confederation"].map(CONF_LABEL)
    return df


def attach_picks(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    labels, grades = [], []
    for _, r in df.iterrows():
        ps = picks[(picks["match_date"] == r["match_date"])]
        ps = ps[[same_team(a, r["home"]) and same_team(b, r["away"]) for a, b in zip(ps["home"], ps["away"])]]
        if ps.empty:
            labels.append("")
            grades.append("")
            continue
        top = ps.sort_values("agree", ascending=False).iloc[0]
        labels.append(f"{top['pick']} ({top['agree']}/{top['checked']})" + (f" +{len(ps) - 1}" if len(ps) > 1 else ""))
        g = [grade(p["market"], p["pick"], r["home_score"], r["away_score"]) for _, p in ps.iterrows()]
        g = [x for x in g if x is not None]
        grades.append(f"{sum(g)}/{len(g)} ✓" if g else "")
    df["Consensus pick"] = labels
    df["Picks landed"] = grades
    return df


def date_options(dates: list[str]) -> dict:
    today = datetime.now(tz).date().isoformat()
    counts = matches.groupby("match_date").size().to_dict()
    out = {}
    for d in dates:
        dt = datetime.fromisoformat(d)
        tag = " (today)" if d == today else ""
        out[d] = f"{dt:%a %d %b}{tag} · {counts.get(d, 0)}"
    return out


all_dates = sorted(matches["match_date"].unique())
today_iso = datetime.now(timezone.utc).date().isoformat()

tab_scan, tab_fx, tab_res, tab_picks = st.tabs(["🌍 Scanner", "📅 Fixtures", "🏁 Results", "🎯 Consensus picks"])

# ---------- Scanner ----------
with tab_scan:
    opts = date_options(all_dates)
    d = st.selectbox("Date", list(opts), index=all_dates.index(today_iso) if today_iso in all_dates else 0,
                     format_func=opts.get, key="scan_date", label_visibility="collapsed")
    day = apply_filters(matches[matches["match_date"] == d])
    c = st.columns(5)
    c[0].metric("Matches", len(day))
    c[1].metric("Countries", day["country"].nunique())
    c[2].metric("Competitions", day["competition"].nunique())
    c[3].metric("Men", int((day["gender"] == "men").sum()))
    c[4].metric("Women", int((day["gender"] == "women").sum()))
    if not day.empty:
        left, right = st.columns([3, 2])
        by = (day.assign(Conf=day["confederation"].map(CONF_LABEL), Gender=day["gender"].str.title())
                 .groupby(["Conf", "Gender"]).size().reset_index(name="Matches"))
        fig = px.bar(by, x="Conf", y="Matches", color="Gender", barmode="stack",
                     color_discrete_map={"Men": "#0E7A4F", "Women": "#B83E78"},
                     category_orders={"Conf": [CONF_LABEL[k] for k in CONF_ORDER]})
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), legend_title=None, xaxis_title=None)
        left.plotly_chart(fig, width="stretch")
        top = (day.groupby(["competition", "country"]).size().reset_index(name="Matches")
                  .sort_values("Matches", ascending=False).head(12))
        right.dataframe(top.rename(columns={"competition": "Competition", "country": "Country"}),
                        hide_index=True, width="stretch", height=320)
    week = apply_filters(matches)
    trend = week.groupby(["match_date", "gender"]).size().reset_index(name="Matches")
    trend["Day"] = pd.to_datetime(trend["match_date"]).dt.strftime("%a %d")
    fig2 = px.bar(trend, x="Day", y="Matches", color="gender", color_discrete_map={"men": "#0E7A4F", "women": "#B83E78"})
    fig2.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0), legend_title=None, xaxis_title=None)
    st.plotly_chart(fig2, width="stretch")

# ---------- Fixtures ----------
with tab_fx:
    fx_dates = [x for x in all_dates if x >= today_iso]
    if not fx_dates:
        st.info("No upcoming fixtures loaded.")
    else:
        opts = date_options(fx_dates)
        d = st.selectbox("Fixture date", fx_dates, format_func=opts.get, key="fx_date")
        day = apply_filters(matches[matches["match_date"] == d])
        day = attach_picks(with_local_time(day))
        c = st.columns(4)
        c[0].metric("Matches", len(day))
        c[1].metric("To play", int((day["status"] == "NS").sum()))
        c[2].metric("Live", int((day["status"] == "Live").sum()))
        c[3].metric("With consensus picks", int((day["Consensus pick"] != "").sum()))
        view = day.sort_values(["Consensus pick", "Time"], ascending=[False, True])
        view["Score"] = view.apply(lambda r: f"{int(r.home_score)}–{int(r.away_score)}" if pd.notna(r.home_score) and pd.notna(r.away_score) else "", axis=1)
        st.dataframe(
            view[["Time", "Conf", "country", "competition", "home", "Score", "away", "status", "Consensus pick"]]
                .rename(columns={"country": "Country", "competition": "Competition", "home": "Home",
                                 "away": "Away", "status": "Status"}),
            hide_index=True, width="stretch", height=min(38 * len(view) + 40, 720))

# ---------- Results ----------
with tab_res:
    res_dates = [x for x in all_dates if x <= today_iso]
    opts = date_options(res_dates)
    default = len(res_dates) - 1
    if res_dates and matches[(matches["match_date"] == res_dates[-1]) & matches["status"].isin(FINISHED)].empty and default > 0:
        default -= 1
    d = st.selectbox("Result date", res_dates, index=default, format_func=opts.get, key="res_date")
    day = apply_filters(matches[matches["match_date"] == d])
    done = day[day["status"].isin(FINISHED) & day["home_score"].notna()].copy()
    done = attach_picks(with_local_time(done))
    n = len(done)
    hs, as_ = done["home_score"].astype(int), done["away_score"].astype(int)
    goals = int((hs + as_).sum())
    pct = lambda x: f"{(100 * x / n):.0f}%" if n else "–"
    c = st.columns(6)
    c[0].metric("Played", n)
    c[1].metric("Goals", goals, f"{goals / n:.2f} per match" if n else None, delta_color="off")
    c[2].metric("Over 2.5", pct(((hs + as_) > 2).sum()))
    c[3].metric("Both scored", pct(((hs > 0) & (as_ > 0)).sum()))
    c[4].metric("Home wins", pct((hs > as_).sum()))
    c[5].metric("Draws", pct((hs == as_).sum()))
    if n:
        left, right = st.columns(2)
        outcome = pd.Series({"Home": int((hs > as_).sum()), "Draw": int((hs == as_).sum()), "Away": int((hs < as_).sum())})
        fig = px.pie(values=outcome.values, names=outcome.index, hole=.55,
                     color=outcome.index, color_discrete_map={"Home": "#12813F", "Draw": "#E3A21A", "Away": "#C4382F"})
        fig.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0), legend_title=None)
        left.plotly_chart(fig, width="stretch")
        byc = done.assign(Goals=hs + as_).groupby("Conf").agg(Matches=("id", "count"), Avg_goals=("Goals", "mean")).reset_index()
        fig = px.bar(byc, x="Conf", y="Matches", color="Conf", text=byc["Avg_goals"].round(1).astype(str) + " g/m",
                     color_discrete_map=CONF_COLORS)
        fig.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0), showlegend=False, xaxis_title=None)
        right.plotly_chart(fig, width="stretch")
        done["Score"] = hs.astype(str) + "–" + as_.astype(str)
        st.dataframe(
            done.sort_values(["Picks landed", "Conf", "competition"], ascending=[False, True, True])
                [["Time", "Conf", "country", "competition", "home", "Score", "away", "Consensus pick", "Picks landed"]]
                .rename(columns={"country": "Country", "competition": "Competition", "home": "Home", "away": "Away"}),
            hide_index=True, width="stretch", height=min(38 * n + 40, 720))
    else:
        st.info("No finished matches for this date and filter.")

# ---------- Consensus picks ----------
with tab_picks:
    if picks.empty:
        st.info("No tipster-consensus edition loaded yet.")
    else:
        ed = int(picks["edition"].max())
        cur = picks[picks["edition"] == ed].copy()
        rows = []
        for _, p in cur.iterrows():
            mm = matches[(matches["match_date"] == p["match_date"])]
            mm = mm[[same_team(a, p["home"]) and same_team(b, p["away"]) for a, b in zip(mm["home"], mm["away"])]]
            res, g = "", None
            if not mm.empty and mm.iloc[0]["status"] in FINISHED and pd.notna(mm.iloc[0]["home_score"]) and pd.notna(mm.iloc[0]["away_score"]):
                r = mm.iloc[0]
                res = f"{int(r.home_score)}–{int(r.away_score)}"
                g = grade(p["market"], p["pick"], r.home_score, r.away_score)
            rows.append({"Market": p["market"], "Match": f"{p['home']} v {p['away']}", "Date": p["match_date"],
                         "Pick": p["pick"], "Agree": p["agree"] / max(p["checked"], 1), "Sources": f"{p['agree']}/{p['checked']}",
                         "Result": res, "Outcome": "✅ landed" if g else ("❌ missed" if g is False else "⏳"),
                         "Who": p["sources"]})
        df = pd.DataFrame(rows).sort_values(["Market", "Agree"], ascending=[True, False])
        graded = df[df["Outcome"] != "⏳"]
        c = st.columns(4)
        c[0].metric("Edition", ed)
        c[1].metric("Qualifying picks", len(df))
        c[2].metric("Graded", len(graded))
        c[3].metric("Landed", f"{(graded['Outcome'] == '✅ landed').sum()}/{len(graded)}" if len(graded) else "–")
        market = st.pills("Market", sorted(df["Market"].unique()), selection_mode="multi", label_visibility="collapsed")
        if market:
            df = df[df["Market"].isin(market)]
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"Agree": st.column_config.ProgressColumn("Agreement", min_value=0, max_value=1, format="percent"),
                                    "Who": st.column_config.TextColumn("Sources agreeing", width="large")})
