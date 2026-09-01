"""Pure criteria evaluation: attribute extraction and listing scoring.

Owns the criteria rule language and the extractor spec. Never touches
the database or the network; everything here is dict-in/dict-out so it
tests without I/O. Callers rely on: score is in [0, 1], unknown fields
earn 0 but only a *present, contradicting* value hard-fails a required
rule, and extract_attrs() is case-insensitive over title+description.

Extractor spec (product.extractors), field name -> spec:
  {"pattern": regex, "type": "int"|"float"|"str"|"bool"|"size_gb", "group": 1}
"bool" means presence of the pattern; "size_gb" reads `(\\d+) (gb|tb)`
from groups 1 and 2 and normalizes to GB.

Criteria rule (product.criteria), one dict per rule:
  {"field": name, "op": op, "value": v, "weight": 1.0,
   "required": false, "reject": false, "note": ""}
A violated required rule flags the listing (hard_fails); a violated
reject rule means the row is not the product at all — see rejected().
ops: gte, lte, eq, contains, one_of, matches, exists.
Fields resolve from listing attrs first, then listing columns
(price, seller_rating, seller_feedback_count, condition, ...).
"""

import re
from statistics import median

OPS = ("gte", "lte", "eq", "contains", "one_of", "matches", "exists")


def extract_attrs(text: str, extractors: dict) -> dict:
    attrs: dict = {}
    for field, spec in extractors.items():
        m = re.search(spec["pattern"], text, re.IGNORECASE)
        kind = spec.get("type", "str")
        if kind == "bool":
            attrs[field] = m is not None
            continue
        if not m:
            continue
        if kind == "size_gb":
            n = float(m.group(1))
            unit = (m.group(2) or "gb").lower()
            attrs[field] = n * 1000 if unit == "tb" else n
        else:
            raw = m.group(spec.get("group", 1) if m.groups() else 0)
            if kind == "int":
                attrs[field] = int(raw)
            elif kind == "float":
                attrs[field] = float(raw)
            else:
                attrs[field] = raw.strip().lower()
    return attrs


def _passes(op: str, actual, expected) -> bool:
    if op == "exists":
        return actual is not None
    if op == "gte":
        return float(actual) >= float(expected)
    if op == "lte":
        return float(actual) <= float(expected)
    if op == "eq":
        return str(actual).lower() == str(expected).lower()
    if op == "contains":
        return str(expected).lower() in str(actual).lower()
    if op == "one_of":
        return str(actual).lower() in [str(v).lower() for v in expected]
    if op == "matches":
        return re.search(str(expected), str(actual), re.IGNORECASE) is not None
    raise ValueError(f"unknown op: {op}")


def rejected(merged: dict, criteria: list[dict]) -> str | None:
    """Return the note of the first violated reject-rule, else None.

    A rule with "reject": true marks listings that are not the product
    at all (parts, accessories, wrong category). Callers discard such
    rows at ingest instead of storing them flagged.
    """
    for rule in criteria:
        if not rule.get("reject"):
            continue
        actual = merged.get(rule["field"])
        if actual is None:
            continue
        try:
            if not _passes(rule["op"], actual, rule.get("value")):
                return rule.get("note") or rule["field"]
        except (TypeError, ValueError):
            continue
    return None


def score_listing(merged: dict, criteria: list[dict]) -> tuple[float, list[str]]:
    """Score one listing dict (attrs merged over columns) against criteria.

    Returns (score, hard_fails). hard_fails lists the notes/fields of
    required rules a present value contradicted.
    """
    total = earned = 0.0
    hard_fails: list[str] = []
    for rule in criteria:
        weight = float(rule.get("weight", 1.0))
        total += weight
        actual = merged.get(rule["field"])
        if actual is None:
            continue
        try:
            ok = _passes(rule["op"], actual, rule.get("value"))
        except (TypeError, ValueError):
            continue
        if ok:
            earned += weight
        elif rule.get("required"):
            hard_fails.append(rule.get("note") or rule["field"])
    return (earned / total if total else 0.0, hard_fails)


def annotate_deals(listings: list[dict]) -> list[dict]:
    """Add price context to scored listings: median and % below it.

    Median is computed over the priced listings given; listings without
    a price get no annotation. Pure; sorts nothing.
    """
    prices = [li["price"] for li in listings if li.get("price")]
    if not prices:
        return listings
    med = median(prices)
    for li in listings:
        if li.get("price"):
            li["median_price"] = med
            li["pct_vs_median"] = round((li["price"] - med) / med * 100, 1)
    return listings
