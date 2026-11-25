"""Minimal CLI for SCOTUS Briefs: monitor, explain, build, or run all three."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .docket import _current_term, load_all_dockets, monitor_dockets, normalize_docket
from .explain import LLMConfig, explain_case, load_all_explanations, needs_update
from .site import build_site


def _cmd_monitor(args: argparse.Namespace) -> int:
    cases = monitor_dockets(
        dockets=args.dockets or None,
        term=args.term,
        limit=args.limit,
        download_pdfs=not args.no_download,
        root=Path(args.root),
    )
    print(f"Monitored {len(cases)} docket(s).")
    for c in cases:
        pdf_count = len(c.pdf_hashes())
        print(f"  {c.docket:10s} [{c.status:7s}] {c.title} ({len(c.proceedings)} entries, {pdf_count} PDFs)")
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    root = Path(args.root)
    config = LLMConfig.from_env()
    if args.base_url:
        config.base_url = args.base_url.rstrip("/")
    if args.model:
        config.model = args.model

    cases = load_all_dockets(root)
    if args.dockets:
        wanted = {normalize_docket(d) for d in args.dockets}
        cases = [c for c in cases if c.docket in wanted]
    if args.limit is not None:
        cases = cases[: args.limit]

    updated = 0
    for case in cases:
        try:
            exp = explain_case(case, config, root=root, force=args.force)
            if exp is not None:
                updated += 1
                print(f"  {case.docket:10s} explained ({exp.model})")
        except Exception as exc:
            print(f"[warn] failed to explain {case.docket}: {exc}", file=sys.stderr)

    print(f"Explained {updated}/{len(cases)} case(s).")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    root = Path(args.root)
    out = Path(args.out) if args.out else root / "dist"
    count = build_site(root=root, out_dir=out)
    print(f"Built static site in {out} ({count} case page(s)).")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    _cmd_monitor(args)
    _cmd_explain(args)
    _cmd_build(args)
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    """Backfill one previous October Term (the newest incomplete term) and stop."""
    root = Path(args.root)
    config = LLMConfig.from_env()
    if args.base_url:
        config.base_url = args.base_url.rstrip("/")
    if args.model:
        config.model = args.model

    start_term = args.from_term if args.from_term is not None else (_current_term() - 1)
    existing_exps = {e.docket: e for e in load_all_explanations(root)}

    for term in range(start_term, args.min_term - 1, -1):
        print(f"Checking OT {term} for backfill...")
        cases = monitor_dockets(
            term=term,
            download_pdfs=not args.no_download,
            root=root,
        )
        if not cases:
            print(f"OT {term}: no dockets found on supremecourt.gov; stopping backfill.")
            return 0

        pending = [c for c in cases if needs_update(c, existing_exps.get(c.docket))]
        if not pending:
            print(f"OT {term}: already complete ({len(cases)} cases).")
            continue

        if args.limit is not None:
            pending = pending[: args.limit]

        print(f"Backfilling OT {term}: {len(pending)} case(s) to explain with {config.model}.")
        updated = 0
        for case in pending:
            try:
                exp = explain_case(case, config, root=root)
                if exp is not None:
                    updated += 1
                    print(f"  {case.docket:10s} explained ({exp.model})")
            except Exception as exc:
                print(f"[warn] failed to explain {case.docket}: {exc}", file=sys.stderr)

        print(f"Backfilled OT {term}: {updated}/{len(pending)} case(s).")
        return 0

    print(f"Backfill complete through OT {args.min_term}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scotus",
        description="A citizen's guide to the Supreme Court.",
    )
    parser.add_argument("--root", default=".", help="Project root directory (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)

    # monitor
    p_mon = sub.add_parser("monitor", help="Fetch supremecourt.gov dockets and download new PDFs.")
    p_mon.add_argument("dockets", nargs="*", help="Specific docket numbers (e.g. 24-297). Omit to discover current term.")
    p_mon.add_argument("--term", type=int, default=None, help="October Term year (e.g. 2025).")
    p_mon.add_argument("--limit", type=int, default=None, help="Max number of dockets to fetch.")
    p_mon.add_argument("--no-download", action="store_true", help="Skip downloading filing PDFs.")
    p_mon.set_defaults(func=_cmd_monitor)

    # explain
    p_exp = sub.add_parser("explain", help="Generate plain-English explanations via an LLM.")
    p_exp.add_argument("dockets", nargs="*", help="Specific docket numbers to explain. Omit for all monitored dockets.")
    p_exp.add_argument("--limit", type=int, default=None, help="Max number of cases to explain.")
    p_exp.add_argument("--force", action="store_true", help="Re-explain even if filings have not changed.")
    p_exp.add_argument("--base-url", default=None, help="Override LLM_BASE_URL (OpenAI-compatible).")
    p_exp.add_argument("--model", default=None, help="Override LLM_MODEL.")
    p_exp.set_defaults(func=_cmd_explain)

    # build
    p_bld = sub.add_parser("build", help="Build the zero-JS static HTML site into dist/.")
    p_bld.add_argument("--out", default=None, help="Output directory (default: dist/).")
    p_bld.set_defaults(func=_cmd_build)

    # run (all three)
    p_run = sub.add_parser("run", help="Monitor dockets, explain new/changed cases, and build the site.")
    p_run.add_argument("dockets", nargs="*", help="Optional specific docket numbers.")
    p_run.add_argument("--term", type=int, default=None, help="October Term year.")
    p_run.add_argument("--limit", type=int, default=None, help="Max dockets per run.")
    p_run.add_argument("--no-download", action="store_true", help="Skip downloading filing PDFs.")
    p_run.add_argument("--force", action="store_true", help="Re-explain unchanged cases.")
    p_run.add_argument("--base-url", default=None, help="Override LLM_BASE_URL.")
    p_run.add_argument("--model", default=None, help="Override LLM_MODEL.")
    p_run.add_argument("--out", default=None, help="Site output directory (default: dist/).")
    p_run.set_defaults(func=_cmd_run)

    # backfill (one previous term per invocation)
    p_bf = sub.add_parser("backfill", help="Backfill the newest incomplete previous term.")
    p_bf.add_argument("--from-term", type=int, default=None, help="Starting term (default: current term - 1).")
    p_bf.add_argument("--min-term", type=int, default=2000, help="Earliest term to backfill (default: 2000).")
    p_bf.add_argument("--limit", type=int, default=None, help="Optional cap on cases to explain in one run.")
    p_bf.add_argument("--no-download", action="store_true", help="Skip downloading filing PDFs.")
    p_bf.add_argument("--base-url", default=None, help="Override LLM_BASE_URL.")
    p_bf.add_argument("--model", default=None, help="Override LLM_MODEL.")
    p_bf.set_defaults(func=_cmd_backfill)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
