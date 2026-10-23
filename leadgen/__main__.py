"""Command line: python -m leadgen [--city Plano] [--limit 10] [--yes] [--refresh] [--dry-run]"""
import argparse
import logging
import sys

from .config import load_config
from .pipeline import run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leadgen", description="Find and rank dental practice leads.")
    ap.add_argument("--city", action="append",
                    help="only search this city (repeat or comma-separate for several). Default: all in config")
    ap.add_argument("--limit", type=int, help="stop after this many practices (for cheap test runs)")
    ap.add_argument("--yes", "-y", action="store_true", help="skip the cost confirmation prompt")
    ap.add_argument("--refresh", action="store_true", help="ignore cached API responses and fetch fresh data")
    ap.add_argument("--dry-run", action="store_true", help="show the API call estimate and exit")
    ap.add_argument("--config", help="path to config.yaml (default: the one next to this package)")
    ap.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    out_dir = cfg.path("output_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(out_dir / "run.log", encoding="utf-8")],
    )
    for noisy in ("urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    known = {c.lower(): c for c in cfg["cities"]}
    cities = list(cfg["cities"])
    if args.city:
        requested = [c.strip() for arg in args.city for c in arg.split(",") if c.strip()]
        unknown = [c for c in requested if c.lower() not in known]
        if unknown:
            ap.error(f"unknown city {', '.join(unknown)}; choose from {', '.join(cities)} (or add it to config.yaml)")
        cities = [known[c.lower()] for c in requested]
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be at least 1")

    result = run(cfg, cities, limit=args.limit, yes=args.yes, refresh=args.refresh,
                 dry_run=args.dry_run, args=vars(args))
    return 0 if result is not None or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
