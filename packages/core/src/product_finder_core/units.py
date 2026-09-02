"""Pack-size parsing: listing title -> total ounces (or count) -> price per unit.

Owns the one rule for reading "4 x 11 fl oz", "12 ct / 11 fl oz",
"22 count, 17.6 oz", "2 lb", "750 ml" out of a title and reducing it
to a single comparable quantity. Weight and volume both normalize to
ounces ("oz" — fluid ounces are treated as ounces, which is what a
shelf-tag "$/oz" does too); a bare count ("10 ct") is the fallback
unit "ct" when no weight/volume is present. Pure functions, no I/O;
callers (ingest, backfill) store the result on the listing.

Conventions the parser relies on, learned from the grocery parsers:
  - the LAST size mention wins — parsers append the canonical size to
    the end of the title, ahead of whatever the name itself says;
  - "N x M oz" and Kroger's "N ct / M oz" mean N packages of M each
    (multiply); a comma-separated ", M oz" after a count is the total;
  - a gram figure with "protein"/"fiber"/"sugar" within the next few
    words ("30g Vanilla Protein Shake") is a nutrition claim, not a
    weight.
Never guesses a size from a bare number, and never returns a unit
price for a listing with no price or no size.
"""

from __future__ import annotations

import re

_TO_OZ = {
    "oz": 1.0,
    "fl oz": 1.0,
    "lb": 16.0,
    "g": 1 / 28.3495,
    "kg": 35.274,
    "ml": 1 / 29.5735,
    "l": 33.814,
    "gal": 128.0,
    "qt": 32.0,
    "pt": 16.0,
}

_UNIT_RE = (
    r"(fl\.?\s*oz|fluid\s+ounces?|oz|ounces?|lbs?|pounds?|kg|kilograms?|g|grams?"
    r"|ml|milliliters?|liters?|litres?|l|gal(?:lons?)?|qt|quarts?|pt|pints?)"
)
_NUM = r"(\d+(?:\.\d+)?|\.\d+)"
# "4 x 11 fl oz" / "12 ct / 11 fl oz" / "40 pk / .8 oz": N packages of M each.
_MULTI_RE = re.compile(
    rf"\b(\d{{1,3}})\s*(?:[x\u00d7]|(?:ct|count|pk|pack|pouches?|bottles?|cans?)\s*/)\s*{_NUM}\s*{_UNIT_RE}\b",
    re.I,
)
_SIZE_RE = re.compile(rf"(?<![\w.\-/]){_NUM}(\s*){_UNIT_RE}\b", re.I)
_COUNT_RE = re.compile(
    r"\b(\d{1,3})\s*(?:-\s*)?(?:ct|count|pk|pack|pouches|bottles|cans|bars|rolls)\b", re.I
)
_NUTRITION_RE = re.compile(r"\b(?:protein|fiber|fibre|sugar|carbs?|caffeine)\b", re.I)


def _not_a_weight(text: str, m: re.Match) -> bool:
    """Gram figures that aren't weights: a nutrient word within ~40 chars
    ("30g Vanilla Protein Shake", "42g High Protein"), or an uppercase G
    glued to the number ("256G" SSD, "4G LTE") — electronics, not grams."""
    if _canon(m.group(3)) != "g":
        return False
    if m.group(3) == "G" and not m.group(2):
        return True
    return bool(_NUTRITION_RE.search(text[m.end() : m.end() + 40]))


def _canon(unit: str) -> str:
    u = unit.lower().replace(".", "")
    u = re.sub(r"\s+", " ", u)
    if u.startswith("fl") or u.startswith("fluid"):
        return "fl oz"
    if u.startswith("ounce"):
        return "oz"
    if u.startswith("lb") or u.startswith("pound"):
        return "lb"
    if u.startswith("kilo") or u == "kg":
        return "kg"
    if u.startswith("gram") or u == "g":
        return "g"
    if u.startswith("milli") or u == "ml":
        return "ml"
    if u.startswith("lit") or u == "l":
        return "l"
    if u.startswith("gal"):
        return "gal"
    if u.startswith("q"):
        return "qt"
    if u.startswith("p"):
        return "pt"
    return u


def pack_size(title: str) -> tuple[float, str] | None:
    """(quantity, unit) for a title, unit "oz" or "ct"; None if no size found."""
    text = title or ""
    multi = list(_MULTI_RE.finditer(text))
    spans = [(m.start(), m.end()) for m in multi]
    sizes = [
        m
        for m in _SIZE_RE.finditer(text)
        if not _not_a_weight(text, m) and not any(a <= m.start() < b for a, b in spans)
    ]
    best_oz = None
    if multi:
        m = multi[-1]
        best_oz = (m.start(), int(m.group(1)) * float(m.group(2)) * _TO_OZ[_canon(m.group(3))])
    if sizes:
        m = sizes[-1]
        oz = float(m.group(1)) * _TO_OZ[_canon(m.group(3))]
        if best_oz is None or m.start() > best_oz[0]:
            best_oz = (m.start(), oz)
    if best_oz is not None and best_oz[1] > 0:
        return (round(best_oz[1], 4), "oz")
    counts = list(_COUNT_RE.finditer(text))
    if counts:
        n = int(counts[-1].group(1))
        if n > 0:
            return (float(n), "ct")
    return None


def unit_price(price: float | None, title: str) -> dict:
    """{unit_qty, unit, unit_price} for a listing — all None when unknown."""
    size = pack_size(title)
    if size is None or price is None or price <= 0:
        return {"unit_qty": None, "unit": None, "unit_price": None}
    qty, unit = size
    return {"unit_qty": qty, "unit": unit, "unit_price": round(price / qty, 4)}
