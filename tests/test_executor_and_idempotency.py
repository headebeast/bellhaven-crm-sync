"""Unit tests for CHOW execution ordering and idempotent SQLite proposal tracking."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from bellhaven_sync.config import BELLHAVEN_PARENT_ID
from bellhaven_sync.crm_client import CrmClient
from bellhaven_sync.executor import execute_proposal
from bellhaven_sync.models import CrmAccount, CrmContact, Proposal
from bellhaven_sync.state_store import StateStore


class FakeCrmClient:
    """In-memory stand-in for CrmClient verifying exact write calls and payloads."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def create_account(self, payload: dict[str, Any]) -> CrmAccount:
        self.calls.append(("create_account", payload))
        return CrmAccount(
            account_id="NEW_ACCT_999",
            name=payload["name"],
            parent_id=payload["parent_id"],
            parent_name="Bellhaven Senior Living (Parent Account)",
            billing_street=payload.get("billing_street", ""),
            billing_city=payload.get("billing_city", ""),
            billing_state=payload.get("billing_state", ""),
            billing_zip=payload.get("billing_zip", ""),
            care_type=payload.get("care_type", ""),
            status=payload.get("status", "Active"),
            phone=payload.get("phone", ""),
            lifetime_revenue=0,
            outstanding_ar=0,
        )

    def patch_account(self, account_id: str, payload: dict[str, Any]) -> CrmAccount:
        self.calls.append(("patch_account", account_id, payload))
        return CrmAccount(
            account_id=account_id,
            name="Old Name",
            parent_id="CEDAR_ID",
            parent_name="Cedar Trail Communities",
            billing_street="45 St Lawrence Dr",
            billing_city="Tiffin",
            billing_state="OH",
            billing_zip="44883",
            care_type="Skilled Nursing",
            status="Active",
            phone="",
            lifetime_revenue=84000,
            outstanding_ar=12400,
            chow_current_account=payload.get("chow_current_account", ""),
        )

    def create_contact(self, payload: dict[str, Any]) -> CrmContact:
        self.calls.append(("create_contact", payload))
        return CrmContact(
            contact_id="NEW_CONT_111",
            account_id=payload["account_id"],
            name=payload["name"],
            title=payload.get("title", ""),
            email="",
            phone=payload.get("phone", ""),
            is_active=True,
        )

    def patch_contact(self, contact_id: str, payload: dict[str, Any]) -> CrmContact:
        self.calls.append(("patch_contact", contact_id, payload))
        return CrmContact(contact_id=contact_id, account_id="A", name="X", title="Administrator", email="", phone="", is_active=False)


class ExecutorAndIdempotencyTests(unittest.TestCase):
    def test_chow_execution_resolves_created_account_id_without_modifying_old_parent(self) -> None:
        client = FakeCrmClient()
        proposal = Proposal(
            fingerprint="fp_chow_tiffin",
            category="CHOW_CREATE_AND_LINK",
            title="CHOW SOP: Bellhaven of Tiffin",
            facility_name="Bellhaven of Tiffin",
            target_account_id="OLD_TIFFIN",
            recommended_decision="approve",
            confidence="high",
            summary="CHOW split",
            operations=[
                {
                    "action": "create_account",
                    "payload": {"name": "Bellhaven of Tiffin", "parent_id": BELLHAVEN_PARENT_ID},
                },
                {
                    "action": "patch_account",
                    "account_id": "OLD_TIFFIN",
                    "payload": {"chow_current_account": "$CREATED_ACCOUNT_ID", "note": "Preserved"},
                },
                {
                    "action": "create_contact",
                    "payload": {"account_id": "$CREATED_ACCOUNT_ID", "name": "Walt Osei", "title": "Administrator"},
                },
            ],
        )

        res = execute_proposal(client, proposal)  # type: ignore[arg-type]
        self.assertEqual(res["created_account_id"], "NEW_ACCT_999")
        self.assertEqual(client.calls[1], ("patch_account", "OLD_TIFFIN", {"chow_current_account": "NEW_ACCT_999", "note": "Preserved"}))
        self.assertEqual(client.calls[2][1]["account_id"], "NEW_ACCT_999")

    def test_state_store_never_reproposes_decided_items_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            p_approve = Proposal(
                fingerprint="fp_approve",
                category="FIELD_UPDATE",
                title="Fix Portsmouth ZIP",
                facility_name="Bellhaven of Portsmouth",
                target_account_id="001CF3LDWVRGL09P4F",
                recommended_decision="approve",
                confidence="high",
                summary="ZIP mismatch",
            )
            p_reject = Proposal(
                fingerprint="fp_reject",
                category="FALSE_POSITIVE_GUARD",
                title="Amberly CO False Positive",
                facility_name="Amberly Manor",
                target_account_id="0015D74ZLRY810RGY5",
                recommended_decision="reject",
                confidence="low",
                summary="Colorado Springs != Hudson OH",
            )

            first = store.upsert_proposals([p_approve, p_reject], 35, 121, 67)
            self.assertEqual(first["new_pending"], 2)

            store.mark_approved("fp_approve", {"applied_steps": []}, "OK")
            store.mark_rejected("fp_reject", "Different state")

            second = store.upsert_proposals([p_approve, p_reject], 35, 121, 67)
            self.assertEqual(second["new_pending"], 0)
            self.assertEqual(second["skipped_decided"], 2)
            self.assertEqual(store.counts(), {"pending": 0, "approved": 1, "rejected": 1})

    def test_crm_client_paginates_data_wrapper(self) -> None:
        pages = {
            1: {"data": [{"account_id": "A1", "name": "One"}], "page": 1, "page_size": 1, "total": 2},
            2: {"data": [{"account_id": "A2", "name": "Two"}], "page": 2, "page_size": 1, "total": 2},
        }

        class Resp(io.BytesIO):
            def __enter__(self) -> "Resp":
                return self
            def __exit__(self, *args: Any) -> None:
                self.close()

        def fake_opener(req: Any, timeout: int = 25) -> Resp:
            page = 2 if "page=2" in req.full_url else 1
            return Resp(json.dumps(pages[page]).encode("utf-8"))

        client = CrmClient("https://example.test/api/v1", "tok", opener=fake_opener)
        accounts = client.list_accounts(page_size=1)
        self.assertEqual([a.account_id for a in accounts], ["A1", "A2"])


if __name__ == "__main__":
    unittest.main()
