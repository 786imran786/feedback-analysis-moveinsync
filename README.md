# MoveInSync Play Store Ratings Monitor

A small system that answers two questions about an app's Play Store ratings: **why are they where they are**, and **what should we fix first**. It also keeps answering them every week without anyone asking.

Built for the MoveInSync Forward Deployed Engineer case study, using the public reviews of the employee app (`com.moveinsync.ets`).

| Part | File | What it does |
| --- | --- | --- |
| Pipeline | `moveinsync_ratings_pipeline.py` | Fetches reviews, tags themes with Gemini, compares periods, writes results |
| Dashboard | `dashboard.py` | Streamlit app: causes, ranked actions by department, trends, review reader |
| Monitor | `moveinsync_ratings_monitor.n8n.json` | n8n workflow: runs weekly, decides if an alert is needed, posts a report to Slack |

## How it works

```
Google Play reviews (public)
        |
        v
Python pipeline  --->  out/ files  --->  Streamlit dashboard (explore)
 fetch, tag, compare   CSV, summary,
        |              metrics.json,
        |              history.csv
        v
  METRICS_JSON line on stdout
        |
        v
n8n workflow (Mondays 9am)  --->  Gemini report  --->  Slack
```

1. **Fetch.** `google-play-scraper` pulls the newest reviews in pages of 200, up to 5,000.
2. **Tag.** Gemini labels each review with 1 to 2 themes: booking failures, login/OTP/SSO, late or no cab, tracking/GPS, route and pickup time, crashes and performance, driver and safety, support. If a Gemini call fails, keyword rules tag the batch and the script prints a warning.
3. **Compare.** The last 90 days against the 90 days before: average rating, 1-2 star share, reply rate, theme shares, rating by app version.
4. **Report.** The dashboard and the Slack report both show the same analysis. The Slack report adds causes, actions per department, and draft replies.

### Rating drag

The dashboard ranks problems by **rating drag**: the stars the average would gain if every review below a target level (default 4) had given that level, with each review's gap split across its themes. It ranks problems; it is an upper bound, not a forecast.

### Alert rules (n8n)

The Slack report is an alert when any of these is true, otherwise it is a short "stable" note:

- the recent average fell by 0.1 stars or more against the prior period
- any theme's share of 1-2 star reviews rose by 3 points or more
- an app version (30+ reviews) rates 0.3 stars or more below the average

Thresholds are set in the workflow's **Config** node.

## Quick start

### Requirements

- Python 3.10 or newer
- A Gemini API key (Google AI Studio). Without one, the pipeline still runs using keyword tagging.
- For the weekly monitor: Node.js and a self-hosted n8n, and a Slack incoming webhook

### 1. Install

```bash
pip install google-play-scraper pandas tabulate google-genai streamlit
```

### 2. Set your key

```bash
# macOS / Linux
export GEMINI_API_KEY=your_key

# Windows (Command Prompt, this window only)
set GEMINI_API_KEY=your_key

# Windows (permanent; reopen the terminal afterwards)
setx GEMINI_API_KEY "your_key"
```

Optional: `GEMINI_MODEL` overrides the model name (default `gemini-3.8-flash`).

### 3. Run the pipeline

```bash
python moveinsync_ratings_pipeline.py
```

A run fetches up to 5,000 reviews and makes about 125 Gemini calls, so it takes a few minutes. Lower `MAX_REVIEWS` at the top of the script to test quickly.

### 4. Open the dashboard

```bash
streamlit run dashboard.py
```

Then open http://localhost:8501. The sidebar button re-runs the pipeline.

### 5. Set up the weekly monitor (optional)

n8n must be **self-hosted**, because the workflow runs a local Python script (Execute Command is not available on n8n Cloud).

```bash
npm install -g n8n
n8n start            # opens http://localhost:5678
```

If the Execute Command node is missing, newer n8n versions disable it by default. Start n8n with it allowed:

```bash
# Windows Command Prompt
set NODES_EXCLUDE=[]
n8n start
```

Then:

1. Import `moveinsync_ratings_monitor.n8n.json` (menu > Import from file).
2. Open the **Config** node and set `project_dir` (folder containing the pipeline, forward slashes), `slack_webhook_url`, `gemini_api_key` and `gemini_model`.
3. Click **Test workflow** once. Check Slack.
4. Switch the workflow to **Active**. It runs every Monday at 9am.

Run n8n on the same machine as the pipeline, with the same Python installed. A Docker install will not find your Python unless you build it in.

## Output files

The pipeline writes to `out/` (created automatically):

| File | Contents |
| --- | --- |
| `reviews_raw.csv` | Every review fetched |
| `reviews_tagged.csv` | Reviews with a theme per row (a review can appear twice) |
| `summary.md` | Plain-text summary of the latest run |
| `metrics.json` | Machine-readable metrics for the latest run |
| `history.csv` | One row appended per run, so trends build up over time |

The pipeline's last line of output is `METRICS_JSON:{...}`. n8n reads that line, so the two tools share one narrow interface.

## Scope and choices

- **One app.** Only `com.moveinsync.ets` (the employee app) is in scope. Driver and field apps are excluded because drivers and field staff rate those, not commuters. Add more app IDs in the `APPS` dictionary in the pipeline.
- **Replies are drafts.** The workflow never posts replies to the Play Store. A person approves first.
- **Fallbacks.** If Gemini fails, keyword rules tag reviews. If Gemini returns something unreadable for the Slack report, the report still posts with the raw text.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `KeyError: 'at'` or no reviews fetched | `pip install -U google-play-scraper`, try another network or toggle your VPN, set `MAX_REVIEWS` to 500 to test |
| Gemini "model not found" or no longer available | Set `GEMINI_MODEL` to a current model, and update `gemini_model` in the n8n Config node |
| `[warn] Gemini tagging failed` in the output | Tags came from keyword rules. Fix the key or model and re-run before trusting the theme ranking |
| Dashboard says "No data yet" | Run the pipeline once from the same folder as `dashboard.py` |
| n8n: "No METRICS_JSON line" | n8n is running an old copy of the pipeline, or `project_dir` is wrong. Run the pipeline by hand and check that its last line starts with `METRICS_JSON:` |
| n8n: Parse metrics input shows only Config | The Execute Command node is missing or disconnected. Allow it (see step 5), re-add it, and wire Config > Run pipeline > Parse metrics |
| Nothing arrives in Slack | Test the webhook with `curl -X POST -H "Content-type: application/json" --data '{"text":"test"}' YOUR_WEBHOOK_URL` |

## Limits

- **Public, scraped data.** Google can change or rate-limit the pages the scraper reads. In production, use the official Google Play Developer API, which also lets you post replies.
- **Review text skews negative** compared with the lifetime store rating. Compare trends over time, not the two absolute numbers.
- **Tags are AI-assisted.** Always read a sample of reviews under each top theme before acting on a ranking. The dashboard's third tab is built for that.
- **Keys in the Config node** are fine for a demo. For real use, move them into n8n credentials and never share an exported workflow with keys filled in.

## Roadmap

- Switch to the Google Play Developer API for complete data and approved replies
- Move secrets into n8n credentials, run n8n as a service
- Create tickets automatically for P1 actions
- Track whether each fix moved its theme's share over the following weeks

## Author

Md Imran Siddiqui
