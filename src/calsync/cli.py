"""Command-line entry point: `supaero-calsync`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .classify import classify_all, summarize
from .config import USER_CONFIG, ConfigError, Config, default_config_text, load_config
from .models import Lesson
from .pipeline import SyncResult, run_sync
from .portal import PortalError, fetch_interventions, origin, parse_lessons
from .sync import UnsafePlanError


def academic_year(today: date) -> tuple[date, date]:
    """1 September to 31 August of the academic year containing `today`."""
    first_year = today.year if today.month >= 9 else today.year - 1
    return date(first_year, 9, 1), date(first_year + 1, 8, 31)


def print_classification(lessons: list[Lesson], config: Config) -> None:
    summary = summarize(lessons, classify_all(lessons, config), config)
    print("\nClassification")
    for category in config.all_categories:
        titles = summary[category.name]
        print(f"\n  {category.name}  ->  '{category.calendar}'  ({sum(titles.values())} sessions)")
        for title, count in sorted(titles.items(), key=lambda kv: (-kv[1], kv[0]))[:12]:
            print(f"      {count:>3} x {title}")
        if len(titles) > 12:
            print(f"      ... and {len(titles) - 12} more titles")


def print_result(result: SyncResult) -> None:
    plan = result.plan
    verb = "Done" if result.applied else "Dry run (nothing written)"
    print(
        f"\n{verb}: {len(plan.creates)} to create, {len(plan.updates)} to update, "
        f"{len(plan.deletes)} to delete, {plan.unchanged} unchanged."
    )


def load_lessons(args: argparse.Namespace, config: Config, start: date, end: date) -> list[Lesson]:
    if args.from_file:
        try:
            raw = json.loads(args.from_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PortalError(f"Cannot read {args.from_file}: {exc}") from exc
        if not isinstance(raw, list):
            raise PortalError(f"{args.from_file} must contain a JSON list of sessions.")
        return parse_lessons(raw)

    from .browser import LoginError, acquire_token, token_expiry

    if not config.portal_url:
        raise PortalError("Set [portal] url in the config file.")
    try:
        token = acquire_token(config.portal_url, browser=config.browser)
    except LoginError as exc:
        raise PortalError(str(exc)) from exc
    expiry = token_expiry(token)
    if expiry:
        print(f"Signed in (session valid until {expiry:%H:%M} UTC).")
    raw = fetch_interventions(origin(config.portal_url), token, start, end)
    if args.save_raw:
        args.save_raw.write_text(json.dumps(raw, ensure_ascii=False, indent=1), "utf-8")
        print(f"Raw portal data saved to {args.save_raw} (contains personal data, do not commit).")
    return parse_lessons(raw)


def cmd_init() -> int:
    if USER_CONFIG.exists():
        print(f"Config already exists: {USER_CONFIG}")
        return 0
    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG.write_text(default_config_text(), encoding="utf-8")
    print(f"Wrote {USER_CONFIG}\nEdit it to change calendar names, colours and matching rules.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    default_start, default_end = academic_year(date.today())
    parser = argparse.ArgumentParser(
        prog="supaero-calsync",
        description="Sync your ISAE-SUPAERO timetable to Google Calendar.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("command", nargs="?", choices=["sync", "init"], default="sync")
    parser.add_argument("--start", type=date.fromisoformat, default=default_start,
                        help=f"first day, YYYY-MM-DD (default: {default_start})")
    parser.add_argument("--end", type=date.fromisoformat, default=default_end,
                        help=f"last day, YYYY-MM-DD (default: {default_end})")
    parser.add_argument("--config", type=Path, help=f"config file (default: {USER_CONFIG})")
    parser.add_argument("--preview", action="store_true",
                        help="fetch and classify only; do not touch Google Calendar")
    parser.add_argument("--dry-run", action="store_true",
                        help="compare with Google Calendar and show the changes without applying them")
    parser.add_argument("--force-delete", action="store_true",
                        help="allow deleting a large share of existing events")
    parser.add_argument("--from-file", type=Path, metavar="JSON",
                        help="read raw portal data from a file instead of logging in")
    parser.add_argument("--save-raw", type=Path, metavar="JSON",
                        help="save the raw portal response (debugging; contains personal data)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return cmd_init()
    if args.end < args.start:
        print("--end is before --start", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
        lessons = load_lessons(args, config, args.start, args.end)
        lessons = [lesson for lesson in lessons if args.start <= lesson.start.date() <= args.end]
        print(f"{len(lessons)} sessions between {args.start} and {args.end}.")
        print_classification(lessons, config)
        if args.preview:
            return 0

        from .google import GoogleGateway, GoogleSetupError

        try:
            gateway = GoogleGateway.connect()
        except GoogleSetupError as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 1
        result = run_sync(
            lessons, config, gateway, args.start, args.end,
            dry_run=args.dry_run, force_delete=args.force_delete,
        )
        print_result(result)
        return 0
    except (ConfigError, PortalError, UnsafePlanError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
