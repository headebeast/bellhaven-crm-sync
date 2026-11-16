"""CLI entrypoint for scraping, reconciling, reviewing, and approving Bellhaven CRM changes."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from bellhaven_sync.review_app import serve_review_app
from bellhaven_sync.service import SyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bellhaven Senior Living -> CRM daily reconciliation tool")
    parser.add_argument("--sync", action="store_true", help="Scrape website + CRM and enqueue new proposals")
    parser.add_argument("--serve", action="store_true", help="Start the local review web UI")
    parser.add_argument("--status", action="store_true", help="Print queue counts and latest sync stats")
    parser.add_argument("--list", dest="list_status", choices=["pending", "approved", "rejected", "all"], help="List proposals as JSON")
    parser.add_argument("--approve", metavar="FINGERPRINT", help="Approve and execute a single pending proposal by fingerprint")
    parser.add_argument("--reject", metavar="FINGERPRINT", help="Reject and suppress a single pending proposal by fingerprint")
    parser.add_argument("--note", default="", help="Optional decision note used with --approve or --reject")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = SyncService.from_settings()

    if not any([args.sync, args.serve, args.status, args.list_status, args.approve, args.reject]):
        parser.print_help()
        return 0

    if args.sync:
        stats = service.run_sync()
        print("SYNC COMPLETE:")
        print(json.dumps(stats, indent=2))

    if args.approve:
        res = service.approve(args.approve, args.note)
        print(f"APPROVED {args.approve}:")
        print(json.dumps(res, indent=2))

    if args.reject:
        service.reject(args.reject, args.note)
        print(f"REJECTED {args.reject}.")

    if args.status:
        print(json.dumps({"counts": service.store.counts(), "latest_sync": service.store.latest_sync_run()}, indent=2))

    if args.list_status:
        items = service.store.list_proposals(None if args.list_status == "all" else args.list_status)
        out = [
            {
                "fingerprint": p.fingerprint,
                "category": p.category,
                "recommended": p.recommended_decision,
                "status": p.decision_status,
                "title": p.title,
                "target_account_id": p.target_account_id,
                "summary": p.summary,
                "operations": p.operations,
            }
            for p in items
        ]
        print(json.dumps(out, indent=2))

    if args.serve:
        serve_review_app(service)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
