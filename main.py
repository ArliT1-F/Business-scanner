#!/usr/bin/env python3
"""Tirana Business Finder - command line interface.

Discovers businesses in Tirana that have no website listed on their Google
Maps / Google Business profile, using the official Google Places API (New).

Commands (the command is optional; the default is `scan`):

    scan    run the grid/category scan (default)
    export  export the database to CSV/XLSX
    stats   show statistics for the database

Examples:

    python main.py --dry-run
    python main.py --categories cafe,restaurant,bar
    python main.py scan --grid-km 1.0 --limit 6
    python main.py --no-website-only
    python main.py export --no-website-only
    python main.py stats
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional

from config import DEFAULT_CATEGORIES, GRID_SIZE_KM, AppConfig
from database import Database
from exporters import export_database
from scanner import run_scan
from utils import fmt_float, fmt_int, section, truncate

EXAMPLES = """examples:
  python main.py --dry-run                     preview scan size, zero API calls
  python main.py --categories cafe,restaurant  scan only these categories
  python main.py scan --grid-km 5              small test scan before a full one
  python main.py --no-website-only             export only website-less businesses
  python main.py export --no-website-only      re-export from the existing database
  python main.py stats                         show database statistics
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Discover businesses in Tirana that have no website listed on "
            "Google Maps, using the official Google Places API (New). "
            "No browser automation is used."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="scan",
        choices=["scan", "export", "stats"],
        help="command to run (default: scan)",
    )

    # -- scan options ---------------------------------------------------------
    scan = parser.add_argument_group("scan options")
    scan.add_argument(
        "--categories",
        metavar="TYPES",
        default=None,
        help=(
            "comma-separated Google Places feature types to search, e.g. "
            "cafe,restaurant,bar (default: built-in list of "
            f"{len(DEFAULT_CATEGORIES)} types)"
        ),
    )
    scan.add_argument(
        "--grid-km",
        type=float,
        default=None,
        metavar="KM",
        help=f"geographic grid cell size in kilometres (default: {GRID_SIZE_KM})",
    )
    scan.add_argument(
        "--radius-m",
        type=int,
        default=None,
        metavar="M",
        help="search radius in metres, 100-50000 (default: from config)",
    )
    scan.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="show the planned scan size and make ZERO API requests",
    )
    scan.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="run at most N search jobs (useful for small test scans)",
    )
    scan.add_argument(
        "--batch-types",
        action="store_true",
        default=False,
        help="send ALL categories in ONE request per cell (Google allows "
             "up to 50 types per request) - roughly one request per cell "
             "instead of one per cell and category; the default mode sends "
             "one request per cell and category",
    )
    scan.add_argument(
        "--no-website-only",
        action="store_true",
        default=False,
        help="the final CSV/XLSX export contains only businesses without a "
        "website listed on Google Maps",
    )
    scan.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="reduce per-search console output",
    )

    # -- API behaviour ---------------------------------------------------------
    api = parser.add_argument_group("api behaviour")
    api.add_argument(
        "--delay",
        type=float,
        default=None,
        metavar="SECONDS",
        help="minimum delay between API requests (default: from config)",
    )
    api.add_argument(
        "--max-retries",
        type=int,
        default=None,
        metavar="N",
        help="max retries for transient errors, 429/5xx (default: from config)",
    )

    # -- storage ----------------------------------------------------------------
    storage = parser.add_argument_group("storage")
    storage.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="SQLite database path (default: data/tirana_businesses.db)",
    )
    storage.add_argument(
        "--csv",
        default=None,
        metavar="PATH",
        help="CSV output path (default: output/tirana_businesses.csv)",
    )
    return parser


def build_config(args: argparse.Namespace) -> AppConfig:
    """Apply CLI overrides on top of the defaults from config.py."""
    config = AppConfig()
    if args.categories:
        config.categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    if args.grid_km is not None:
        config.grid_size_km = args.grid_km
    if args.radius_m is not None:
        config.search_radius_m = args.radius_m
    if args.delay is not None:
        config.request_delay_s = args.delay
    if args.max_retries is not None:
        config.max_retries = args.max_retries
    if args.db:
        config.database_path = args.db
    if args.csv:
        config.csv_path = args.csv
    if args.no_website_only:
        config.no_website_only = True
    if args.batch_types:
        config.batch_types = True
    try:
        config.validate()
    except ValueError as exc:
        raise SystemExit(f"error: configuration - {exc}")
    return config


def cmd_export(config: AppConfig) -> int:
    if not os.path.exists(config.database_path):
        print(f"Database not found: {config.database_path}")
        print("Run a scan first, e.g.:  python main.py --categories cafe --grid-km 5")
        return 1
    with Database(config.database_path) as db:
        if db.get_stats()["total"] == 0:
            print("The database is empty. Run a scan first.")
            return 1
        export_database(
            db,
            csv_path=config.csv_path,
            xlsx_path=config.xlsx_path,
            no_website_only=config.no_website_only,
        )
    return 0


def cmd_stats(config: AppConfig) -> int:
    if not os.path.exists(config.database_path):
        print(f"Database not found: {config.database_path}")
        print("Run a scan first, e.g.:  python main.py --categories cafe --grid-km 5")
        return 1
    with Database(config.database_path) as db:
        stats = db.get_stats()
        print(section("TIRANA BUSINESS FINDER - STATISTICS", width=40).strip())
        print(f"Database:            {config.database_path}")
        print(f"Unique businesses:   {fmt_int(stats['total'])}")
        print(f"Without website:     {fmt_int(stats['without_website'])}")
        print(f"With website:        {fmt_int(stats['with_website'])}")
        print(f"Average rating:      {fmt_float(stats['avg_rating'])}")

        categories = db.top_categories(10)
        if categories:
            print("Top categories:")
            for entry in categories:
                print(f"    {entry['type']:<28} {fmt_int(entry['count'])}")

        prospects = db.top_prospects(5)
        if prospects:
            print("Top prospects (no website listed on Google Maps):")
            for row in prospects:
                name = truncate(row["name"], 28)
                address = truncate(row["address"], 34)
                phone = row["phone"] or "-"
                print(
                    f"    score {row['prospect_score']:>3} | "
                    f"rating {fmt_float(row['rating'])} | "
                    f"{row['review_count']:>4} reviews | "
                    f"{name:<28} | {address:<34} | {phone}"
                )

        if stats["total"] == 0:
            print("\nThe database is empty. Run a scan first (see README).")
        print()
        print(
            "Note: statistics cover the businesses discovered through the "
            "configured\nGoogle Places searches, not every business in the "
            "area."
        )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Retry warnings from the API client go to stderr, progress to stdout.
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    if args.limit is not None and args.limit < 1:
        raise SystemExit("error: --limit must be >= 1")

    config = build_config(args)

    if args.command == "scan":
        return run_scan(
            config,
            dry_run=args.dry_run,
            limit=args.limit,
            quiet=args.quiet,
        )
    if args.command == "export":
        return cmd_export(config)
    if args.command == "stats":
        return cmd_stats(config)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
