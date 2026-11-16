"""Pipeline coordinator wiring scraper, CRM client, matcher, executor, and state store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings
from .crm_client import CrmClient
from .executor import execute_proposal
from .matcher import generate_proposals
from .state_store import StateStore
from .scraper import BellhavenScraper


@dataclass
class SyncService:
    """High-level use cases shared between the CLI runner and the local review server."""

    settings: Settings
    client: CrmClient
    scraper: BellhavenScraper
    store: StateStore

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "SyncService":
        cfg = settings or Settings.from_env()
        return cls(
            settings=cfg,
            client=CrmClient(cfg.api_base, cfg.api_token),
            scraper=BellhavenScraper(cfg.site_base),
            store=StateStore(cfg.db_path),
        )

    def run_sync(self) -> dict[str, Any]:
        communities = self.scraper.scrape_all()
        accounts = self.client.list_accounts()
        contacts = self.client.list_contacts()
        proposals = generate_proposals(communities, accounts, contacts)
        stats = self.store.upsert_proposals(
            proposals=proposals,
            website_count=len(communities),
            crm_account_count=len(accounts),
            crm_contact_count=len(contacts),
        )
        return {
            "website_communities": len(communities),
            "crm_accounts": len(accounts),
            "crm_contacts": len(contacts),
            **stats,
            "queue_counts": self.store.counts(),
        }

    def approve(self, fingerprint: str, note: str = "") -> dict[str, Any]:
        proposal = self.store.get_proposal(fingerprint)
        if not proposal:
            raise KeyError(f"Proposal {fingerprint} not found")
        if proposal.decision_status != "pending":
            raise ValueError(f"Proposal {proposal.fingerprint} already decided ({proposal.decision_status})")
        result = execute_proposal(self.client, proposal)
        self.store.mark_approved(proposal.fingerprint, result, note or "Approved via review queue")
        return result

    def reject(self, fingerprint: str, note: str = "") -> None:
        proposal = self.store.get_proposal(fingerprint)
        if not proposal:
            raise KeyError(f"Proposal {fingerprint} not found")
        if proposal.decision_status != "pending":
            raise ValueError(f"Proposal {proposal.fingerprint} already decided ({proposal.decision_status})")
        self.store.mark_rejected(proposal.fingerprint, note or "Rejected by reviewer")
