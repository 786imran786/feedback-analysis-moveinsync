"""

MoveInSync Play Store ratings pipeline  (re-runnable: `python moveinsync_ratings_pipeline.py`)

Setup:   pip install google-play-scraper pandas

Optional: pip install google-genai  + set GEMINI_API_KEY for LLM theme tagging (else keyword fallback)

Outputs (./out/):

  reviews_raw.csv       every review fetched

  reviews_tagged.csv    + theme, severity

  summary.md            the "How are our ratings?" answer: current vs prior period, themes, top issues

"""

import datetime as dt
import json
import os
import re
import sys
import time

import pandas as pd

from google_play_scraper import search, app, reviews, Sort

try: sys.stdout.reconfigure(encoding="utf-8")        # safe when n8n pipes stdout on Windows

except Exception: pass

os.chdir(os.path.dirname(os.path.abspath(__file__)))  # always run from the script's folder

OUT = "out"; os.makedirs(OUT, exist_ok=True)

MAX_REVIEWS = 5000           # per app

LOOKBACK_DAYS = 90           # "current" window; prior window = the 90 days before

# 1. Discover apps (verify the list manually: employee app vs driver app vs shuttle app).

#    Driver apps get rated by drivers, so keep them separate or exclude them.

#    Discovery by search is noisy (returns ad results with no appId, driver apps, other products),

#    so the in-scope list is explicit. Add more IDs here if you decide they belong in scope.

APPS = {

    "com.moveinsync.ets": "MoveInSync (employee app)",   # the app the case study is about

    # "com.workinsync.meeting": "MoveInSync Workplace",   # different product, uncomment to include

    # "com.moveinsync.publictransit": "TransitInSync",    # different product, uncomment to include

}

# Excluded on purpose (rated by drivers/field staff, not employee commuters):

#   com.moveinsync.driver.dco, com.moveinsync.driver.lite, field.moveinsync.ets

# For reference, a clean search that skips ad results (appId None):

def discover(query="MoveInSync"):

    return {r["appId"]: r["title"] for r in search(query, n_hits=10, country="in", lang="en") if r.get("appId")}

print("In-scope apps:", APPS)

# 2. Themes mapped to the employee journey in the brief

THEMES = {

    "booking_fails":    r"book|booking|schedule|roster|slot|seat|cancel|no cab available|not able to (book|schedule)",

    "login_otp_sso":    r"login|log in|sign in|otp|sso|password|verify|verification code|authentic",

    "late_or_no_cab":   r"late|delay|waiting|no show|not come|didn.?t come|never came|wait(ed)? for|driver (cancel|not)",

    "tracking_gps":     r"track|gps|location|live|map|eta|wrong (location|pickup)",

    "route_pickup_time":r"route|pickup time|pick.?up|detour|long route|too early|changed time",

    "app_crash_perf":   r"crash|bug|hang|freez|slow|loading|not (working|opening|responding)|glitch|error|update",

    "driver_safety":    r"driver|rude|behav|unsafe|safety|sos|harass|rash|drunk|panic",

    "support_resolution":r"support|complaint|no response|helpline|ticket|nobody|not resolved|customer care",

}

def keyword_tag(text):

    t = (text or "").lower()

    hits = [k for k, p in THEMES.items() if re.search(p, t)]

    return hits or ["other"]

_warned = False

def llm_tag(batch):

    """Tag a batch of reviews with themes via Gemini; falls back to keywords on any failure."""

    try:

        from google import genai

        from google.genai import types

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

        prompt = (

            "Tag each employee-commute app review with 1-2 themes from "

            f"{list(THEMES) + ['praise', 'other']}. Reply ONLY JSON: {{\"<id>\": [\"theme\", ...]}}.\n"

            + json.dumps({str(i): txt[:400] for i, txt in batch})

        )

        r = client.models.generate_content(

            model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),

            contents=prompt,

            config=types.GenerateContentConfig(response_mime_type="application/json"),

        )

        out = {int(k): v for k, v in json.loads(r.text).items()}

        # any review the model skipped gets keyword tags

        return {i: out.get(i) or keyword_tag(t) for i, t in batch}

    except Exception as e:

        global _warned

        if not _warned:                      # say so once, instead of silently switching to keyword tags

            print(f"\n[warn] Gemini tagging failed ({str(e)[:200]}); using keyword tags instead.")

            _warned = True

        return {i: keyword_tag(t) for i, t in batch}

# 3. Fetch (small pages with a continuation token; more reliable than one 5,000-review request)

def fetch_reviews(app_id, total=MAX_REVIEWS, page=200):

    out, token = [], None

    for sort in (Sort.NEWEST, Sort.MOST_RELEVANT):          # fall back to another sort if NEWEST returns nothing

        while len(out) < total:

            try:

                batch, token = reviews(app_id, lang="en", country="in", sort=sort,

                                       count=page, continuation_token=token)

            except Exception as e:

                print(f"  fetch error after {len(out)} reviews: {e}")

                break

            if not batch:

                break

            out += batch

            print(f"  fetched {len(out)} reviews", end="\r")

            if token is None:

                break

            time.sleep(0.5)                                  # be polite, avoid rate limits

        if out:

            break

        token = None

    print()

    return out[:total]

frames = []

for app_id, title in APPS.items():

    try:

        meta = app(app_id, country="in", lang="en")

    except Exception as e:

        print(f"Skipping {app_id}: {e}")

        continue

    print(f"{title}: store rating {meta['score']:.2f} ({meta['ratings']:,} ratings)")

    revs = fetch_reviews(app_id)

    if not revs:

        print(f"No reviews returned for {app_id}.")

        continue

    df = pd.DataFrame(revs); df["app"] = title; df["app_id"] = app_id

    df["store_score"] = meta["score"]

    frames.append(df)

