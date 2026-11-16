"""Executes approved proposal operations against the live CRM REST API."""

from __future__ import annotations

from typing import Any

from .crm_client import CrmClient
from .models import Proposal


def _resolve_placeholders(payload: dict[str, Any], created_account_id: str | None) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, val in payload.items():
        if val == "$CREATED_ACCOUNT_ID":
            if not created_account_id:
                raise RuntimeError(f"Operation requires $CREATED_ACCOUNT_ID for field '{key}', but none was created.")
            resolved[key] = created_account_id
        else:
            resolved[key] = val
    return resolved


def execute_proposal(client: CrmClient, proposal: Proposal) -> dict[str, Any]:
    """Apply every API step in proposal.operations in order, returning an execution audit record."""
    if not proposal.operations:
        return {"applied_steps": [], "created_account_id": None}

    created_account_id: str | None = None
    applied_steps: list[dict[str, Any]] = []

    for step in proposal.operations:
        action = step["action"]
        payload = _resolve_placeholders(step.get("payload", {}), created_account_id)

        if action == "create_account":
            created = client.create_account(payload)
            created_account_id = created.account_id
            applied_steps.append(
                {
                    "action": action,
                    "account_id": created.account_id,
                    "name": created.name or payload.get("name", ""),
                    "parent_id": created.parent_id or payload.get("parent_id", ""),
                }
            )
        elif action == "patch_account":
            target_id = step["account_id"]
            updated = client.patch_account(target_id, payload)
            applied_steps.append(
                {
                    "action": action,
                    "account_id": updated.account_id or target_id,
                    "patched_fields": sorted(payload.keys()),
                }
            )
        elif action == "create_contact":
            created_contact = client.create_contact(payload)
            applied_steps.append(
                {
                    "action": action,
                    "contact_id": created_contact.contact_id,
                    "account_id": created_contact.account_id or payload.get("account_id", ""),
                    "name": created_contact.name or payload.get("name", ""),
                }
            )
        elif action == "patch_contact":
            contact_id = step["contact_id"]
            updated_contact = client.patch_contact(contact_id, payload)
            applied_steps.append(
                {
                    "action": action,
                    "contact_id": updated_contact.contact_id,
                    "patched_fields": sorted(payload.keys()),
                }
            )
        else:
            raise ValueError(f"Unsupported operation action: {action}")

    return {"created_account_id": created_account_id, "applied_steps": applied_steps}
