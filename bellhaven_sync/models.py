"""Domain data models for website records, CRM objects, and review proposals."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class WebsiteCommunity:
    """Normalized facility record parsed from the Bellhaven public website."""

    slug: str
    url: str
    name: str
    street: str
    city: str
    state: str
    zip_code: str
    care_offerings: tuple[str, ...]
    administrator: str
    phone: str
    discovered_from: str = "directory"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrmAccount:
    """Mirror of an account record returned by GET /api/v1/accounts."""

    account_id: str
    name: str
    parent_id: str
    parent_name: str
    billing_street: str
    billing_city: str
    billing_state: str
    billing_zip: str
    care_type: str
    status: str
    phone: str
    lifetime_revenue: float
    outstanding_ar: float
    chow_current_account: str = ""
    duplicate_of_account: str = ""
    note: str = ""

    @property
    def is_parent_account(self) -> bool:
        return not self.billing_street.strip() and "parent account" in self.name.lower()

    @property
    def requires_chow_sop(self) -> bool:
        """Preserve account & link CHOW when both revenue and open AR exceed zero."""
        return float(self.lifetime_revenue or 0) > 0 and float(self.outstanding_ar or 0) > 0

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "CrmAccount":
        return cls(
            account_id=str(data.get("account_id", "")),
            name=str(data.get("name", "") or ""),
            parent_id=str(data.get("parent_id", "") or ""),
            parent_name=str(data.get("parent_name", "") or ""),
            billing_street=str(data.get("billing_street", "") or ""),
            billing_city=str(data.get("billing_city", "") or ""),
            billing_state=str(data.get("billing_state", "") or ""),
            billing_zip=str(data.get("billing_zip", "") or ""),
            care_type=str(data.get("care_type", "") or ""),
            status=str(data.get("status", "") or ""),
            phone=str(data.get("phone", "") or ""),
            lifetime_revenue=float(data.get("lifetime_revenue") or 0),
            outstanding_ar=float(data.get("outstanding_ar") or 0),
            chow_current_account=str(data.get("chow_current_account", "") or ""),
            duplicate_of_account=str(data.get("duplicate_of_account", "") or ""),
            note=str(data.get("note", "") or ""),
        )


@dataclass(frozen=True)
class CrmContact:
    """Mirror of a contact record returned by GET /api/v1/contacts."""

    contact_id: str
    account_id: str
    name: str
    title: str
    email: str
    phone: str
    is_active: bool

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "CrmContact":
        return cls(
            contact_id=str(data.get("contact_id", "")),
            account_id=str(data.get("account_id", "")),
            name=str(data.get("name", "") or ""),
            title=str(data.get("title", "") or ""),
            email=str(data.get("email", "") or ""),
            phone=str(data.get("phone", "") or ""),
            is_active=bool(data.get("is_active", True)),
        )


@dataclass
class Proposal:
    """Single reviewable reconciliation action with machine plan and human evidence."""

    fingerprint: str
    category: str
    title: str
    facility_name: str
    target_account_id: str
    recommended_decision: str  # "approve" | "reject"
    confidence: str            # "high" | "medium" | "low"
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    operations: list[dict[str, Any]] = field(default_factory=list)
    decision_status: str = "pending"  # "pending" | "approved" | "rejected"
    decision_note: str = ""
    execution_result: dict[str, Any] = field(default_factory=dict)
