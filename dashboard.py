"""

MoveInSync Play Store ratings: diagnosis + action plan dashboard.

Run:  pip install streamlit pandas google-genai

      streamlit run dashboard.py

Reads out/reviews_tagged.csv produced by moveinsync_ratings_pipeline.py (same folder).

Optional: set GEMINI_API_KEY to enable the AI-written root-cause brief.

"""

import json
import os
import subprocess
import sys

import pandas as pd

import streamlit as st

DATA = "out/reviews_tagged.csv"

st.set_page_config(page_title="MoveInSync ratings", layout="wide")

st.title("MoveInSync Play Store ratings")

# Concrete first actions per theme. The DATA decides the ORDER; this decides WHAT to do.

PLAYBOOK = {

    "booking_fails": dict(owner="Engineering + Product",

        day1="Add funnel events (open > pick shift > pick slot > confirm > success) with error codes; pull the top 5 failing error codes from the last 30 days.",

        then="Replace generic errors with the reason and next step (cut-off passed, roster missing, no seats). Add a one-tap 'ask transport desk' fallback.",

        metric="Booking success rate; % of 1-2 star reviews mentioning booking"),

    "login_otp_sso": dict(owner="Engineering",

        day1="Measure OTP delivery and SSO failure rate by customer company and by telecom operator for the last 30 days.",

        then="Add resend timer, email OTP fallback, longer session persistence, and a clear message when an employee is not mapped to a company.",

        metric="Login success rate; OTP delivery time p90"),

    "late_or_no_cab": dict(owner="Operations + Product",

        day1="Add a 'cab not arrived' button that opens an escalation to the transport desk with the trip ID attached.",

        then="Show the assigned vehicle and honest live ETA, send proactive delay alerts, and give each customer an on-time pickup report.",

        metric="On-time pickup %; late-cab complaints per 1,000 trips"),

    "tracking_gps": dict(owner="Engineering",

        day1="Log location update lag and accuracy by device and OS; list the worst 10 device models.",

        then="Show 'last updated N sec ago' instead of a frozen pin, and fix permission and battery-optimisation onboarding on the driver app.",

        metric="Median location lag; % trips with a tracking gap"),

    "route_pickup_time": dict(owner="Product + Operations",

        day1="List every pickup-time change in the last 30 days and how many were notified to the employee.",

        then="Notify every change with the reason and minutes shifted; show the planned route and pickup time before the cut-off.",

        metric="% pickup changes notified; reviews mentioning pickup time"),

    "app_crash_perf": dict(owner="Engineering",

        day1="Open Play Console > Android vitals; list the top 3 crash and ANR clusters by app version and device.",

        then="Fix those clusters; ship with staged rollout (5% > 20% > 100%) and auto-halt if crash rate rises.",

        metric="Crash-free users %; ANR rate"),

    "driver_safety": dict(owner="Safety + Operations",

        day1="Route every review mentioning safety, SOS, or harassment to the safety team the same day.",

        then="Add repeat-offender tracking per driver and show driver verification before boarding.",

        metric="Safety reports closed in 24h; repeat-offender count"),

    "support_resolution": dict(owner="Support",

        day1="Reply to every 1-2 star review from the last 30 days with a route to raise a ticket (template reply, 2 hours of work).",

        then="Show ticket status inside the app and set a 48-hour reply SLA on all 1-3 star reviews.",

        metric="Developer reply rate; median ticket resolution time"),

}

LABEL = lambda t: t.replace("_", " ").title()

# ---------- data ----------

@st.cache_data(show_spinner=False)

def load(path, mtime):

    df = pd.read_csv(path, parse_dates=["at"])

    df["appVersion"] = df["appVersion"].fillna("unknown").astype(str)

    df["themes"] = df["themes"].fillna("other")

    df["replied"] = df["replyContent"].notna() if "replyContent" in df else False

    return df

