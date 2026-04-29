"""Streamlit dashboard: Code4rena findings and contests from synced SQLite."""

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dateutil import parser as dateparser

# Plotly: colored bars / markers by magnitude
CHART_COLOR_SCALE = "Tealgrn"


def chart_is_dark() -> bool:
    """Return True when Streamlit theme base is dark (Settings → Theme)."""
    try:
        ctx = getattr(st, "context", None)
        theme = getattr(ctx, "theme", None) if ctx is not None else None
        theme_base = getattr(theme, "base", None) if theme is not None else None
        return theme_base == "dark"
    except (AttributeError, TypeError):
        return False


def apply_chart_colors(figure: go.Figure) -> go.Figure:
    """Apply light or dark Plotly layout to match the Streamlit theme."""
    dark = chart_is_dark()
    if dark:
        figure.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(15, 23, 42, 0.35)",
            plot_bgcolor="rgba(15, 23, 42, 0.72)",
            coloraxis_colorbar=dict(
                thickness=14,
                len=0.55,
                title="",
                tickfont=dict(size=14, color="#e2e8f0"),
            ),
            font=dict(size=16, color="#e2e8f0"),
            hoverlabel=dict(
                font_size=15,
                bgcolor="#1e293b",
                font_color="#f8fafc",
                bordercolor="#334155",
            ),
        )
        grid = "rgba(148, 163, 184, 0.22)"
        figure.update_xaxes(
            tickfont=dict(size=14, color="#cbd5e1"),
            title_font=dict(size=15, color="#e2e8f0"),
            gridcolor=grid,
            zerolinecolor="rgba(148, 163, 184, 0.35)",
        )
        figure.update_yaxes(
            tickfont=dict(size=14, color="#cbd5e1"),
            title_font=dict(size=15, color="#e2e8f0"),
            gridcolor=grid,
            zerolinecolor="rgba(148, 163, 184, 0.35)",
        )
    else:
        figure.update_layout(
            template="plotly_white",
            paper_bgcolor="rgba(248, 250, 252, 0.65)",
            plot_bgcolor="rgba(241, 245, 249, 0.9)",
            coloraxis_colorbar=dict(
                thickness=14,
                len=0.55,
                title="",
                tickfont=dict(size=14),
            ),
            font=dict(size=16),
            hoverlabel=dict(font_size=15),
        )
        figure.update_xaxes(tickfont=dict(size=14), title_font=dict(size=15))
        figure.update_yaxes(tickfont=dict(size=14), title_font=dict(size=15))
    return figure


def positive_max(series: pd.Series, default: float = 1.0) -> float:
    """Largest positive finite value in ``series``, or ``default`` if none."""
    raw_max = series.max()
    try:
        value = float(raw_max)
    except (TypeError, ValueError):
        return default
    if pd.isna(value) or value <= 0:
        return default
    return value


def format_compact(value: float) -> str:
    """Format a number with k / m / b suffixes (two decimal places)."""
    sign = "-" if value < 0 else ""
    n = abs(float(value))
    if n >= 1_000_000_000:
        return f"{sign}{n / 1_000_000_000:.2f}b"
    if n >= 1_000_000:
        return f"{sign}{n / 1_000_000:.2f}m"
    if n >= 1_000:
        return f"{sign}{n / 1_000:.2f}k"
    return f"{sign}{n:.2f}"


def fmt_usd(value: float) -> str:
    """Compact USD string (e.g. ``$1.23k``)."""
    return f"${format_compact(value)}"


def dataframe_pretty(
    df: pd.DataFrame,
    *,
    column_config: dict[str, Any],
    height: int = 420,
    key: str,
) -> None:
    """Render ``df`` with shared table styling (stretch, hide index, column_config)."""
    st.dataframe(
        df,
        width="stretch",
        height=height,
        hide_index=True,
        column_config=column_config,
        key=key,
    )


