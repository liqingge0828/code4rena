"""Generate a detailed HTML report from code4rena_leaderboard.sqlite3."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dateutil import parser as dateparser


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "code4rena_leaderboard.sqlite3"
OUT_PATH = ROOT / "code4rena_detailed_report.html"


def fmt_usd(value: float) -> str:
    sign = "-" if value < 0 else ""
    n = abs(float(value))
    if n >= 1_000_000_000:
        return f"{sign}${n / 1_000_000_000:.2f}b"
    if n >= 1_000_000:
        return f"{sign}${n / 1_000_000:.2f}m"
    if n >= 1_000:
        return f"{sign}${n / 1_000:.2f}k"
    return f"{sign}${n:.2f}"


def parse_start_time(value: str) -> pd.Timestamp:
    """Parse contest start_time to timezone-naive pandas timestamp."""
    try:
        dt = dateparser.parse(str(value), fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return pd.NaT
    if not dt:
        return pd.NaT
    ts = pd.Timestamp(dt)
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def display_payout_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only display payout column and rename it to payout_usd."""
    out = df.copy()
    if "payout_usd_display" in out.columns:
        out = out.drop(columns=["payout_usd"], errors="ignore")
        out = out.rename(columns={"payout_usd_display": "payout_usd"})
    return out


def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        overview = pd.read_sql_query(
            """
            SELECT
                (SELECT COUNT(1) FROM findings) AS findings_rows,
                (SELECT COUNT(1) FROM contests) AS contest_rows,
                (SELECT COUNT(DISTINCT LOWER(handle)) FROM findings) AS unique_handles,
                (SELECT COUNT(DISTINCT contest) FROM findings) AS contests_with_findings,
                (SELECT ROUND(SUM(COALESCE(awardUSD, 0)), 2) FROM findings) AS total_payout_usd
            """,
            conn,
        ).iloc[0]

        base = pd.read_sql_query(
            """
            SELECT
                LOWER(f.handle) AS handle_norm,
                COALESCE(f.awardUSD, 0) AS award_usd,
                c.start_time
            FROM findings f
            JOIN contests c ON c.contestid = f.contest
            """,
            conn,
        )
        base["contest_start"] = base["start_time"].apply(parse_start_time)
        base = base[base["contest_start"].notna()].copy()
        base["award_usd"] = pd.to_numeric(base["award_usd"], errors="coerce").fillna(0.0)
        base["year"] = base["contest_start"].dt.year.astype(int).astype(str)
        base["month"] = base["contest_start"].dt.to_period("M").astype(str)

        coverage = {
            "first_contest_start": base["contest_start"].min().strftime("%Y-%m-%d"),
            "last_contest_start": base["contest_start"].max().strftime("%Y-%m-%d"),
        }

        yearly = (
            base.groupby("year", as_index=False)
            .agg(
                payout_usd=("award_usd", "sum"),
                findings_count=("award_usd", "size"),
                unique_handles=("handle_norm", "nunique"),
            )
            .sort_values("year")
        )
        yearly["payout_usd"] = yearly["payout_usd"].round(2)

        monthly_recent = (
            base.groupby("month", as_index=False)
            .agg(
                payout_usd=("award_usd", "sum"),
                findings_count=("award_usd", "size"),
                unique_handles=("handle_norm", "nunique"),
            )
            .sort_values("month", ascending=False)
        )
        monthly_recent["payout_usd"] = monthly_recent["payout_usd"].round(2)

        top_handles = pd.read_sql_query(
            """
            SELECT
                LOWER(f.handle) AS handle,
                ROUND(SUM(COALESCE(f.awardUSD, 0)), 2) AS payout_usd,
                COUNT(*) AS findings_count,
                COUNT(DISTINCT f.contest) AS contests_count
            FROM findings f
            GROUP BY LOWER(f.handle)
            ORDER BY payout_usd DESC
            LIMIT 25
            """,
            conn,
        )

        top_sponsors = pd.read_sql_query(
            """
            SELECT
                COALESCE(c.sponsor, 'Unknown') AS sponsor,
                ROUND(SUM(COALESCE(f.awardUSD, 0)), 2) AS payout_usd,
                COUNT(*) AS findings_count,
                COUNT(DISTINCT LOWER(f.handle)) AS unique_handles
            FROM findings f
            JOIN contests c ON c.contestid = f.contest
            GROUP BY COALESCE(c.sponsor, 'Unknown')
            ORDER BY payout_usd DESC
            LIMIT 20
            """,
            conn,
        )

        span = (
            base.groupby("handle_norm", as_index=False)
            .agg(
                first_contest=("contest_start", "min"),
                last_contest=("contest_start", "max"),
                payout_usd=("award_usd", "sum"),
            )
            .sort_values("payout_usd", ascending=False)
            .head(25)
        )
        first_p = span["first_contest"].dt.to_period("M")
        last_p = span["last_contest"].dt.to_period("M")
        span["first_month"] = first_p.astype(str)
        span["last_month"] = last_p.astype(str)
        span["span_months"] = (
            (last_p.dt.year - first_p.dt.year) * 12 + (last_p.dt.month - first_p.dt.month) + 1
        )
        span["payout_usd"] = span["payout_usd"].round(2)
        span = span[["handle_norm", "first_month", "last_month", "span_months", "payout_usd"]]

        severity = pd.read_sql_query(
            """
            SELECT
                SUM(CASE WHEN UPPER(finding) LIKE 'H-%' THEN 1 ELSE 0 END) AS high_count,
                SUM(CASE WHEN UPPER(finding) LIKE 'M-%' OR UPPER(finding) LIKE 'L-%' THEN 1 ELSE 0 END) AS med_low_count,
                SUM(CASE WHEN UPPER(finding) LIKE 'G-%' THEN 1 ELSE 0 END) AS gas_count,
                COUNT(*) AS total_findings
            FROM findings
            """,
            conn,
        ).iloc[0]
    finally:
        conn.close()

    top_10_share = (
        top_handles.head(10)["payout_usd"].sum() / float(overview["total_payout_usd"] or 1) * 100
    )
    top_25_share = (
        top_handles["payout_usd"].sum() / float(overview["total_payout_usd"] or 1) * 100
    )

    for df in (yearly, monthly_recent, top_handles, top_sponsors, span):
        if "payout_usd" in df.columns:
            df["payout_usd_display"] = df["payout_usd"].map(fmt_usd)

    yearly_show = display_payout_only(yearly)
    monthly_recent_show = display_payout_only(monthly_recent)
    top_handles_show = display_payout_only(top_handles)
    top_sponsors_show = display_payout_only(top_sponsors)
    span_show = display_payout_only(span)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Code4rena Public Contest Detailed Report</title>
  <style>
    body {{ font-family: Arial, Helvetica, sans-serif; margin: 28px; line-height: 1.5; color: #111827; }}
    .container {{ max-width: 1024px; margin: 0 auto; }}
    h1, h2 {{ margin: 0.4em 0; }}
    h1 {{ font-size: 28px; }}
    h2 {{ font-size: 20px; margin-top: 28px; }}
    p, li {{ font-size: 14px; }}
    .meta {{ color: #6b7280; margin-bottom: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 10px; margin: 12px 0 18px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; background: #f9fafb; }}
    .k {{ font-size: 12px; color: #6b7280; }}
    .v {{ font-size: 18px; font-weight: 700; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 6px 8px; font-size: 13px; text-align: left; }}
    th {{ background: #f3f4f6; }}
    .note {{ background: #eff6ff; border: 1px solid #bfdbfe; padding: 10px; border-radius: 8px; }}
    .small {{ font-size: 12px; color: #6b7280; }}
  </style>
</head>
<body>
  <div class="container">
  <h1>Code4rena Public Contest Detailed Report</h1>
  <div class="meta">Generated at: {generated_at}</div>

  <h2>1) Executive Summary</h2>
  <ul>
    <li>The report uses public contest finding-level data, with payouts aggregated by <code>awardUSD</code>.</li>
    <li>Payout distribution is strongly top-heavy: Top 10 handles account for about <b>{top_10_share:.2f}%</b> of total payouts.</li>
    <li>The current year is a partial period, so strict year-over-year comparison should be interpreted carefully.</li>
  </ul>

  <h2>2) Dataset Overview</h2>
  <div class="grid">
    <div class="card"><div class="k">Findings Rows</div><div class="v">{int(overview["findings_rows"]):,}</div></div>
    <div class="card"><div class="k">Contest Rows</div><div class="v">{int(overview["contest_rows"]):,}</div></div>
    <div class="card"><div class="k">Unique Handles</div><div class="v">{int(overview["unique_handles"]):,}</div></div>
    <div class="card"><div class="k">Contests with Findings</div><div class="v">{int(overview["contests_with_findings"]):,}</div></div>
    <div class="card"><div class="k">Total Payout (USD)</div><div class="v">{fmt_usd(float(overview["total_payout_usd"] or 0))}</div></div>
  </div>
  <p>Time coverage: <b>{coverage["first_contest_start"]}</b> to <b>{coverage["last_contest_start"]}</b></p>

  <h2>3) Yearly Trend</h2>
  <p class="small">Note: <code>payout_usd_display</code> uses compact notation (k/m/b).</p>
  {yearly_show.to_html(index=False, border=0)}

  <h2>4) Monthly Trend (All Available Months)</h2>
  {monthly_recent_show.to_html(index=False, border=0)}

  <h2>5) Top Handles (By Cumulative Payout)</h2>
  <p>Top 25 handles account for about <b>{top_25_share:.2f}%</b> of total payouts.</p>
  {top_handles_show.to_html(index=False, border=0)}

  <h2>6) Top Sponsors (By Public Contest Payout)</h2>
  {top_sponsors_show.to_html(index=False, border=0)}

  <h2>7) Participation Span (First Month to Last Month) and Payout</h2>
  <p class="small"><code>span_months</code> is inclusive of both first and last month.</p>
  {span_show.to_html(index=False, border=0)}

  <h2>8) Finding Type Distribution</h2>
  <div class="grid">
    <div class="card"><div class="k">High (H-)</div><div class="v">{int(severity["high_count"] or 0):,}</div></div>
    <div class="card"><div class="k">Medium/Low (M-/L-)</div><div class="v">{int(severity["med_low_count"] or 0):,}</div></div>
    <div class="card"><div class="k">Gas (G-)</div><div class="v">{int(severity["gas_count"] or 0):,}</div></div>
    <div class="card"><div class="k">Total Findings</div><div class="v">{int(severity["total_findings"] or 0):,}</div></div>
    <div class="card"><div class="k">High Ratio</div><div class="v">{(float(severity["high_count"] or 0) / float(severity["total_findings"] or 1) * 100):.2f}%</div></div>
  </div>

  <h2>9) Conclusions and Recommendations</h2>
  <div class="note">
    <ul>
      <li>Public contest payouts are highly concentrated; consider talent segmentation using span months, active months, and payout efficiency.</li>
      <li>For total earning-power assessment, private contest data is required; otherwise top performers are likely underestimated.</li>
      <li>Track sponsor-level participation, payout, and high-severity finding mix over time to build an ROI-oriented monitoring view.</li>
    </ul>
  </div>

  <p class="small">Disclaimer: this report is based on the current SQLite snapshot and may differ from final on-chain or project-side settlement figures.</p>
  </div>
</body>
</html>"""

    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Report generated: {OUT_PATH}")


if __name__ == "__main__":
    run()