with st.sidebar:

    st.header("Controls")

    if st.button("Refresh data (re-run pipeline)", width="stretch"):

        with st.spinner("Fetching and tagging reviews, this can take a few minutes..."):

            p = subprocess.run([sys.executable, "moveinsync_ratings_pipeline.py"], capture_output=True, text=True)

        load.clear()

        if p.returncode != 0:

            st.error(p.stderr[-1500:])

if not os.path.exists(DATA):

    st.info("No data yet. Click **Refresh data** in the sidebar, or run `python moveinsync_ratings_pipeline.py` first.")

    st.stop()

df = load(DATA, os.path.getmtime(DATA))

apps = sorted(df["app"].unique())

with st.sidebar:

    app_sel = st.selectbox("App", apps) if len(apps) > 1 else apps[0]

    win = st.slider("Comparison window (days)", 30, 180, 90, step=15)

    min_n = st.slider("Min reviews per app version", 5, 100, 30, step=5)

    target = st.slider("Target star level for 'rating drag'", 3, 5, 4,

                       help="Rating drag = how many stars the overall average would gain if every review below this level had given this level instead.")

df = df[df["app"] == app_sel].copy()

rev = df.drop_duplicates("reviewId")

now = rev["at"].max()

cur_mask = lambda d: d["at"] > now - pd.Timedelta(days=win)

pri_mask = lambda d: (d["at"] <= now - pd.Timedelta(days=win)) & (d["at"] > now - pd.Timedelta(days=2 * win))

cur, pri = rev[cur_mask(rev)], rev[pri_mask(rev)]

store = df["store_score"].iloc[0]

neg_share = lambda d: (d["score"] <= 2).mean() * 100 if len(d) else float("nan")

reply_rate = lambda d: d["replied"].mean() * 100 if len(d) else float("nan")

# per-theme stats

neg = df[df["score"] <= 2]

def theme_share(d):

    return d.groupby("themes")["reviewId"].nunique() / max(d["reviewId"].nunique(), 1) * 100

tcur, tpri = theme_share(neg[cur_mask(neg)]), theme_share(neg[pri_mask(neg)])

n_themes = df.groupby("reviewId")["themes"].transform("nunique")

df["gap"] = (target - df["score"]).clip(lower=0) / n_themes        # split a review's gap across its themes

drag = df[cur_mask(df)].groupby("themes")["gap"].sum() / max(len(cur), 1)

stats = pd.DataFrame({"drag": drag, "share_now": tcur, "share_prior": tpri}).fillna(0)

stats["trend"] = stats["share_now"] - stats["share_prior"]

stats = stats.drop(index=[i for i in ("praise", "other") if i in stats.index]).sort_values("drag", ascending=False)

g = rev.groupby("appVersion")["score"].agg(avg="mean", reviews="count")

g["last_seen"] = rev.groupby("appVersion")["at"].max().dt.date

g = g[g["reviews"] >= min_n].sort_values("avg")

bad_versions = g[g["avg"] <= rev["score"].mean() - 0.3]

tab1, tab2, tab3 = st.tabs(["1. Why are ratings where they are?  2. What to fix first", "Trends and themes", "Read the reviews"])

# =============== TAB 1: DIAGNOSIS + PLAN ===============

