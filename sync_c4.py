"""
Sync Code4rena data from official website community CSVs into SQLite.

Primary sources (documented in https://docs.code4rena.com/awarding/awarding-process):
  - https://code4rena.com/community-resources/findings.csv
  - https://code4rena.com/community-resources/contests.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests
from dateutil import parser as dateparser

OFFICIAL_FINDINGS_URL = "https://code4rena.com/community-resources/findings.csv"
OFFICIAL_CONTESTS_URL = "https://code4rena.com/community-resources/contests.csv"


def _fetch_csv_text(url: str, timeout: int = 180) -> str:
    last_err: Optional[Exception] = None
    for _ in range(3):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.content.decode("utf-8-sig", errors="replace")
        except requests.RequestException as err:
            last_err = err
    if last_err:
        raise last_err
    raise RuntimeError("unreachable")


def _parse_date(value: str) -> Optional[str]:
    if not value or not str(value).strip():
        return None
    try:
        dt = dateparser.parse(str(value), fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    if not dt:
        return None
    return dt.date().isoformat()


def _to_float(value: str) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(Decimal(s.replace(",", "")))
    except (InvalidOperation, ValueError):
        return None


def _extract_pool_usd(amount_field: str) -> Optional[float]:
    if not amount_field:
        return None
    m = re.search(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)", str(amount_field))
    if not m:
        return None
    return _to_float(m.group(1))


def _repo_slug(repo_url: str) -> Optional[str]:
    if not repo_url:
        return None
    m = re.search(r"github\.com/code-423n4/([^/#?]+)", repo_url, flags=re.I)
    if m:
        return m.group(1)
    tail = repo_url.rstrip("/").split("/")[-1]
    return tail or None


def _severity_counts(finding_id: str) -> tuple[int, int, int]:
    fid = (finding_id or "").strip().upper()
    high = med = gas = 0
    if fid.startswith("H-"):
        high = 1
    elif fid.startswith("M-") or fid.startswith("L-"):
        med = 1
    elif fid.startswith("G-"):
        gas = 1
    return high, med, gas


def build_db(db_path: str, min_year: int) -> None:
    contests_text = _fetch_csv_text(OFFICIAL_CONTESTS_URL, timeout=120)
    findings_text = _fetch_csv_text(OFFICIAL_FINDINGS_URL, timeout=240)

    contest_rows = list(csv.DictReader(io.StringIO(contests_text)))
    findings_rows = list(csv.DictReader(io.StringIO(findings_text)))

    contests_by_id: dict[str, dict[str, str]] = {}
    for row in contest_rows:
        cid = str(row.get("contestid", "")).strip()
        if cid:
            contests_by_id[cid] = row

    def contest_allowed(cid: str) -> bool:
        meta = contests_by_id.get(str(cid).strip())
        if not meta:
            return False
        start_iso = _parse_date(meta.get("start_time", ""))
        if not start_iso:
            return False
        return int(start_iso[:4]) >= min_year

    # Aggregate (contest_id, handle) -> stats for contest_results
    agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "prize_money_usd": 0.0,
            "total_reports": 0,
            "high_all": 0,
            "med_all": 0,
            "gas_all": 0,
        }
    )
    for fr in findings_rows:
        cid = str(fr.get("contest", "")).strip()
        handle = (fr.get("handle") or "").strip()
        if not cid or not handle:
            continue
        if not contest_allowed(cid):
            continue
        usd = _to_float(fr.get("awardUSD", "")) or 0.0
        h, m, g = _severity_counts(fr.get("finding", ""))
        key = (cid, handle)
        a = agg[key]
        a["prize_money_usd"] += usd
        a["total_reports"] += 1
        a["high_all"] += h
        a["med_all"] += m
        a["gas_all"] += g

    contest_result_rows: list[tuple] = []
    for (cid, handle), a in agg.items():
        meta = contests_by_id[cid]
        start_iso = _parse_date(meta.get("start_time", ""))
        end_iso = _parse_date(meta.get("end_time", ""))
        repo = (meta.get("repo") or "").strip()
        slug = _repo_slug(repo)
        pool = _extract_pool_usd(meta.get("amount", "") or "")
        contest_result_rows.append(
            (
                slug,
                (meta.get("sponsor") or "").strip() or None,
                (meta.get("title") or "").strip() or None,
                start_iso,
                end_iso,
                pool,
                handle,
                handle.lower(),
                round(a["prize_money_usd"], 2),
                int(a["total_reports"]),
                int(a["high_all"]),
                0,
                int(a["med_all"]),
                0,
                int(a["gas_all"]),
            )
        )

    # All-time leaderboard from official findings (contests with start_year >= min_year)
    lb_agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "prize_money_usd": 0.0,
            "total_reports": 0,
            "high_all": 0,
            "med_all": 0,
            "gas_all": 0,
        }
    )
    for fr in findings_rows:
        cid = str(fr.get("contest", "")).strip()
        handle = (fr.get("handle") or "").strip()
        if not cid or not handle:
            continue
        if not contest_allowed(cid):
            continue
        usd = _to_float(fr.get("awardUSD", "")) or 0.0
        h, m, g = _severity_counts(fr.get("finding", ""))
        b = lb_agg[handle]
        b["prize_money_usd"] += usd
        b["total_reports"] += 1
        b["high_all"] += h
        b["med_all"] += m
        b["gas_all"] += g

    lb_rows: list[tuple] = []
    for handle, b in lb_agg.items():
        hn = handle.lower()
        lb_rows.append(
            (
                handle,
                hn,
                "all_time_official",
                None,
                None,
                round(b["prize_money_usd"], 2),
                int(b["total_reports"]),
                int(b["high_all"]),
                0,
                int(b["med_all"]),
                0,
                int(b["gas_all"]),
            )
        )

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        conn.executescript(
            """
            DROP TABLE IF EXISTS findings;
            DROP TABLE IF EXISTS contests;
            DROP TABLE IF EXISTS contest_results;
            DROP TABLE IF EXISTS leaderboard_snapshots;
            DROP TABLE IF EXISTS import_meta;

            CREATE TABLE contests (
                contestid TEXT PRIMARY KEY,
                title TEXT,
                sponsor TEXT,
                details TEXT,
                start_time TEXT,
                end_time TEXT,
                amount TEXT,
                repo TEXT,
                findingsRepo TEXT,
                hide TEXT,
                league TEXT
            );

            CREATE TABLE findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contest TEXT NOT NULL,
                handle TEXT NOT NULL,
                finding TEXT,
                risk TEXT,
                score REAL,
                pie REAL,
                split REAL,
                slice REAL,
                award REAL,
                awardCoin TEXT,
                awardUSD REAL
            );

            CREATE INDEX idx_findings_contest ON findings(contest);
            CREATE INDEX idx_findings_handle ON findings(handle);
            CREATE INDEX idx_findings_contest_handle ON findings(contest, handle);

            CREATE TABLE contest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contest_report_repo TEXT,
                contest_sponsor TEXT,
                contest_desc TEXT,
                start_date TEXT,
                end_date TEXT,
                prize_pool_usd REAL,
                handle TEXT NOT NULL,
                handle_norm TEXT NOT NULL,
                prize_money_usd REAL,
                total_reports INTEGER,
                high_all INTEGER,
                high_solo INTEGER,
                med_all INTEGER,
                med_solo INTEGER,
                gas_all INTEGER
            );

            CREATE TABLE leaderboard_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                handle TEXT NOT NULL,
                handle_norm TEXT NOT NULL,
                timeframe TEXT,
                period_start TEXT,
                period_end TEXT,
                prize_money_usd REAL,
                total_reports INTEGER,
                high_all INTEGER,
                high_solo INTEGER,
                med_all INTEGER,
                med_solo INTEGER,
                gas_all INTEGER
            );

            CREATE TABLE import_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX idx_contest_handle_norm ON contest_results(handle_norm);
            CREATE INDEX idx_contest_start_date ON contest_results(start_date);
            CREATE INDEX idx_contest_sponsor ON contest_results(contest_sponsor);
            CREATE INDEX idx_leaderboard_handle_norm ON leaderboard_snapshots(handle_norm);
            """
        )

        conn.executemany(
            """
            INSERT INTO contests (
                contestid, title, sponsor, details, start_time, end_time,
                amount, repo, findingsRepo, hide, league
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(r.get("contestid", "")).strip(),
                    (r.get("title") or "").strip() or None,
                    (r.get("sponsor") or "").strip() or None,
                    (r.get("details") or "").strip() or None,
                    (r.get("start_time") or "").strip() or None,
                    (r.get("end_time") or "").strip() or None,
                    (r.get("amount") or "").strip() or None,
                    (r.get("repo") or "").strip() or None,
                    (r.get("findingsRepo") or "").strip() or None,
                    str(r.get("hide", "")).strip(),
                    (r.get("league") or "").strip() or None,
                )
                for r in contest_rows
                if str(r.get("contestid", "")).strip()
            ],
        )

        conn.executemany(
            """
            INSERT INTO findings (
                contest, handle, finding, risk, score, pie, split, slice,
                award, awardCoin, awardUSD
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(r.get("contest", "")).strip(),
                    (r.get("handle") or "").strip(),
                    (r.get("finding") or "").strip() or None,
                    (r.get("risk") or "").strip() or None,
                    _to_float(r.get("score", "")),
                    _to_float(r.get("pie", "")),
                    _to_float(r.get("split", "")),
                    _to_float(r.get("slice", "")),
                    _to_float(r.get("award", "")),
                    (r.get("awardCoin") or "").strip() or None,
                    _to_float(r.get("awardUSD", "")),
                )
                for r in findings_rows
                if str(r.get("contest", "")).strip() and (r.get("handle") or "").strip()
            ],
        )

        conn.executemany(
            """
            INSERT INTO contest_results (
                contest_report_repo, contest_sponsor, contest_desc, start_date, end_date,
                prize_pool_usd, handle, handle_norm, prize_money_usd,
                total_reports, high_all, high_solo, med_all, med_solo, gas_all
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            contest_result_rows,
        )

        conn.executemany(
            """
            INSERT INTO leaderboard_snapshots (
                handle, handle_norm, timeframe, period_start, period_end, prize_money_usd,
                total_reports, high_all, high_solo, med_all, med_solo, gas_all
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            lb_rows,
        )

        conn.executemany(
            "INSERT INTO import_meta(key, value) VALUES(?, ?)",
            [
                ("generated_at_utc", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                ("min_year", str(min_year)),
                ("official_findings_url", OFFICIAL_FINDINGS_URL),
                ("official_contests_url", OFFICIAL_CONTESTS_URL),
                ("source", "code4rena.com/community-resources (official)"),
            ],
        )

        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Download Code4rena official community CSVs from code4rena.com "
            "and save contests, findings, and aggregates to SQLite."
        )
    )
    parser.add_argument(
        "--db-path",
        default="code4rena_leaderboard.sqlite3",
        help="Target sqlite3 file path.",
    )
    parser.add_argument(
        "--min-year",
        type=int,
        default=2020,
        help="Only include contests with start year >= this value in aggregates.",
    )
    args = parser.parse_args()

    build_db(args.db_path, args.min_year)
    print(f"Done. SQLite written to: {args.db_path}")


if __name__ == "__main__":
    main()
