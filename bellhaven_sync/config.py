"""Configuration loader and domain constants."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SITE_BASE = "https://analyst-assessment-production.up.railway.app"
DEFAULT_API_BASE = f"{DEFAULT_SITE_BASE}/api/v1"

BELLHAVEN_PARENT_ID = "0015QAPLGS3FVYEEEM"
BELLHAVEN_PARENT_NAME = "Bellhaven Senior Living (Parent Account)"

# Operators explicitly acquired per /about; used as a positive signal alongside address matching.
ACQUIRED_PARENT_NAMES = {
    "harborview care group (parent account)",
    "cedar trail communities (parent account)",
}

# Canonical website care offering strings -> valid CRM care_type enum values.
CARE_OFFERING_MAP = {
    "assisted living": "Assisted Living",
    "independent living": "Independent Living",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "short term rehabilitation & nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
}


def load_dotenv(env_path: Path) -> None:
    """Populate os.environ from a simple KEY=VALUE .env file without overwriting set vars."""
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    """Runtime settings for scraping, CRM API access, state persistence, and review UI."""

    site_base: str
    api_base: str
    api_token: str
    crm_browser_url: str
    db_path: Path
    review_host: str
    review_port: int

    @classmethod
    def from_env(cls, root: Path = PROJECT_ROOT) -> "Settings":
        load_dotenv(root / ".env")
        token = os.environ.get("BELLHAVEN_API_TOKEN", "").strip()
        site_base = os.environ.get("BELLHAVEN_SITE_BASE", DEFAULT_SITE_BASE).rstrip("/")
        api_base = os.environ.get("BELLHAVEN_API_BASE", f"{site_base}/api/v1").rstrip("/")
        default_browser = f"{site_base}/crm/{token}/accounts" if token else f"{site_base}/"
        crm_browser_url = os.environ.get("BELLHAVEN_CRM_BROWSER_URL", default_browser)
        db_path = Path(os.environ.get("BELLHAVEN_STATE_DB", str(root / "data" / "state.db")))
        review_host = os.environ.get("REVIEW_HOST", "127.0.0.1")
        review_port = int(os.environ.get("REVIEW_PORT", "8765"))
        return cls(
            site_base=site_base,
            api_base=api_base,
            api_token=token,
            crm_browser_url=crm_browser_url,
            db_path=db_path,
            review_host=review_host,
            review_port=review_port,
        )