with tab1:

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric("Store rating (lifetime)", f"{store:.2f}")

    c2.metric(f"Review avg, last {win}d", f"{cur.score.mean():.2f}", f"{cur.score.mean() - pri.score.mean():+.2f} vs prior")

    c3.metric("1-2 star share", f"{neg_share(cur):.0f}%", f"{neg_share(cur) - neg_share(pri):+.0f} pts", delta_color="inverse")

    c4.metric("Reviews in window", f"{len(cur):,}", f"{len(cur) - len(pri):+,}")

    c5.metric("Developer reply rate", f"{reply_rate(cur):.0f}%", f"{reply_rate(cur) - reply_rate(pri):+.0f} pts")

    st.header("1. Why the ratings are where they are")

    top = stats.head(3)

    bullets = []

    gap = store - cur.score.mean()

    if abs(gap) >= 0.15:

        bullets.append(f"**Recent reviews average {cur.score.mean():.2f} vs a lifetime store rating of {store:.2f}** "

                       f"({'recent users are less happy than the long-run average' if gap > 0 else 'recent users are happier than the long-run average'}). "

                       "Review text also skews negative, so compare trends, not absolutes.")

    if len(top):

        parts = ", ".join(f"**{LABEL(t)}** ({r.share_now:.0f}% of 1-2 star reviews, costing ~{r.drag:.2f} stars)" for t, r in top.iterrows())

        bullets.append(f"**The three biggest causes of lost stars:** {parts}. Together ~{top.drag.sum():.2f} of the "

                       f"{stats.drag.sum():.2f} stars available if every sub-{target}-star review had given {target}.")

    rising = stats[stats["trend"] >= 3]

    if len(rising):

        bullets.append("**Getting worse vs the prior window:** " + ", ".join(f"{LABEL(t)} (+{r.trend:.0f} pts)" for t, r in rising.iterrows()) + ".")

    if len(bad_versions):

        bullets.append("**Release problem:** " + ", ".join(f"v{v} ({r.avg:.2f} avg, {int(r.reviews)} reviews)" for v, r in bad_versions.head(3).iterrows())

                       + " rate well below the app average, which points at a regression, not a long-standing issue.")

    if reply_rate(cur) < 20:

        bullets.append(f"**Almost nobody gets a reply:** only {reply_rate(cur):.0f}% of recent reviews have a developer response, "

                       "so frustrated users have no visible route to a fix.")

    for b in bullets:

        st.markdown("- " + b)

    st.caption("'Rating drag' = stars the overall average would gain if reviews below the target level had given the target level, "

               "with each review's gap split across its themes. It is an upper bound for fixing a theme, not a forecast.")

    st.header("2. What to fix, in order")

    rows = []

    for rank, (t, r) in enumerate(stats.iterrows(), 1):

        pb = PLAYBOOK.get(t)

        if not pb:

            continue

        rows.append({"#": rank, "Problem": LABEL(t), "Stars lost": round(r.drag, 3), "% of 1-2 star": round(r.share_now, 1),

                     "Trend (pts)": round(r.trend, 1), "Owner": pb["owner"], "Start tomorrow": pb["day1"], "Track": pb["metric"]})

    plan = pd.DataFrame(rows)

    st.dataframe(plan, hide_index=True, width="stretch")

    st.subheader("What to do first")

    first = []

    if len(bad_versions):

        v = bad_versions.index[0]

        first.append(f"**Day 1, Engineering:** find what changed in v{v}; hotfix or roll back, and gate future releases on crash rate with a staged rollout.")

    if len(plan):

        t0 = plan.iloc[0]

        first.append(f"**Day 1, {t0['Owner']}:** {t0['Start tomorrow']} (largest source of lost stars: {t0['Problem']}).")

    first.append("**Week 1, Support:** reply to every 1-2 star review from the last 30 days with a ticket route. It costs hours, and users sometimes revise their rating after a helpful reply.")

    if len(plan) > 1:

        t1 = plan.iloc[1]

        first.append(f"**Week 1, {t1['Owner']}:** {t1['Start tomorrow']} (second largest: {t1['Problem']}).")

    first.append("**Then, monthly:** re-run this dashboard, check whether the targeted theme's share of 1-2 star reviews fell, and move down the list.")

    for i, f in enumerate(first, 1):

        st.markdown(f"{i}. {f}")

    if len(plan):

        st.subheader("Follow-up fixes (after the first actions land)")

        for t in stats.index:

            if t in PLAYBOOK:

                st.markdown(f"- **{LABEL(t)}:** {PLAYBOOK[t]['then']}")

    st.subheader("AI-written brief (optional)")

    st.caption("Sends theme stats and the most-upvoted negative reviews to Gemini and asks for a root-cause brief. Needs GEMINI_API_KEY.")

    if st.button("Generate AI brief"):

        if not os.getenv("GEMINI_API_KEY"):

            st.warning("Set the GEMINI_API_KEY environment variable and restart Streamlit.")

        else:

            try:

                from google import genai

                payload = {"store_rating": round(float(store), 2), "recent_avg": round(float(cur.score.mean()), 2),

                           "prior_avg": round(float(pri.score.mean()), 2), "themes": stats.round(2).head(5).reset_index().to_dict("records"),

                           "worst_versions": g.head(3).round(2).reset_index().to_dict("records"), "reply_rate_pct": round(float(reply_rate(cur)), 1),

                           "verbatims": {t: neg[cur_mask(neg) & (neg.themes == t)].drop_duplicates("reviewId")

                                         .sort_values("thumbsUpCount", ascending=False).content.head(8).str[:250].tolist() for t in stats.index[:4]}}

                prompt = ("You are a product analyst for an employee-commute app. Using ONLY this data, write: (1) why the rating is where it is, "

                          "3 bullets with the evidence; (2) the 5 highest-impact actions in order, each with owner, what to do on day 1, and the metric; "

                          "(3) what NOT to spend time on. Do not invent numbers.\n" + json.dumps(payload, default=str))

                with st.spinner("Asking Gemini..."):

                    st.session_state["brief"] = genai.Client(api_key=os.environ["GEMINI_API_KEY"]).models.generate_content(

                        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), contents=prompt).text

            except Exception as e:

                st.error(f"Gemini call failed: {e}")

    if "brief" in st.session_state:

        st.markdown(st.session_state["brief"])

        st.download_button("Download brief (.md)", st.session_state["brief"], "ratings_brief.md")

