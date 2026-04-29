# Code4rena Leaderboard Sync

Download **official** Code4rena data (hosted on `code4rena.com`, documented in the [awarding process](https://docs.code4rena.com/awarding/awarding-process)) and store it in a local SQLite file.

## Data source (官网)

- `https://code4rena.com/community-resources/findings.csv` — every finding with `awardUSD` etc.
- `https://code4rena.com/community-resources/contests.csv` — contest metadata (dates, sponsor, repo links)

These are the same exports the docs refer to for reproducing awards; they are **not** scraped from random GitHub mirrors.

## Setup

```bash
poetry install
```

## Run

```bash
poetry run python sync_c4.py --db-path code4rena_leaderboard.sqlite3 --min-year 2020
```

`--min-year` only affects the **aggregated** tables `contest_results` and `leaderboard_snapshots`. Raw rows in `contests` and `findings` are always the full official CSVs.

## What gets stored

- `contests`: full `contests.csv` (官网)
- `findings`: full `findings.csv`（每条 finding + `awardUSD`）
- `contest_results`: 按 `(contest, handle)` 聚合后的奖金与条数（便于和旧版 Streamlit 一致）
- `leaderboard_snapshots`: 由官方 findings 汇总的「全时期」每位 warden 总 `awardUSD`（`timeframe = all_time_official`）
- `import_meta`: 导入时间与官方 URL

## Visualize with Streamlit

```bash
poetry run streamlit run streamlit_app.py
```

If your database is in a different path, set it in the Streamlit sidebar.

## Deploy

### Vercel (static landing page only)

Streamlit is a long-lived Python app with WebSockets and (typically) a local SQLite file. **Vercel’s serverless model is not a good fit** for running `streamlit_app.py` directly.

This repo includes a small static site under **`vercel-site/`** (single `index.html` + `vercel.json`) you can host on Vercel:

1. In [Vercel](https://vercel.com), **Import** your GitLab project `liqingge/code4rena`.
2. Open **Project → Settings → General → Root Directory**, set it to **`vercel-site`**, save.
3. Deploy (default framework detection should serve the static `index.html`).

If you import the repo **without** changing the root directory, Vercel may try to treat the Python/poetry layout as a deploy target and behave unexpectedly—always set **Root Directory** to `vercel-site` for this static page.

### Streamlit app (recommended)

Use **[Streamlit Community Cloud](https://share.streamlit.io/)** (GitLab sign-in): point the main file to `streamlit_app.py`, match Python 3.12+, and install dependencies from `pyproject.toml` / lockfile as documented there. You still need a **database strategy** on the host (e.g. bake a small read-only SQLite into the image, download CSVs at startup, or attach managed storage—Community Cloud’s free tier is ephemeral, so plan sync accordingly).

## Useful SQL

明细（每条 finding）：

```sql
SELECT f.*, c.title, c.sponsor
FROM findings f
LEFT JOIN contests c ON c.contestid = f.contest
WHERE f.handle = 'cmichel'
LIMIT 50;
```

某场比赛谁拿了多少（聚合表）：

```sql
SELECT contest_sponsor, handle, prize_money_usd
FROM contest_results
ORDER BY start_date DESC, prize_money_usd DESC;
```

全时期总收益（与 `leaderboard_snapshots` 一致）：

```sql
SELECT handle_norm, SUM(prize_money_usd) AS total_usd
FROM contest_results
GROUP BY handle_norm
ORDER BY total_usd DESC;
```