def parse_start_time(value: str) -> Any:
    """Parse contest start_time to timezone-naive pandas Timestamp."""
    try:
        dt = dateparser.parse(str(value), fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return pd.NaT
    if not dt:
        return pd.NaT
    ts = pd.Timestamp(dt)
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


@st.cache_data(show_spinner=False)
def load_findings(path: str) -> tuple[Any, int, int]:
    """Load findings joined to contests from SQLite; return counts for sidebar."""
    conn = sqlite3.connect(path)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                f.contest,
                f.handle,
                LOWER(f.handle) AS handle_norm,
                f.finding,
                COALESCE(f.award, 0) AS award_native,
                f.awardCoin,
                COALESCE(f.awardUSD, 0) AS award_usd,
                c.contestid,
                c.title AS contest_title,
                c.sponsor AS contest_sponsor,
                c.repo
            FROM findings f
            JOIN contests c ON c.contestid = f.contest
            """,
            conn,
        )
        cur = conn.cursor()
        num_findings = int(cur.execute("SELECT COUNT(1) FROM findings").fetchone()[0])
        num_contests = int(cur.execute("SELECT COUNT(1) FROM contests").fetchone()[0])
    finally:
        conn.close()

    nat = pd.to_numeric(df["award_native"], errors="coerce").fillna(0.0)
    df["award_native"] = nat  # pyright: ignore[reportAttributeAccessIssue]
    usd = pd.to_numeric(df["award_usd"], errors="coerce").fillna(0.0)
    df["award_usd"] = usd  # pyright: ignore[reportAttributeAccessIssue]
    return df, num_findings, num_contests


@st.cache_data(show_spinner=False)
def load_contest_dates(path: str) -> Any:
    """Load contest id and parsed start_time for filtering and charts."""
    conn = sqlite3.connect(path)
    try:
        contests = pd.read_sql_query(
            "SELECT contestid, start_time FROM contests",
            conn,
        )
    finally:
        conn.close()
    contests["contest_start"] = contests["start_time"].apply(parse_start_time)
    return contests[["contestid", "contest_start"]]


def severity_flags(fid: str) -> tuple[int, int, int]:
    """Return (high, med_low, gas) indicator tuple for a finding id prefix."""
    fid = str(fid or "").upper()
    high = 1 if fid.startswith("H-") else 0
    med = 1 if fid.startswith("M-") or fid.startswith("L-") else 0
    gas = 1 if fid.startswith("G-") else 0
    return high, med, gas


# Top-level Streamlit widgets use many locals; names are not UPPER_CASE constants.
# pylint: disable=invalid-name
st.set_page_config(page_title="Code4rena DB Viewer", layout="wide")
st.markdown(
    """
    <style>
    html { font-size: 18px; }
    .block-container {
      padding-top: 1.2rem;
      max-width: 100%;
    }
    .stApp {
      font-size: 1.05rem;
    }
    h1 { font-size: 2.35rem !important; font-weight: 700 !important; }
    h2 { font-size: 1.65rem !important; }
    h3 { font-size: 1.35rem !important; }
    div[data-testid="stMetricLabel"] { font-size: 1.05rem !important; }
    div[data-testid="stMetricValue"] {
      font-size: 2.15rem !important;
      color: #0d9488;
    }
    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span {
      font-size: 1.02rem !important;
    }
    [data-baseweb="tab"] { font-size: 1.05rem !important; }
    div[data-testid="stSelectbox"] label,
    div[data-testid="stSlider"] label,
    div[data-testid="stMultiSelect"] label,
    div[data-testid="stTextInput"] label,
    div[data-testid="stDateInput"] label {
      font-size: 1.02rem !important;
    }
    div[data-testid="stCaption"] { font-size: 0.95rem !important; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Code4rena SQLite Visualizer")

default_db = Path(__file__).with_name("code4rena_leaderboard.sqlite3")
db_path = st.sidebar.text_input("SQLite file path", str(default_db))
top_n = st.sidebar.slider("Top N", min_value=5, max_value=100, value=20, step=5)

db_file = Path(db_path)
if not db_file.exists():
    st.error(f"Database not found: {db_file}")
    st.stop()

raw_df, n_findings, n_contests = load_findings(str(db_file))
dates_df = load_contest_dates(str(db_file))
raw_df = raw_df.merge(dates_df, left_on="contest", right_on="contestid", how="left")
raw_df["awardCoin"] = raw_df["awardCoin"].fillna("USD")

min_date = raw_df["contest_start"].min()
max_date = raw_df["contest_start"].max()

st.sidebar.caption(f"Rows: findings={n_findings:,}, contests={n_contests:,}")
date_range = st.sidebar.date_input(
    "Contest start date range",
    value=(
        min_date.date() if pd.notna(min_date) else None,
        max_date.date() if pd.notna(max_date) else None,
    ),
)

filtered = raw_df.copy()
if isinstance(date_range, tuple) and len(date_range) == 2 and all(date_range):
    start_date = pd.to_datetime(date_range[0])
    end_date = pd.to_datetime(date_range[1])
    filtered = filtered[
        (filtered["contest_start"] >= start_date)
        & (filtered["contest_start"] <= end_date)
    ]

sponsors = sorted(
    x for x in filtered["contest_sponsor"].dropna().unique().tolist() if x
)
selected_sponsors = st.sidebar.multiselect("Sponsors", sponsors)
if selected_sponsors:
    filtered = filtered[filtered["contest_sponsor"].isin(selected_sponsors)]

handles = sorted(x for x in filtered["handle_norm"].dropna().unique().tolist() if x)
selected_handles = st.sidebar.multiselect("Handles", handles)
if selected_handles:
    filtered = filtered[filtered["handle_norm"].isin(selected_handles)]

coins = sorted(x for x in filtered["awardCoin"].dropna().unique().tolist() if x)
selected_coins = st.sidebar.multiselect("Coins", coins)
if selected_coins:
    filtered = filtered[filtered["awardCoin"].isin(selected_coins)]

if filtered.empty:
    st.warning("No rows after current filters.")
    st.stop()

flags = filtered["finding"].apply(severity_flags)
filtered["high_all"] = flags.map(lambda x: x[0])
filtered["med_all"] = flags.map(lambda x: x[1])
filtered["gas_all"] = flags.map(lambda x: x[2])

agg = filtered.groupby(
    [
        "contest",
        "contest_title",
        "contest_sponsor",
        "repo",
        "contest_start",
        "handle",
        "handle_norm",
    ],
    as_index=False,
).agg(
    native_amount=("award_native", "sum"),
    usd_amount=("award_usd", "sum"),
    total_reports=("finding", "count"),
    high_all=("high_all", "sum"),
    med_all=("med_all", "sum"),
    gas_all=("gas_all", "sum"),
    coins=("awardCoin", list),
)
agg["contest_report_repo"] = agg["repo"].fillna("")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Rows", f"{len(agg):,}")
col2.metric("Unique handles", f"{agg['handle_norm'].nunique():,}")
col3.metric("Unique contests", f"{agg['contest'].nunique():,}")
col4.metric("Total payouts (USD)", fmt_usd(agg["usd_amount"].sum()))

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "Top Handles",
        "Contest Breakdown",
        "Data Table",
        "Monthly Income",
        "Yearly Income",
    ]
)