# =============== TAB 2: TRENDS ===============

with tab2:

    left, right = st.columns([2, 1])

    with left:

        st.subheader("Weekly average rating")

        wk = rev.set_index("at").score.resample("W").agg(["mean", "count"]).tail(52)

        st.line_chart(wk["mean"], y_label="avg stars")

        st.bar_chart(wk["count"], y_label="reviews / week", height=140)

    with right:

        st.subheader("Star distribution")

        dist = pd.DataFrame({f"Last {win}d": cur.score.value_counts(normalize=True),

                             "Prior": pri.score.value_counts(normalize=True)}).reindex([1, 2, 3, 4, 5]).fillna(0) * 100

        st.bar_chart(dist, y_label="% of reviews")

    st.subheader("Themes in 1-2 star reviews")

    t1c, t2c = st.columns(2)

    show = stats[["share_now", "share_prior", "trend", "drag"]].round(2)

    show.columns = ["% of negatives (now)", "% (prior)", "change (pts)", "stars lost"]

    t1c.bar_chart(show["% of negatives (now)"], y_label="% of 1-2 star reviews")

    t2c.dataframe(show, width="stretch")

    st.caption("A review can carry up to two themes, so shares can add to more than 100%.")

    st.subheader("Rating by app version")

    v1, v2 = st.columns(2)

    v1.dataframe(g.round(2), width="stretch")

    v2.bar_chart(g["avg"], y_label="avg stars")

# =============== TAB 3: VERBATIMS ===============

with tab3:

    f1, f2, f3 = st.columns(3)

    theme_pick = f1.selectbox("Theme", ["all"] + list(stats.index))

    max_star = f2.selectbox("Max stars", [1, 2, 3, 4, 5], index=1)

    period = f3.selectbox("Period", [f"Last {win}d", "All time"])

    v = df[df["score"] <= max_star]

    if theme_pick != "all":

        v = v[v["themes"] == theme_pick]

    if period != "All time":

        v = v[cur_mask(v)]

    v = v.drop_duplicates("reviewId").sort_values("thumbsUpCount", ascending=False)

    st.caption(f"{len(v):,} reviews match, most upvoted first. Read these before trusting the AI tags.")

    cols = ["at", "score", "appVersion", "thumbsUpCount", "content"]

    st.dataframe(v[cols].head(200), width="stretch", hide_index=True)

    st.download_button("Download matching reviews (CSV)", v[cols].to_csv(index=False), "reviews_filtered.csv")
