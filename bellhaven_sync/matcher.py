"""Reconciliation engine matching scraped Bellhaven communities against CRM state."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .config import ACQUIRED_PARENT_NAMES, BELLHAVEN_PARENT_ID
from .models import CrmAccount, CrmContact, Proposal, WebsiteCommunity
from .normalizer import (
    extract_street_number,
    is_po_box,
    map_care_offerings,
    meaningful_name_tokens,
    name_similarity,
    normalize_city,
    normalize_name,
    normalize_phone,
    normalize_state,
    normalize_street,
    normalize_zip,
    preferred_crm_care_type,
)


def _fingerprint(category: str, anchor: str, operations: list[dict[str, Any]]) -> str:
    payload = json.dumps({"category": category, "anchor": anchor, "operations": operations}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _location_key(street: str, city: str, state: str) -> tuple[str, str, str]:
    return (normalize_street(street), normalize_city(city), normalize_state(state))


def _contacts_by_account(contacts: list[CrmContact]) -> dict[str, list[CrmContact]]:
    grouped: dict[str, list[CrmContact]] = {}
    for c in contacts:
        grouped.setdefault(c.account_id, []).append(c)
    return grouped


def _find_matching_accounts(site: WebsiteCommunity, facility_accounts: list[CrmAccount]) -> list[CrmAccount]:
    """Match a website community to all CRM accounts representing the same physical property."""
    site_loc = _location_key(site.street, site.city, site.state)
    exact_addr_matches = [
        acc for acc in facility_accounts if _location_key(acc.billing_street, acc.billing_city, acc.billing_state) == site_loc
    ]
    if exact_addr_matches:
        return exact_addr_matches

    # Fallback for PO Box billing addresses inside the same city/state with strong name match.
    site_cs = (normalize_city(site.city), normalize_state(site.state))
    po_box_matches: list[CrmAccount] = []
    for acc in facility_accounts:
        acc_cs = (normalize_city(acc.billing_city), normalize_state(acc.billing_state))
        if acc_cs != site_cs:
            continue
        if is_po_box(acc.billing_street) and name_similarity(acc.name, site.name) >= 0.80:
            po_box_matches.append(acc)
    return po_box_matches


def _rank_survivor(acc: CrmAccount, site: WebsiteCommunity) -> tuple[Any, ...]:
    """Higher tuple wins when picking canonical surviving account from duplicate matches."""
    is_already_dup = 1 if (acc.status == "Inactive" and acc.duplicate_of_account) else 0
    return (
        1 - is_already_dup,
        1 if acc.parent_id == BELLHAVEN_PARENT_ID else 0,
        1 if acc.requires_chow_sop else 0,
        1 if acc.lifetime_revenue > 0 else 0,
        1 if normalize_phone(acc.phone) == normalize_phone(site.phone) else 0,
        1 if acc.parent_name.lower() in ACQUIRED_PARENT_NAMES else 0,
        round(name_similarity(acc.name, site.name), 4),
        1 if normalize_street(acc.billing_street) == normalize_street(site.street) else 0,
        acc.account_id,
    )


def _filter_chow_predecessors(matches: list[CrmAccount]) -> list[CrmAccount]:
    """Exclude historical CHOW accounts whose chow_current_account points to a sibling in matches."""
    ids = {a.account_id for a in matches}
    return [a for a in matches if not (a.chow_current_account and a.chow_current_account in ids)]


def _compute_field_diff(site: WebsiteCommunity, acc: CrmAccount, include_parent: bool) -> dict[str, tuple[Any, Any]]:
    """Return {field_name: (old_val, new_val)} for meaningful CRM discrepancies."""
    diffs: dict[str, tuple[Any, Any]] = {}

    if include_parent and acc.parent_id != BELLHAVEN_PARENT_ID:
        diffs["parent_id"] = (acc.parent_id or "(none)", BELLHAVEN_PARENT_ID)

    if acc.name.strip() != site.name.strip():
        diffs["name"] = (acc.name, site.name)

    if is_po_box(acc.billing_street) or normalize_street(acc.billing_street) != normalize_street(site.street):
        diffs["billing_street"] = (acc.billing_street, site.street)

    if normalize_city(acc.billing_city) != normalize_city(site.city):
        diffs["billing_city"] = (acc.billing_city, site.city)

    if normalize_state(acc.billing_state) != normalize_state(site.state):
        diffs["billing_state"] = (acc.billing_state, site.state)

    if normalize_zip(acc.billing_zip) != normalize_zip(site.zip_code):
        diffs["billing_zip"] = (acc.billing_zip, site.zip_code)

    mapped_cares = map_care_offerings(site.care_offerings)
    if mapped_cares and acc.care_type not in mapped_cares:
        diffs["care_type"] = (acc.care_type, mapped_cares[0])

    if site.phone and normalize_phone(acc.phone) != normalize_phone(site.phone):
        diffs["phone"] = (acc.phone, site.phone)

    if acc.status != "Active":
        diffs["status"] = (acc.status, "Active")

    return diffs


def _build_chow_proposal(site: WebsiteCommunity, old_acc: CrmAccount) -> Proposal:
    """Apply CHOW SOP: keep old account parent_id intact, create Bellhaven child, link chow_current_account."""
    new_payload = {
        "name": site.name,
        "parent_id": BELLHAVEN_PARENT_ID,
        "billing_street": site.street,
        "billing_city": site.city,
        "billing_state": site.state,
        "billing_zip": site.zip_code,
        "care_type": preferred_crm_care_type(site.care_offerings, old_acc.care_type),
        "status": "Active",
        "phone": site.phone or old_acc.phone,
        "note": (
            f"CHOW successor created under Bellhaven Senior Living from prior account "
            f"{old_acc.account_id} ({old_acc.parent_name or 'previous owner'})."
        ),
    }
    old_note = (
        f"CHOW SOP: preserved under {old_acc.parent_name or 'prior parent'} due to "
        f"${old_acc.lifetime_revenue:,.0f} revenue and ${old_acc.outstanding_ar:,.0f} open AR; "
        f"new Bellhaven account linked in chow_current_account."
    )
    ops: list[dict[str, Any]] = [
        {"action": "create_account", "payload": new_payload},
        {
            "action": "patch_account",
            "account_id": old_acc.account_id,
            "payload": {
                "chow_current_account": "$CREATED_ACCOUNT_ID",
                "note": old_note,
            },
        },
    ]
    if site.administrator:
        ops.append(
            {
                "action": "create_contact",
                "payload": {
                    "account_id": "$CREATED_ACCOUNT_ID",
                    "name": site.administrator,
                    "title": "Administrator",
                    "phone": site.phone,
                    "is_active": True,
                },
            }
        )

    evidence = {
        "website": site.to_dict(),
        "crm_old_account": {
            "account_id": old_acc.account_id,
            "name": old_acc.name,
            "parent_id": old_acc.parent_id,
            "parent_name": old_acc.parent_name,
            "lifetime_revenue": old_acc.lifetime_revenue,
            "outstanding_ar": old_acc.outstanding_ar,
            "billing_address": f"{old_acc.billing_street}, {old_acc.billing_city}, {old_acc.billing_state} {old_acc.billing_zip}",
        },
        "sop_rule": "lifetime_revenue > 0 AND outstanding_ar > 0 -> preserve old parent_id, create new Bellhaven account, link chow_current_account.",
    }
    fp = _fingerprint("CHOW_CREATE_AND_LINK", old_acc.account_id, ops)
    return Proposal(
        fingerprint=fp,
        category="CHOW_CREATE_AND_LINK",
        title=f"CHOW SOP: {site.name} (Preserve {old_acc.parent_name} History & Create Bellhaven Account)",
        facility_name=site.name,
        target_account_id=old_acc.account_id,
        recommended_decision="approve",
        confidence="high",
        summary=(
            f"Address matches {old_acc.account_id} under {old_acc.parent_name}. Because lifetime_revenue is "
            f"${old_acc.lifetime_revenue:,.0f} and outstanding_ar is ${old_acc.outstanding_ar:,.0f}, CHOW SOP "
            f"forbids editing old parent_id: create a new Bellhaven account and set chow_current_account."
        ),
        evidence=evidence,
        operations=ops,
    )


def _build_update_proposal(
    site: WebsiteCommunity,
    acc: CrmAccount,
    diffs: dict[str, tuple[Any, Any]],
) -> Proposal:
    """Create direct PATCH proposal (including re-parenting when CHOW AR condition does not trigger)."""
    patch_payload = {field: new_val for field, (_, new_val) in diffs.items()}
    is_reparent = "parent_id" in diffs
    category = "REPARENT_DIRECT" if is_reparent else "FIELD_UPDATE"
    if is_reparent:
        patch_payload["note"] = (
            f"Re-parented directly to Bellhaven Senior Living per website listing ({site.slug}); "
            f"safe without CHOW split (revenue=${acc.lifetime_revenue:,.0f}, outstanding_ar=${acc.outstanding_ar:,.0f})."
        )
    else:
        changed_cols = ", ".join(sorted(diffs.keys()))
        patch_payload["note"] = f"Synced {changed_cols} from Bellhaven website ({site.slug})."

    ops = [{"action": "patch_account", "account_id": acc.account_id, "payload": patch_payload}]
    diff_rows = [
        {"field": k, "crm_current": str(old), "website_target": str(new)}
        for k, (old, new) in sorted(diffs.items())
    ]
    title_prefix = "Re-Parent & Sync" if is_reparent else "Sync Account Fields"
    reason = (
        f"Old parent '{acc.parent_name or '(none)'}' moves directly to Bellhaven (AR=${acc.outstanding_ar:,.0f} = $0). "
        if is_reparent
        else f"Account {acc.account_id} matches Bellhaven property at {site.street} with stale fields ({', '.join(diffs)})."
    )
    fp = _fingerprint(category, acc.account_id, ops)
    return Proposal(
        fingerprint=fp,
        category=category,
        title=f"{title_prefix}: {site.name}",
        facility_name=site.name,
        target_account_id=acc.account_id,
        recommended_decision="approve",
        confidence="high",
        summary=reason.strip(),
        evidence={
            "website": site.to_dict(),
            "crm_account_id": acc.account_id,
            "crm_name": acc.name,
            "crm_parent": acc.parent_name or "(none)",
            "lifetime_revenue": acc.lifetime_revenue,
            "outstanding_ar": acc.outstanding_ar,
            "field_diffs": diff_rows,
        },
        operations=ops,
    )


def _build_duplicate_proposal(site: WebsiteCommunity, loser: CrmAccount, survivor: CrmAccount) -> Proposal:
    note = (
        f"Duplicate of surviving account {survivor.account_id} ({survivor.name}) at "
        f"{site.street}, {site.city}, {site.state}. Marked Inactive via daily sync."
    )
    ops = [
        {
            "action": "patch_account",
            "account_id": loser.account_id,
            "payload": {
                "status": "Inactive",
                "duplicate_of_account": survivor.account_id,
                "note": note,
            },
        }
    ]
    fp = _fingerprint("DUPLICATE_INACTIVATE", loser.account_id, ops)
    return Proposal(
        fingerprint=fp,
        category="DUPLICATE_INACTIVATE",
        title=f"Inactivate Duplicate: {loser.name} -> Keep {survivor.name}",
        facility_name=site.name,
        target_account_id=loser.account_id,
        recommended_decision="approve",
        confidence="high",
        summary=(
            f"Account {loser.account_id} ({loser.parent_name or 'no parent'}) shares the exact physical address "
            f"{site.street}, {site.city}, {site.state} with survivor {survivor.account_id}. Set duplicate_of_account and Inactive."
        ),
        evidence={
            "website": site.to_dict(),
            "losing_account": {
                "account_id": loser.account_id,
                "name": loser.name,
                "parent": loser.parent_name or "(none)",
                "street": loser.billing_street,
                "phone": loser.phone,
                "revenue": loser.lifetime_revenue,
                "ar": loser.outstanding_ar,
            },
            "surviving_account": {
                "account_id": survivor.account_id,
                "name": survivor.name,
                "parent": survivor.parent_name or "(none)",
                "street": survivor.billing_street,
            },
        },
        operations=ops,
    )


def _build_contact_sync_proposal(
    site: WebsiteCommunity,
    acc: CrmAccount,
    acc_contacts: list[CrmContact],
) -> Proposal | None:
    """Sync website Administrator contact if missing or stale on the active account."""
    if not site.administrator:
        return None
    target_name_norm = normalize_name(site.administrator)
    active_admins = [c for c in acc_contacts if c.is_active and "administrator" in c.title.lower()]
    if any(normalize_name(c.name) == target_name_norm for c in active_admins):
        return None

    ops: list[dict[str, Any]] = []
    stale_names: list[str] = []
    for stale in active_admins:
        stale_names.append(f"{stale.name} ({stale.contact_id})")
        ops.append(
            {
                "action": "patch_contact",
                "contact_id": stale.contact_id,
                "payload": {"is_active": False},
            }
        )
    ops.append(
        {
            "action": "create_contact",
            "payload": {
                "account_id": acc.account_id,
                "name": site.administrator,
                "title": "Administrator",
                "phone": site.phone,
                "is_active": True,
            },
        }
    )
    fp = _fingerprint("CONTACT_SYNC", acc.account_id, ops)
    summary = (
        f"Website lists Administrator '{site.administrator}'. "
        + (f"Deactivate stale CRM Administrator(s): {', '.join(stale_names)} and add current Administrator." if stale_names else "Add missing Administrator contact in CRM.")
    )
    return Proposal(
        fingerprint=fp,
        category="CONTACT_SYNC",
        title=f"Sync Administrator Contact: {site.name} ({site.administrator})",
        facility_name=site.name,
        target_account_id=acc.account_id,
        recommended_decision="approve",
        confidence="high",
        summary=summary,
        evidence={
            "website_administrator": site.administrator,
            "website_phone": site.phone,
            "crm_account_id": acc.account_id,
            "stale_admin_contacts": stale_names,
        },
        operations=ops,
    )


def _find_false_positive_candidates(
    site: WebsiteCommunity,
    facility_accounts: list[CrmAccount],
) -> list[tuple[CrmAccount, str]]:
    """Identify tempting name/city matches whose address, state, and parent indicate a different facility."""
    candidates: list[tuple[CrmAccount, str]] = []
    site_name_norm = normalize_name(site.name)
    site_tokens = meaningful_name_tokens(site.name)
    site_cs = (normalize_city(site.city), normalize_state(site.state))
    site_num = extract_street_number(site.street)

    for acc in facility_accounts:
        if acc.status == "Inactive":
            continue
        if acc.parent_id == BELLHAVEN_PARENT_ID:
            continue

        acc_cs = (normalize_city(acc.billing_city), normalize_state(acc.billing_state))
        # Case 1: Identical facility name in a completely different state.
        if normalize_name(acc.name) == site_name_norm and normalize_state(acc.billing_state) != normalize_state(site.state):
            reason = (
                f"Homonym trap: exact name '{acc.name}', but located in {acc.billing_city}, {acc.billing_state} "
                f"(versus website {site.city}, {site.state}) under competitor parent {acc.parent_name or '(none)'}."
            )
            candidates.append((acc, reason))
            continue

        # Case 2: Same city+state and shared non-city facility token, but different street & non-acquired parent.
        if acc_cs == site_cs and acc.parent_name.lower() not in ACQUIRED_PARENT_NAMES:
            city_tokens = set(normalize_city(site.city).split())
            overlap = (site_tokens & meaningful_name_tokens(acc.name)) - city_tokens
            acc_num = extract_street_number(acc.billing_street)
            if overlap and site_num and acc_num and site_num != acc_num:
                reason = (
                    f"Same city/token trap: shares city {site.city}, {site.state} and token(s) {sorted(overlap)}, "
                    f"but street differs ('{acc.billing_street}' vs '{site.street}') and parent '{acc.parent_name}' "
                    f"was not acquired per Bellhaven About page."
                )
                candidates.append((acc, reason))

    return candidates


def _build_false_positive_proposals(
    site: WebsiteCommunity,
    facility_accounts: list[CrmAccount],
) -> list[Proposal]:
    """Emit explicit reject-recommended guard proposals for homonym or competitor-street lookalikes."""
    guards: list[Proposal] = []
    for cand, reason in _find_false_positive_candidates(site, facility_accounts):
        guard_ops = [
            {
                "action": "patch_account",
                "account_id": cand.account_id,
                "payload": {
                    "status": "Needs Review",
                    "note": f"Ambiguous candidate link with website {site.name} ({site.street}, {site.city}, {site.state}).",
                },
            }
        ]
        guards.append(
            Proposal(
                fingerprint=_fingerprint("FALSE_POSITIVE_GUARD", f"{site.slug}:{cand.account_id}", guard_ops),
                category="FALSE_POSITIVE_GUARD",
                title=f"Verify Ambiguous Candidate (Recommend Reject): {site.name} vs {cand.name}",
                facility_name=site.name,
                target_account_id=cand.account_id,
                recommended_decision="reject",
                confidence="low",
                summary=reason,
                evidence={
                    "website": site.to_dict(),
                    "candidate_crm_account": {
                        "account_id": cand.account_id,
                        "name": cand.name,
                        "parent": cand.parent_name or "(none)",
                        "billing_address": f"{cand.billing_street}, {cand.billing_city}, {cand.billing_state} {cand.billing_zip}",
                        "phone": cand.phone,
                    },
                    "recommendation": "REJECT: Do not link or flag competitor property; keep rejected so daily sync suppresses forever.",
                },
                operations=guard_ops,
            )
        )
    return guards


def _build_missing_proposal(site: WebsiteCommunity) -> Proposal:
    """Create proposal for a Bellhaven website community with no matching CRM account."""
    create_ops: list[dict[str, Any]] = [
        {
            "action": "create_account",
            "payload": {
                "name": site.name,
                "parent_id": BELLHAVEN_PARENT_ID,
                "billing_street": site.street,
                "billing_city": site.city,
                "billing_state": site.state,
                "billing_zip": site.zip_code,
                "care_type": preferred_crm_care_type(site.care_offerings),
                "status": "Active",
                "phone": site.phone,
                "note": f"Created from Bellhaven website ({site.slug}); discovered via {site.discovered_from}.",
            },
        }
    ]
    if site.administrator:
        create_ops.append(
            {
                "action": "create_contact",
                "payload": {
                    "account_id": "$CREATED_ACCOUNT_ID",
                    "name": site.administrator,
                    "title": "Administrator",
                    "phone": site.phone,
                    "is_active": True,
                },
            }
        )

    return Proposal(
        fingerprint=_fingerprint("CREATE_MISSING", site.slug, create_ops),
        category="CREATE_MISSING",
        title=f"Create Missing CRM Account: {site.name}",
        facility_name=site.name,
        target_account_id="NEW",
        recommended_decision="approve",
        confidence="high",
        summary=(
            f"Active community on Bellhaven website ({site.street}, {site.city}, {site.state} {site.zip_code}) "
            f"has no matching property in CRM. Create account under Bellhaven Senior Living."
        ),
        evidence={
            "website": site.to_dict(),
            "matched_crm_accounts": 0,
        },
        operations=create_ops,
    )


def _build_offboarded_proposal(acc: CrmAccount, facility_accounts: list[CrmAccount]) -> Proposal | None:
    """Handle Bellhaven-parented CRM accounts absent from the current Bellhaven website."""
    if acc.status == "Inactive" and acc.duplicate_of_account:
        return None
    if acc.chow_current_account:
        return None
    if acc.status == "Needs Review" and "offboarded" in acc.note.lower():
        return None

    acc_loc = _location_key(acc.billing_street, acc.billing_city, acc.billing_state)
    successor = next(
        (
            other
            for other in facility_accounts
            if other.account_id != acc.account_id
            and other.parent_id != BELLHAVEN_PARENT_ID
            and other.status == "Active"
            and _location_key(other.billing_street, other.billing_city, other.billing_state) == acc_loc
        ),
        None,
    )

    if successor and acc.requires_chow_sop:
        note = (
            f"CHOW divestiture: facility no longer on Bellhaven website; succeeded by {successor.name} "
            f"({successor.account_id} under {successor.parent_name}). Preserved parent/account per CHOW SOP "
            f"(revenue=${acc.lifetime_revenue:,.0f}, outstanding_ar=${acc.outstanding_ar:,.0f})."
        )
        ops = [
            {
                "action": "patch_account",
                "account_id": acc.account_id,
                "payload": {
                    "chow_current_account": successor.account_id,
                    "note": note,
                },
            }
        ]
        return Proposal(
            fingerprint=_fingerprint("OFFBOARDED_CHOW_LINK", acc.account_id, ops),
            category="OFFBOARDED_CHOW_LINK",
            title=f"Divested CHOW Link: {acc.name} -> {successor.name}",
            facility_name=acc.name,
            target_account_id=acc.account_id,
            recommended_decision="approve",
            confidence="high",
            summary=(
                f"Offboarded Bellhaven facility matches active successor {successor.name} ({successor.account_id}) "
                f"at {acc.billing_street}. Set chow_current_account and preserve old account due to ${acc.outstanding_ar:,.0f} open AR."
            ),
            evidence={
                "old_bellhaven_account": {
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "revenue": acc.lifetime_revenue,
                    "ar": acc.outstanding_ar,
                    "address": f"{acc.billing_street}, {acc.billing_city}, {acc.billing_state} {acc.billing_zip}",
                },
                "successor_crm_account": {
                    "account_id": successor.account_id,
                    "name": successor.name,
                    "parent": successor.parent_name,
                    "address": f"{successor.billing_street}, {successor.billing_city}, {successor.billing_state} {successor.billing_zip}",
                },
            },
            operations=ops,
        )

    note = (
        f"Offboarded from Bellhaven Senior Living website during daily sync; no successor account matched in CRM "
        f"(revenue=${acc.lifetime_revenue:,.0f}, outstanding_ar=${acc.outstanding_ar:,.0f}). Flagged Needs Review."
    )
    ops = [
        {
            "action": "patch_account",
            "account_id": acc.account_id,
            "payload": {
                "status": "Needs Review",
                "note": note,
            },
        }
    ]
    return Proposal(
        fingerprint=_fingerprint("OFFBOARDED_NEEDS_REVIEW", acc.account_id, ops),
        category="OFFBOARDED_NEEDS_REVIEW",
        title=f"Flag Offboarded Bellhaven Account (Needs Review): {acc.name}",
        facility_name=acc.name,
        target_account_id=acc.account_id,
        recommended_decision="approve",
        confidence="high",
        summary=(
            f"Account {acc.account_id} is currently Active under Bellhaven parent, absent from the 35 website "
            f"communities, and has no new owner at {acc.billing_street}, {acc.billing_city}, {acc.billing_state}. "
            f"Mark status='Needs Review' instead of deleting or un-parenting blindly."
        ),
        evidence={
            "crm_account": {
                "account_id": acc.account_id,
                "name": acc.name,
                "status": acc.status,
                "address": f"{acc.billing_street}, {acc.billing_city}, {acc.billing_state} {acc.billing_zip}",
                "revenue": acc.lifetime_revenue,
                "ar": acc.outstanding_ar,
            },
            "website_presence": False,
            "successor_found_in_crm": False,
        },
        operations=ops,
    )


def generate_proposals(
    communities: list[WebsiteCommunity],
    accounts: list[CrmAccount],
    contacts: list[CrmContact],
) -> list[Proposal]:
    """Reconcile website directory against CRM accounts & contacts and emit Proposals."""
    facility_accounts = [a for a in accounts if not a.is_parent_account]
    contacts_map = _contacts_by_account(contacts)
    proposals: list[Proposal] = []
    matched_crm_ids: set[str] = set()

    for site in communities:
        proposals.extend(_build_false_positive_proposals(site, facility_accounts))

        raw_matches = _find_matching_accounts(site, facility_accounts)
        for m in raw_matches:
            matched_crm_ids.add(m.account_id)

        active_matches = _filter_chow_predecessors(raw_matches)
        if not active_matches:
            proposals.append(_build_missing_proposal(site))
            continue

        ranked = sorted(active_matches, key=lambda acc: _rank_survivor(acc, site), reverse=True)
        survivor = ranked[0]
        losers = ranked[1:]

        for loser in losers:
            if loser.status != "Inactive" or loser.duplicate_of_account != survivor.account_id:
                proposals.append(_build_duplicate_proposal(site, loser, survivor))

        needs_reparent = survivor.parent_id != BELLHAVEN_PARENT_ID
        if needs_reparent and survivor.requires_chow_sop:
            proposals.append(_build_chow_proposal(site, survivor))
            continue

        diffs = _compute_field_diff(site, survivor, include_parent=needs_reparent)
        if diffs:
            proposals.append(_build_update_proposal(site, survivor, diffs))

        contact_prop = _build_contact_sync_proposal(site, survivor, contacts_map.get(survivor.account_id, []))
        if contact_prop:
            proposals.append(contact_prop)

    for acc in facility_accounts:
        if acc.parent_id == BELLHAVEN_PARENT_ID and acc.account_id not in matched_crm_ids:
            off_prop = _build_offboarded_proposal(acc, facility_accounts)
            if off_prop:
                proposals.append(off_prop)

    return proposals
