"""Scraper for Bellhaven Senior Living public community directory and homepage."""

from __future__ import annotations

import html
import re
import urllib.request
from typing import Callable

from .models import WebsiteCommunity

COMMUNITY_LINK_RE = re.compile(r'href=["\'](/communities/[a-z0-9-]+)["\']', re.I)
NEXT_PAGE_RE = re.compile(r'href=["\'](/communities\?page=\d+)["\']', re.I)
TAG_RE = re.compile(r"<[^>]+>")
DETAIL_ITEM_RE = re.compile(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", re.S | re.I)
BADGE_RE = re.compile(r'<span[^>]*class=["\'][^"\']*badge[^"\']*["\'][^>]*>(.*?)</span>', re.S | re.I)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def default_fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "bellhaven-sync/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _clean_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_community_detail(page_html: str, slug: str, base_url: str, discovered_from: str) -> WebsiteCommunity:
    """Parse structured fields from a single /communities/<slug> detail page."""
    h1_match = H1_RE.search(page_html)
    name = _clean_text(h1_match.group(1)) if h1_match else slug

    fields: dict[str, str] = {}
    care_offerings: list[str] = []
    for raw_label, raw_val in DETAIL_ITEM_RE.findall(page_html):
        label = _clean_text(raw_label).lower()
        if label == "care offerings":
            badges = [_clean_text(b) for b in BADGE_RE.findall(raw_val)]
            care_offerings = [b for b in badges if b] or [
                part.strip() for part in _clean_text(raw_val).splitlines() if part.strip()
            ]
        else:
            fields[label] = _clean_text(raw_val)

    street, city, state, zip_code = _parse_address(fields.get("address", ""))
    return WebsiteCommunity(
        slug=slug,
        url=f"{base_url.rstrip('/')}{slug}",
        name=name,
        street=street,
        city=city,
        state=state,
        zip_code=zip_code,
        care_offerings=tuple(care_offerings),
        administrator=fields.get("administrator", ""),
        phone=fields.get("phone", ""),
        discovered_from=discovered_from,
    )


def _parse_address(address_block: str) -> tuple[str, str, str, str]:
    lines = [line.strip() for line in address_block.splitlines() if line.strip()]
    street = lines[0] if lines else ""
    city, state, zip_code = "", "", ""
    tail = lines[1] if len(lines) > 1 else ""
    match = re.match(r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", tail)
    if match:
        city, state, zip_code = match.group(1).strip(), match.group(2), match.group(3)
    return street, city, state, zip_code


class BellhavenScraper:
    """Fetches every Bellhaven community across paginated directory + homepage callouts."""

    def __init__(self, site_base: str, fetcher: Callable[[str], str] | None = None) -> None:
        self.site_base = site_base.rstrip("/")
        self._fetch = fetcher or default_fetch_html

    def discover_community_paths(self) -> dict[str, str]:
        """Return mapping {slug_path: discovered_source} across / and /communities?page=N."""
        discovered: dict[str, str] = {}

        # Crawl homepage as well as /communities pagination so newly featured facilities are not missed.
        home_html = self._fetch(f"{self.site_base}/")
        for path in COMMUNITY_LINK_RE.findall(home_html):
            discovered.setdefault(path, "homepage")

        queue = ["/communities"]
        visited_pages: set[str] = set()
        while queue:
            page_path = queue.pop(0)
            if page_path in visited_pages:
                continue
            visited_pages.add(page_path)
            page_html = self._fetch(f"{self.site_base}{page_path}")
            for path in COMMUNITY_LINK_RE.findall(page_html):
                discovered[path] = "directory" if path not in discovered else discovered[path]
            for next_path in NEXT_PAGE_RE.findall(page_html):
                if next_path not in visited_pages and next_path not in queue:
                    queue.append(next_path)

        return dict(sorted(discovered.items()))

    def scrape_all(self) -> list[WebsiteCommunity]:
        """Fetch and parse every discovered community detail page."""
        discovered = self.discover_community_paths()
        communities: list[WebsiteCommunity] = []
        for slug, source in discovered.items():
            detail_html = self._fetch(f"{self.site_base}{slug}")
            communities.append(parse_community_detail(detail_html, slug, self.site_base, source))
        return communities
