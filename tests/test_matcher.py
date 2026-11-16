"""Unit tests for matching rules, CHOW SOP, deduplication, and false-positive guards."""

import unittest

from bellhaven_sync.config import BELLHAVEN_PARENT_ID
from bellhaven_sync.matcher import generate_proposals
from bellhaven_sync.models import CrmAccount, CrmContact, WebsiteCommunity


def _site(
    slug: str,
    name: str,
    street: str,
    city: str,
    state: str,
    zip_code: str,
    care: tuple[str, ...] = ("Assisted Living",),
    admin: str = "Jane Doe",
    phone: str = "(555) 111-2222",
) -> WebsiteCommunity:
    return WebsiteCommunity(
        slug=slug,
        url=f"https://example.test{slug}",
        name=name,
        street=street,
        city=city,
        state=state,
        zip_code=zip_code,
        care_offerings=care,
        administrator=admin,
        phone=phone,
    )


def _acc(
    account_id: str,
    name: str,
    parent_id: str,
    parent_name: str,
    street: str,
    city: str,
    state: str,
    zip_code: str,
    care: str = "Assisted Living",
    status: str = "Active",
    phone: str = "(555) 111-2222",
    rev: float = 0,
    ar: float = 0,
    chow: str = "",
    dup: str = "",
) -> CrmAccount:
    return CrmAccount(
        account_id=account_id,
        name=name,
        parent_id=parent_id,
        parent_name=parent_name,
        billing_street=street,
        billing_city=city,
        billing_state=state,
        billing_zip=zip_code,
        care_type=care,
        status=status,
        phone=phone,
        lifetime_revenue=rev,
        outstanding_ar=ar,
        chow_current_account=chow,
        duplicate_of_account=dup,
    )


class MatcherTests(unittest.TestCase):
    def test_chow_sop_triggered_only_when_rev_and_ar_positive(self) -> None:
        sites = [
            _site("/communities/marietta", "Bellhaven of Marietta", "805 Colegate Dr", "Marietta", "OH", "45750", ("Short-Term Rehabilitation & Nursing",)),
            _site("/communities/lima", "Bellhaven Crossings of Lima", "3115 N Cole St", "Lima", "OH", "45801"),
        ]
        accounts = [
            _acc("OLD_MAR", "Bellhaven of Marietta", "CEDAR_ID", "Cedar Trail Communities (Parent Account)", "805 Colegate Drive", "Marietta", "OH", "45750", "Skilled Nursing", rev=51250, ar=3800),
            _acc("OLD_LIMA", "Bellhaven Crossings of Lima", "HARB_ID", "Harborview Care Group (Parent Account)", "3115 N Cole St", "Lima", "OH", "45801", "Assisted Living", rev=47000, ar=0),
        ]
        props = generate_proposals(sites, accounts, [])
        by_key = {(p.target_account_id, p.category): p for p in props}

        self.assertIn(("OLD_MAR", "CHOW_CREATE_AND_LINK"), by_key)
        patch_old_mar = [op for op in by_key[("OLD_MAR", "CHOW_CREATE_AND_LINK")].operations if op["action"] == "patch_account"][0]
        self.assertNotIn("parent_id", patch_old_mar["payload"])
        self.assertEqual(patch_old_mar["payload"]["chow_current_account"], "$CREATED_ACCOUNT_ID")

        self.assertIn(("OLD_LIMA", "REPARENT_DIRECT"), by_key)
        self.assertEqual(by_key[("OLD_LIMA", "REPARENT_DIRECT")].operations[0]["payload"]["parent_id"], BELLHAVEN_PARENT_ID)

    def test_chow_predecessor_is_not_flagged_as_duplicate_on_rerun(self) -> None:
        sites = [_site("/communities/marietta", "Bellhaven of Marietta", "805 Colegate Dr", "Marietta", "OH", "45750", ("Short-Term Rehabilitation & Nursing",))]
        accounts = [
            _acc("OLD_MAR", "Bellhaven of Marietta", "CEDAR_ID", "Cedar Trail Communities (Parent Account)", "805 Colegate Drive", "Marietta", "OH", "45750", "Skilled Nursing", rev=51250, ar=3800, chow="NEW_MAR"),
            _acc("NEW_MAR", "Bellhaven of Marietta", BELLHAVEN_PARENT_ID, "Bellhaven Senior Living (Parent Account)", "805 Colegate Dr", "Marietta", "OH", "45750", "Skilled Nursing", rev=0, ar=0),
        ]
        contacts = [CrmContact("C1", "NEW_MAR", "Jane Doe", "Administrator", "", "(555) 111-2222", True)]
        props = generate_proposals(sites, accounts, contacts)
        self.assertEqual(props, [])

    def test_false_positive_guards_missing_locations(self) -> None:
        sites = [
            _site("/communities/amberly", "Amberly Manor", "4390 Darrow Rd", "Hudson", "OH", "44236"),
            _site("/communities/union-sq", "Bellhaven at Union Square", "118 Union Square Dr", "New Albany", "OH", "43054", admin="Phil Holloway"),
        ]
        accounts = [
            _acc("CO_AMB", "Amberly Manor", "JUNIPER", "Juniper Point Healthcare (Parent Account)", "918 S Nevada Ave", "Colorado Springs", "CO", "80903"),
            _acc("MK_UNI", "Union Square Senior Living", "JUNIPER", "Juniper Point Healthcare (Parent Account)", "240 Market St", "New Albany", "OH", "43054"),
        ]
        props = generate_proposals(sites, accounts, [])
        cats = [(p.category, p.target_account_id, p.recommended_decision) for p in props]
        self.assertIn(("CREATE_MISSING", "NEW", "approve"), cats)
        self.assertIn(("FALSE_POSITIVE_GUARD", "CO_AMB", "reject"), cats)
        self.assertIn(("FALSE_POSITIVE_GUARD", "MK_UNI", "reject"), cats)

    def test_offboarded_chow_and_needs_review(self) -> None:
        sites: list[WebsiteCommunity] = []
        accounts = [
            _acc("SAND_BH", "Bellhaven of Sandusky", BELLHAVEN_PARENT_ID, "Bellhaven Senior Living (Parent Account)", "2715 Columbus Ave", "Sandusky", "OH", "44870", "Skilled Nursing", rev=130000, ar=5200),
            _acc("SAND_MIL", "Millstone Care of Sandusky", "MILL_ID", "Millstone Health Partners (Parent Account)", "2715 Columbus Ave", "Sandusky", "OH", "44870", "Skilled Nursing"),
            _acc("ALLI_BH", "Bellhaven Care Center of Alliance", BELLHAVEN_PARENT_ID, "Bellhaven Senior Living (Parent Account)", "1785 Freshley Ave", "Alliance", "OH", "44601"),
        ]
        props = generate_proposals(sites, accounts, [])
        by_target = {p.target_account_id: p for p in props}
        self.assertEqual(by_target["SAND_BH"].category, "OFFBOARDED_CHOW_LINK")
        self.assertEqual(by_target["SAND_BH"].operations[0]["payload"]["chow_current_account"], "SAND_MIL")
        self.assertEqual(by_target["ALLI_BH"].category, "OFFBOARDED_NEEDS_REVIEW")
        self.assertEqual(by_target["ALLI_BH"].operations[0]["payload"]["status"], "Needs Review")


if __name__ == "__main__":
    unittest.main()