with tab1:
    st.subheader("Top handles by total payout (USD)")
    by_handle = (
        agg.groupby("handle_norm", as_index=False)["usd_amount"]
        .sum()
        .sort_values("usd_amount", ascending=False)
        .head(top_n)
    )
    fig = px.bar(
        by_handle,
        x="handle_norm",
        y="usd_amount",
        labels={"usd_amount": "USD"},
        color="usd_amount",
        color_continuous_scale=CHART_COLOR_SCALE,
    )
    fig.update_yaxes(tickformat="~s")
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    apply_chart_colors(fig)
    st.plotly_chart(fig, width="stretch", key="chart_top_handles")
    payout_max = positive_max(by_handle["usd_amount"])
    show = pd.DataFrame(
        {
            "Handle": by_handle["handle_norm"],
            "Share of max": (
                by_handle["usd_amount"] / payout_max * 100.0
            ).clip(0.0, 100.0),
            "Payout (USD)": by_handle["usd_amount"].map(fmt_usd),
        }
    )
    dataframe_pretty(
        show,
        column_config={
            "Handle": st.column_config.TextColumn("Handle", width="medium"),
            "Share of max": st.column_config.ProgressColumn(
                "Share of max",
                min_value=0,
                max_value=100,
                format="%.0f%%",
                help="Payout vs the largest row in this table (colored bar)",
                width="medium",
            ),
            "Payout (USD)": st.column_config.TextColumn("Payout (USD)", width="small"),
        },
        key="df_top_handles",
    )