if not frames:

    sys.exit("\nNo reviews could be fetched. Try: (1) pip install -U google-play-scraper, "

             "(2) a different network or turn VPN off/on, (3) lower MAX_REVIEWS to 500 and retry.")

df = pd.concat(frames, ignore_index=True)

df["at"] = pd.to_datetime(df["at"])

df.to_csv(f"{OUT}/reviews_raw.csv", index=False)

# 4. Tag themes

df["content"] = df["content"].fillna("")

idx_text = list(enumerate(df["content"]))

tags = {}

use_llm = bool(os.getenv("GEMINI_API_KEY"))

for s in range(0, len(idx_text), 40):

    b = idx_text[s:s+40]

    tags.update(llm_tag(b) if use_llm else {i: keyword_tag(t) for i, t in b})

df["themes"] = df.index.map(lambda i: tags.get(i, ["other"]))

df = df.explode("themes")

df.to_csv(f"{OUT}/reviews_tagged.csv", index=False)

# 5. Summary: current vs prior window, 1-2 star share by theme, by app version

now = df["at"].max()

cur = df[df["at"] > now - pd.Timedelta(days=LOOKBACK_DAYS)]

pri = df[(df["at"] <= now - pd.Timedelta(days=LOOKBACK_DAYS)) &

         (df["at"] > now - pd.Timedelta(days=2*LOOKBACK_DAYS))]

def avg(d): return d.drop_duplicates("reviewId")["score"].mean()

lines = [f"# Play Store ratings summary ({dt.date.today()})\n"]

for a in df["app"].unique():

    c, p = cur[cur.app == a], pri[pri.app == a]

    lines.append(f"- **{a}**: last {LOOKBACK_DAYS}d avg {avg(c):.2f} (n={c.reviewId.nunique()}) "

                 f"vs prior {avg(p):.2f} (n={p.reviewId.nunique()}); store rating {df[df.app==a].store_score.iloc[0]:.2f}")

neg = cur[cur["score"] <= 2]

t = (neg.groupby("themes").reviewId.nunique().sort_values(ascending=False))

lines.append("\n## What 1-2 star reviews complain about (last window, % of negative reviews)\n")

tot = neg.reviewId.nunique() or 1

for k, v in t.items():

    lines.append(f"- {k}: {v} ({v/tot:.0%})")

lines.append("\n## Rating by app version (versions with >=30 reviews)\n")

g = df.drop_duplicates("reviewId").groupby(["app", "appVersion"]).score.agg(["mean", "count"])

lines.append(g[g["count"] >= 30].sort_values("mean").head(10).round(2).to_markdown())

lines.append("\n## Example verbatims per top theme (read these before trusting the tags)\n")

for k in t.index[:5]:

    for txt in neg[neg.themes == k].sort_values("thumbsUpCount", ascending=False).content.head(3):

        lines.append(f"- [{k}] {txt[:220]!r}")

open(f"{OUT}/summary.md", "w").write("\n".join(lines))

print("\n".join(lines))

# 6. Machine-readable output for monitoring (n8n reads the METRICS_JSON line from stdout)

def _nz(x):

    return None if pd.isna(x) else round(float(x), 2)

def _share(d):

    n = d.reviewId.nunique() or 1

    return (d.groupby("themes").reviewId.nunique() / n * 100).round(1).to_dict()

cu, pr = cur.drop_duplicates("reviewId"), pri.drop_duplicates("reviewId")

sh_now, sh_pri = _share(neg), _share(pri[pri["score"] <= 2])

_themes = [{"theme": k, "share_now": v, "share_prior": sh_pri.get(k, 0.0), "change": round(v - sh_pri.get(k, 0.0), 1)}

           for k, v in sorted(sh_now.items(), key=lambda kv: -kv[1]) if k not in ("other", "praise")][:6]

_vers = df.drop_duplicates("reviewId").groupby("appVersion").score.agg(["mean", "count"])

_vers = _vers[_vers["count"] >= 30].sort_values("mean").head(3)

_neg1 = neg.drop_duplicates("reviewId")

_unrep = _neg1[_neg1["replyContent"].isna()].sort_values("thumbsUpCount", ascending=False).head(3)

metrics = {

    "date": str(dt.date.today()),

    "store_rating": _nz(df["store_score"].iloc[0]),

    "cur_avg": _nz(cu["score"].mean()), "prior_avg": _nz(pr["score"].mean()),

    "cur_reviews": int(len(cu)),

    "neg_share_cur": _nz((cu["score"] <= 2).mean() * 100),

    "neg_share_prior": _nz((pr["score"] <= 2).mean() * 100),

    "reply_rate": _nz(cu["replyContent"].notna().mean() * 100),

    "themes": _themes,

    "worst_versions": [{"version": str(i), "avg": round(float(r["mean"]), 2), "reviews": int(r["count"])} for i, r in _vers.iterrows()],

    "verbatims": {x["theme"]: neg[neg.themes == x["theme"]].drop_duplicates("reviewId")

                  .sort_values("thumbsUpCount", ascending=False).content.head(3).str[:200].tolist() for x in _themes[:3]},

    "unreplied_negatives": [{"id": r.reviewId, "score": int(r.score), "text": r.content[:250]} for r in _unrep.itertuples()],

}

_hist = f"{OUT}/history.csv"          # one row per run, so the trend builds up over time

pd.DataFrame([{k: metrics[k] for k in ("date", "store_rating", "cur_avg", "prior_avg", "cur_reviews", "neg_share_cur", "reply_rate")}]

             ).to_csv(_hist, mode="a", header=not os.path.exists(_hist), index=False)

json.dump(metrics, open(f"{OUT}/metrics.json", "w"), indent=2)

print("METRICS_JSON:" + json.dumps(metrics, default=str))
