"""Unit tests for BellhavenScraper discovery across homepage callouts and pagination."""

import unittest

from bellhaven_sync.scraper import BellhavenScraper, parse_community_detail


class ScraperTests(unittest.TestCase):
    def test_discovers_homepage_only_and_paginated_communities(self) -> None:
        pages = {
            "https://example.test/": """
                <div class="notice">
                  Welcome <a href="/communities/bellhaven-meadows-of-findlay">Bellhaven Meadows of Findlay</a>
                </div>
            """,
            "https://example.test/communities": """
                <a href="/communities/bellhaven-of-batavia">Batavia</a>
                <a href="/communities?page=2">Next</a>
            """,
            "https://example.test/communities?page=2": """
                <a href="/communities/bellhaven-rehabilitation-nursing-of-grove-city">Grove City</a>
            """,
            "https://example.test/communities/bellhaven-meadows-of-findlay": """
                <h1>Bellhaven Meadows of Findlay</h1>
                <dl class="detail">
                  <dt>Address</dt><dd>1800 N Blanchard St<br>Findlay, OH 45840</dd>
                  <dt>Care Offerings</dt><dd><span class="badge">Assisted Living</span><span class="badge">Memory Support</span></dd>
                  <dt>Administrator</dt><dd>Sam Pruitt</dd>
                  <dt>Phone</dt><dd>(231) 533-2969</dd>
                </dl>
            """,
            "https://example.test/communities/bellhaven-of-batavia": """
                <h1>Bellhaven of Batavia</h1>
                <dl class="detail">
                  <dt>Address</dt><dd>2000 Hospital Dr<br>Batavia, OH 45103</dd>
                  <dt>Care Offerings</dt><dd><span class="badge">Short-Term Rehabilitation &amp; Nursing</span></dd>
                  <dt>Administrator</dt><dd>Elaine Lindqvist</dd>
                  <dt>Phone</dt><dd>(231) 698-3092</dd>
                </dl>
            """,
            "https://example.test/communities/bellhaven-rehabilitation-nursing-of-grove-city": """
                <h1>Bellhaven Rehabilitation &amp; Nursing of Grove City</h1>
                <dl class="detail">
                  <dt>Address</dt><dd>3985 Broadway<br>Grove City, OH 43123</dd>
                  <dt>Care Offerings</dt><dd><span class="badge">Short-Term Rehabilitation &amp; Nursing</span></dd>
                  <dt>Administrator</dt><dd>Marie Vogel</dd>
                  <dt>Phone</dt><dd>(614) 555-7729</dd>
                </dl>
            """,
        }

        scraper = BellhavenScraper("https://example.test", fetcher=lambda u: pages[u])
        communities = scraper.scrape_all()
        by_slug = {c.slug: c for c in communities}

        self.assertEqual(len(communities), 3)
        self.assertIn("/communities/bellhaven-meadows-of-findlay", by_slug)
        self.assertEqual(by_slug["/communities/bellhaven-meadows-of-findlay"].discovered_from, "homepage")
        self.assertEqual(
            by_slug["/communities/bellhaven-meadows-of-findlay"].care_offerings,
            ("Assisted Living", "Memory Support"),
        )
        self.assertEqual(
            by_slug["/communities/bellhaven-rehabilitation-nursing-of-grove-city"].name,
            "Bellhaven Rehabilitation & Nursing of Grove City",
        )


if __name__ == "__main__":
    unittest.main()
