import sqlite3
import pandas as pd
from dateutil import parser as dateparser


conn = sqlite3.connect("code4rena_leaderboard.sqlite3")
df = pd.read_sql_query(
    """
    SELECT
        LOWER(f.handle) AS handle_norm,
        c.start_time,
        COALESCE(f.awardUSD, 0) AS award_usd
    FROM findings f
    JOIN contests c ON c.contestid = f.contest
    """,
    conn,
)
conn.close()

df["contest_start"] = df["start_time"].apply(
    lambda x: dateparser.parse(str(x), fuzzy=True) if pd.notna(x) else pd.NaT
)
df = df[df["contest_start"].notna()].copy()
df["month"] = df["contest_start"].dt.to_period("M").astype(str)
df["year"] = df["contest_start"].dt.year.astype(str)

monthly = (
    df.groupby(["handle_norm", "month"], as_index=False)["award_usd"]
    .sum()
    .sort_values(["month", "award_usd"], ascending=[False, False])
)
monthly.to_csv("monthly_income_by_handle.csv", index=False)

yearly = (
    df.groupby(["handle_norm", "year"], as_index=False)["award_usd"]
    .sum()
    .sort_values(["year", "award_usd"], ascending=[False, False])
)
yearly.to_csv("yearly_income_by_handle.csv", index=False)

latest_month = monthly["month"].max()
latest_top = (
    monthly[monthly["month"] == latest_month]
    .sort_values("award_usd", ascending=False)
    .head(15)
)

print(f"monthly rows: {len(monthly)}")
print(f"latest month: {latest_month}")
print(latest_top.to_string(index=False))
print(f"yearly rows: {len(yearly)}")