with tab2:
    st.subheader("Contest payouts and participation")
    by_contest = (
        agg.groupby(["contest", "contest_title", "contest_sponsor"], as_index=False)
        .agg(
            total_payout_usd=("usd_amount", "sum"),
            unique_handles=("handle_norm", "nunique"),
        )
        .sort_values("total_payout_usd", ascending=False)
        .head(top_n)
    )
    fig = px.scatter(
        by_contest,
        x="unique_handles",
        y="total_payout_usd",
        hover_data=["contest", "contest_title", "contest_sponsor"],
        labels={"total_payout_usd": "USD"},
        color="total_payout_usd",
        color_continuous_scale="Viridis",
    )
    fig.update_yaxes(tickformat="~s")
    scatter_outline = "rgba(15,23,42,0.78)" if chart_is_dark() else "white"
    fig.update_traces(
        marker=dict(size=12, line=dict(width=0.4, color=scatter_outline))
    )
    apply_chart_colors(fig)
    st.plotly_chart(fig, width="stretch", key="chart_contest_breakdown")
    contest_payout_max = positive_max(by_contest["total_payout_usd"])
    show = pd.DataFrame(
        {
            "Contest ID": by_contest["contest"].astype(str),
            "Contest": by_contest["contest_title"],
            "Sponsor": by_contest["contest_sponsor"],
            "Wardens": by_contest["unique_handles"].astype(int),
            "Share of max": (
                by_contest["total_payout_usd"] / contest_payout_max * 100.0
            ).clip(0.0, 100.0),
            "Payout (USD)": by_contest["total_payout_usd"].map(fmt_usd),
        }
    )
    dataframe_pretty(
        show,
        column_config={
            "Contest ID": st.column_config.TextColumn("Contest ID", width="small"),
            "Contest": st.column_config.TextColumn("Contest", width="large"),
            "Sponsor": st.column_config.TextColumn("Sponsor", width="medium"),
            "Wardens": st.column_config.NumberColumn(
                "Wardens",
                width="small",
                format="%d",
                help="Distinct handles in this contest",
            ),
            "Share of max": st.column_config.ProgressColumn(
                "Share of max",
                min_value=0,
                max_value=100,
                format="%.0f%%",
                help="Payout vs the largest row in this table",
                width="small",
            ),
            "Payout (USD)": st.column_config.TextColumn("Payout (USD)", width="small"),
        },
        key="df_contest_breakdown",
    )

with tab3:
    st.subheader("Per handle per contest payouts")
    show_cols = [
        "contest",
        "contest_title",
        "contest_sponsor",
        "contest_start",
        "handle",
        "usd_amount",
        "total_reports",
        "high_all",
        "med_all",
        "gas_all",
    ]
    agg_slice = agg[show_cols].sort_values(
        ["contest_start", "usd_amount"], ascending=[False, False]
    )
    starts = pd.to_datetime(agg_slice["contest_start"], errors="coerce")
    table_payout_max = positive_max(agg_slice["usd_amount"])
    rep_max = int(agg_slice["total_reports"].fillna(0).max()) or 1
    h_max = int(agg_slice["high_all"].fillna(0).max()) or 1
    ml_max = int(agg_slice["med_all"].fillna(0).max()) or 1
    g_max = int(agg_slice["gas_all"].fillna(0).max()) or 1
    show = pd.DataFrame(
        {
            "Contest ID": agg_slice["contest"].astype(str),
            "Contest": agg_slice["contest_title"],
            "Sponsor": agg_slice["contest_sponsor"],
            "Start": starts,
            "Handle": agg_slice["handle"],
            "Share of max": (
                agg_slice["usd_amount"].astype(float) / table_payout_max * 100.0
            ).clip(0.0, 100.0),
            "Payout (USD)": agg_slice["usd_amount"].map(fmt_usd),
            "Reports": agg_slice["total_reports"].fillna(0).astype(int),
            "High": agg_slice["high_all"].fillna(0).astype(int),
            "Med/Low": agg_slice["med_all"].fillna(0).astype(int),
            "Gas": agg_slice["gas_all"].fillna(0).astype(int),
        }
    )
    dataframe_pretty(
        show,
        column_config={
            "Contest ID": st.column_config.TextColumn("Contest ID", width="small"),
            "Contest": st.column_config.TextColumn("Contest", width="large"),
            "Sponsor": st.column_config.TextColumn("Sponsor", width="medium"),
            "Start": st.column_config.DatetimeColumn(
                "Start", width="small", format="YYYY-MM-DD"
            ),
            "Handle": st.column_config.TextColumn("Handle", width="medium"),
            "Share of max": st.column_config.ProgressColumn(
                "Share of max",
                min_value=0,
                max_value=100,
                format="%.0f%%",
                help="Payout vs the largest row in this table",
                width="small",
            ),
            "Payout (USD)": st.column_config.TextColumn("Payout (USD)", width="small"),
            "Reports": st.column_config.ProgressColumn(
                "Reports",
                min_value=0,
                max_value=rep_max,
                format="%d",
                help="Finding count (bar vs max in table)",
                width="small",
            ),
            "High": st.column_config.ProgressColumn(
                "High",
                min_value=0,
                max_value=h_max,
                format="%d",
                help="H- count (colored bar)",
                width="small",
            ),
            "Med/Low": st.column_config.ProgressColumn(
                "Med/Low",
                min_value=0,
                max_value=ml_max,
                format="%d",
                help="M- / L- count",
                width="small",
            ),
            "Gas": st.column_config.ProgressColumn(
                "Gas",
                min_value=0,
                max_value=g_max,
                format="%d",
                help="G- count",
                width="small",
            ),
        },
        height=480,
        key="df_per_handle_contest",
    )

