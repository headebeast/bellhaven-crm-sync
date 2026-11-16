"""Normalization and similarity helpers for addresses, names, phones, and care types."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .config import CARE_OFFERING_MAP

STREET_ABBREVIATIONS = {
    "street": "st",
    "st": "st",
    "avenue": "ave",
    "ave": "ave",
    "road": "rd",
    "rd": "rd",
    "drive": "dr",
    "dr": "dr",
    "boulevard": "blvd",
    "blvd": "blvd",
    "lane": "ln",
    "ln": "ln",
    "court": "ct",
    "ct": "ct",
    "circle": "cir",
    "cir": "cir",
    "pike": "pike",
    "pk": "pike",
    "parkway": "pkwy",
    "pkwy": "pkwy",
    "highway": "hwy",
    "hwy": "hwy",
    "northwest": "nw",
    "northeast": "ne",
    "southwest": "sw",
    "southeast": "se",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}

NOISE_NAME_WORDS = {
    "bellhaven",
    "senior",
    "living",
    "care",
    "center",
    "centre",
    "healthcare",
    "health",
    "rehab",
    "rehabilitation",
    "nursing",
    "assisted",
    "memory",
    "retirement",
    "home",
    "commons",
    "manor",
    "estates",
    "gardens",
    "village",
    "campus",
    "of",
    "at",
    "the",
    "and",
    "cedar",
    "trail",
    "harborview",
}


def normalize_street(street: str) -> str:
    """Canonicalize street string for abbreviation-insensitive comparison."""
    cleaned = re.sub(r"[^\w\s]", " ", (street or "").lower()).strip()
    tokens = [STREET_ABBREVIATIONS.get(tok, tok) for tok in cleaned.split()]
    return " ".join(tokens)


def is_po_box(street: str) -> bool:
    return bool(re.search(r"\bp\.?\s*o\.?\s*box\b", (street or ""), re.I))


def extract_street_number(street: str) -> str:
    match = re.match(r"^\s*(\d+[a-z]?)\b", (street or "").lower())
    return match.group(1) if match else ""


def normalize_city(city: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (city or "").lower())).strip()


def normalize_state(state: str) -> str:
    return (state or "").strip().upper()


def normalize_zip(zip_code: str) -> str:
    digits = re.sub(r"\D", "", zip_code or "")
    return digits[:5]


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_name(name: str) -> str:
    cleaned = (name or "").replace("&", " and ")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned.lower())
    return " ".join(cleaned.split())


def meaningful_name_tokens(name: str) -> set[str]:
    tokens = set(normalize_name(name).split())
    return {t for t in tokens if t not in NOISE_NAME_WORDS and len(t) > 1}


def name_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_name(left), normalize_name(right)).ratio()


def map_care_offerings(offerings: tuple[str, ...] | list[str]) -> list[str]:
    """Map website care labels to ordered deduplicated CRM care_type enum values."""
    mapped: list[str] = []
    for raw in offerings:
        key = re.sub(r"\s+", " ", raw.lower()).strip()
        crm_val = CARE_OFFERING_MAP.get(key)
        if crm_val and crm_val not in mapped:
            mapped.append(crm_val)
    return mapped


def preferred_crm_care_type(offerings: tuple[str, ...] | list[str], current_care_type: str = "") -> str:
    """Keep valid existing CRM care_type if in mapped website set, else pick first mapped offering."""
    mapped = map_care_offerings(offerings)
    if not mapped:
        return current_care_type
    if current_care_type in mapped:
        return current_care_type
    return mapped[0]
