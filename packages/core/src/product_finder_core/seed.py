"""Seed data: the thin-client laptop product this project was born for.

Owns only initial data; storage.py owns persistence. Never runs
automatically — callers (CLI `seed`, MCP `seed_defaults`) opt in. The
criteria/extractors here double as the reference example for defining
any new product over the generic rule language in scoring.py.
"""

from . import storage

LAPTOP = {
    "slug": "thin-client-laptop",
    "name": "Thin-client laptop (ThinkPad X1 Carbon Gen 6+)",
    "description": (
        "Light, big-screen laptop with good battery and keyboard for RDP work; "
        "low local specs are fine."
    ),
    "queries": [
        "ThinkPad X1 Carbon Gen 6",
        "ThinkPad X1 Carbon Gen 7",
        "Lenovo X1 Carbon i5 16GB",
        "ThinkPad X1 Carbon",
    ],
    "extractors": {
        "ram_gb": {"pattern": r"(\d+)\s*gb\b(?!\s*(?:ssd|nvme|hdd))", "type": "int"},
        "storage_gb": {
            "pattern": r"(\d+(?:\.\d+)?)\s*(gb|tb)\s*(?:ssd|nvme|pcie)",
            "type": "size_gb",
        },
        "nvme": {"pattern": r"\bnvme\b|\bpcie\b", "type": "bool"},
        "ips": {"pattern": r"\bips\b", "type": "bool"},
        "fhd": {"pattern": r"1920\s*x\s*1080|\bfhd\b|full\s*hd", "type": "bool"},
        "lowres": {"pattern": r"1366\s*x\s*768|\bhd\b(?!\+| display port)", "type": "bool"},
        "cpu": {"pattern": r"\b(i[57]-8[0-9]{3}u?)\b", "type": "str"},
        "carbon_gen": {"pattern": r"x1\s*carbon\s*(?:gen\s*|g)(\d+)", "type": "int"},
        "touch": {"pattern": r"\btouch(?:screen)?\b", "type": "bool"},
        "has_specs": {
            "pattern": (
                r"i[3579]-\d|\bi[3579]\b|ryzen|\d+\s*gb|\bssd\b|nvme|fhd|wqhd|uhd|qhd"
                r"|touch|laptop|ultrabook|notebook|\bgen\b|\bwin(?:dows)?\b|vpro"
            ),
            "type": "bool",
        },
        "is_parts": {
            "pattern": (
                r"for parts|parts only|as[- ]is|not working|no power|broken|cracked"
                r"|(?:lcd|screen|display) (?:assembly|replacement)|lcd screen|screen display"
                r"|display ass?embly|motherboard|mainboard|logic board|\bcase for\b"
                r"|docking station|\bdock\b|multi[- ]?port|\bhub\b|adapter charger"
                r"|charger adapter|\bsleeve\b|privacy filter|screen protector"
                r"|palmrest|bezel|hinge|heatsink|cooling fan|bottom (?:case|cover)|top cover"
                r"|(?:keyboard|battery|charger|adapter|fan|speaker|screen) for\b"
                r"|replacement (?:screen|keyboard|battery)|digitizer|ac adapter|power adapter"
            ),
            "type": "bool",
        },
    },
    "criteria": [
        {
            "field": "ram_gb",
            "op": "gte",
            "value": 16,
            "weight": 3,
            "required": True,
            "note": "16GB RAM (soldered; 8GB bottlenecks Chrome+RDP)",
        },
        {"field": "storage_gb", "op": "gte", "value": 256, "weight": 1, "note": "256GB minimum"},
        {
            "field": "nvme",
            "op": "eq",
            "value": True,
            "weight": 2,
            "note": "NVMe, not SATA (boot/resume speed)",
        },
        {"field": "fhd", "op": "eq", "value": True, "weight": 2, "note": "1920x1080"},
        {
            "field": "ips",
            "op": "eq",
            "value": True,
            "weight": 2,
            "note": "IPS panel; skip listings that only say HD",
        },
        {
            "field": "lowres",
            "op": "eq",
            "value": False,
            "weight": 3,
            "required": True,
            "note": "avoid 1366x768 base configs",
        },
        {
            "field": "cpu",
            "op": "matches",
            "value": r"i[57]-8",
            "weight": 2,
            "note": "8th-gen i5/i7 minimum",
        },
        {
            "field": "carbon_gen",
            "op": "gte",
            "value": 6,
            "weight": 2,
            "required": True,
            "note": "Gen 6+ (under 2.8 lbs)",
        },
        {
            "field": "has_specs",
            "op": "eq",
            "reject": True,
            "value": True,
            "weight": 1,
            "required": True,
            "note": "title has laptop specs (CPU/RAM/screen/laptop), not an accessory name",
        },
        {
            "field": "is_parts",
            "op": "eq",
            "reject": True,
            "value": False,
            "weight": 2,
            "required": True,
            "note": "whole working laptop, not parts/accessories/broken",
        },
        {
            "field": "seller_rating",
            "op": "gte",
            "value": 98,
            "weight": 2,
            "note": "98%+ seller feedback",
        },
        {
            "field": "seller_feedback_count",
            "op": "gte",
            "value": 1000,
            "weight": 1,
            "note": "1000+ transactions",
        },
    ],
    "manual_checks": [
        "Ask seller for battery cycle count / health % via Lenovo Vantage or BIOS — "
        "biggest risk on 4-7 year old units",
        "Photos: key shine/wear, missing or sticky keys, trackpad clicks",
        "Photos: dead pixels, backlight bleed, staining, cracks",
        "Confirm at least one USB-C/Thunderbolt and one USB-A port",
    ],
    "max_price": None,
}


def seed(conn) -> list[str]:
    """Insert/refresh seed products; returns the slugs written."""
    storage.upsert_product(conn, LAPTOP)
    return [LAPTOP["slug"]]