with tab4:
    st.subheader("Handle income by month (USD)")
    agg_monthly = agg.copy()
    agg_monthly["month"] = agg_monthly["contest_start"].dt.to_period("M").astype(str)
    monthly_income = (
        agg_monthly.groupby(["handle_norm", "month"], as_index=False)["usd_amount"]
        .sum()
        .sort_values(["month", "usd_amount"], ascending=[False, False])
    )
    months = sorted(monthly_income["month"].unique().tolist(), reverse=True)
    selected_month = st.selectbox("Select month", months, index=0)
    top = (
        monthly_income[monthly_income["month"] == selected_month]
        .sort_values("usd_amount", ascending=False)
        .head(top_n)
    )
    month_payout_max = positive_max(top["usd_amount"])
    show = pd.DataFrame(
        {
            "Handle": top["handle_norm"],
            "Month": top["month"],
            "Share of max": (
                top["usd_amount"] / month_payout_max * 100.0
            ).clip(0.0, 100.0),
            "Payout (USD)": top["usd_amount"].map(fmt_usd),
        }
    )
    dataframe_pretty(
        show,
        column_config={
            "Handle": st.column_config.TextColumn("Handle", width="medium"),
            "Month": st.column_config.TextColumn("Month", width="small"),
            "Share of max": st.column_config.ProgressColumn(
                "Share of max",
                min_value=0,
                max_value=100,
                format="%.0f%%",
                help="Vs largest payout in this month list",
                width="small",
            ),
            "Payout (USD)": st.column_config.TextColumn("Payout (USD)", width="small"),
        },
        key="df_monthly_income",
    )
    fig = px.bar(
        top,
        x="handle_norm",
        y="usd_amount",
        labels={"usd_amount": "USD"},
        color="usd_amount",
        color_continuous_scale=CHART_COLOR_SCALE,
    )
    fig.update_yaxes(tickformat="~s")
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    apply_chart_colors(fig)
    st.plotly_chart(fig, width="stretch", key=f"chart_monthly_top_{selected_month}")

with tab5:
    st.subheader("Handle income by year (USD)")
    y = agg.copy()
    y["year"] = y["contest_start"].dt.year.astype(str)
    yearly_income = (
        y.groupby(["handle_norm", "year"], as_index=False)["usd_amount"]
        .sum()
        .sort_values(["year", "usd_amount"], ascending=[False, False])
    )
    years = sorted(yearly_income["year"].unique().tolist(), reverse=True)
    selected_year = st.selectbox("Select year", years, index=0)
    top = (
        yearly_income[yearly_income["year"] == selected_year]
        .sort_values("usd_amount", ascending=False)
        .head(top_n)
    )
    year_payout_max = positive_max(top["usd_amount"])
    show = pd.DataFrame(
        {
            "Handle": top["handle_norm"],
            "Year": top["year"],
            "Share of max": (
                top["usd_amount"] / year_payout_max * 100.0
            ).clip(0.0, 100.0),
            "Payout (USD)": top["usd_amount"].map(fmt_usd),
        }
    )
    dataframe_pretty(
        show,
        column_config={
            "Handle": st.column_config.TextColumn("Handle", width="medium"),
            "Year": st.column_config.TextColumn("Year", width="small"),
            "Share of max": st.column_config.ProgressColumn(
                "Share of max",
                min_value=0,
                max_value=100,
                format="%.0f%%",
                help="Vs largest payout in this year list",
                width="small",
            ),
            "Payout (USD)": st.column_config.TextColumn("Payout (USD)", width="small"),
        },
        key="df_yearly_income",
    )
    fig = px.bar(
        top,
        x="handle_norm",
        y="usd_amount",
        labels={"usd_amount": "USD"},
        color="usd_amount",
        color_continuous_scale=CHART_COLOR_SCALE,
    )
    fig.update_yaxes(tickformat="~s")
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    apply_chart_colors(fig)
    st.plotly_chart(fig, width="stretch", key=f"chart_yearly_top_{selected_year}")
