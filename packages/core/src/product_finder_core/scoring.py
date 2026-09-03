"""Pure criteria evaluation: attribute extraction and listing scoring.

Owns the criteria rule language and the extractor spec. Never touches
the database or the network; everything here is dict-in/dict-out so it
tests without I/O. Callers rely on: score is in [0, 1], unknown fields
earn 0 but only a *present, contradicting* value hard-fails a required
rule, and extract_attrs() is case-insensitive over title+description.

Extractor spec (product.extractors), field name -> spec:
  {"pattern": regex, "type": "int"|"float"|"str"|"bool"|"size_gb", "group": 1,
   "fields": ["title"]}
"bool" means presence of the pattern; "size_gb" reads `(\\d+) (gb|tb)`
from groups 1 and 2 and normalizes to GB. "fields" names which listing
fields the pattern searches (joined with " | "); the default is the
title alone, and a field the caller did not supply is skipped.

Criteria rule (product.criteria), one dict per rule:
  {"field": name, "op": op, "value": v, "weight": 1.0,
   "required": false, "reject": false, "note": ""}
A violated required rule flags the listing (hard_fails); a violated
reject rule means the row is not the product at all — see rejected().
A violated rule with "flag": true still scores normally but its note
is surfaced on the row (flags) — for facts the buyer should see, such
as a salvage title, that are not deal-breakers; see flags().
ops: gte, lte, eq, contains, one_of, matches, exists.
Fields resolve from listing attrs first, then listing columns
(price, seller_rating, seller_feedback_count, condition, ...).

Market value (fit_value_model / estimate_value): for products whose
attrs carry `year` (and usually `mileage`) — cars — a log-linear fit
of price on age and mileage over the product's own priced listings,
outliers trimmed. It is the asking-price market for that year and
mileage, not Kelley Blue Book; callers store it as est_value.
"""

import math
import re
from statistics import median

OPS = ("gte", "lte", "eq", "contains", "one_of", "matches", "exists")


def extract_attrs(text: str, extractors: dict, extra: dict | None = None) -> dict:
    """Run every extractor over the title (`text`); a spec with "fields"
    searches the named listing fields out of `extra` (title included)."""
    attrs: dict = {}
    for field, spec in extractors.items():
        haystack = text
        if spec.get("fields"):
            parts = [text if f == "title" else (extra or {}).get(f) for f in spec["fields"]]
            haystack = " | ".join(str(p) for p in parts if p)
        m = re.search(spec["pattern"], haystack, re.IGNORECASE)
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
                attrs[field] = int(raw.replace(",", ""))  # "62,000 mi"
            elif kind == "float":
                attrs[field] = float(raw.replace(",", ""))
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


def flags(merged: dict, criteria: list[dict]) -> list[str]:
    """Notes of violated flag-rules — shown on the row, never hiding it."""
    out = []
    for rule in criteria:
        if not rule.get("flag"):
            continue
        actual = merged.get(rule["field"])
        if actual is None:
            continue
        try:
            if not _passes(rule["op"], actual, rule.get("value")):
                out.append(rule.get("note") or rule["field"])
        except (TypeError, ValueError):
            continue
    return out


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
    """Add price context to scored listings: median and % vs it, plus %
    vs est_value when the row carries one.

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
            if li.get("est_value"):
                li["pct_vs_est"] = round((li["price"] - li["est_value"]) / li["est_value"] * 100, 1)
    return listings


# --- market value ----------------------------------------------------

MIN_FIT_ROWS = 10
# Listings priced below this share of the median are placeholders
# ("$1", "$123") and never enter the fit.
_PLACEHOLDER_FRACTION = 0.05


def _solve(xt_x: list[list[float]], xt_y: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when singular."""
    n = len(xt_y)
    a = [[*row, xt_y[i]] for i, row in enumerate(xt_x)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r == col:
                continue
            f = a[r][col] / a[col][col]
            for k in range(col, n + 1):
                a[r][k] -= f * a[col][k]
    return [a[i][n] / a[i][i] for i in range(n)]


def _lstsq(points: list[tuple[list[float], float]]) -> list[float] | None:
    """Least squares of y on features (a leading 1 is added here)."""
    if not points:
        return None
    k = len(points[0][0]) + 1
    xt_x = [[0.0] * k for _ in range(k)]
    xt_y = [0.0] * k
    for feats, y in points:
        x = [1.0, *feats]
        for i in range(k):
            xt_y[i] += x[i] * y
            for j in range(k):
                xt_x[i][j] += x[i] * x[j]
    return _solve(xt_x, xt_y)


def _robust_fit(points: list[tuple[list[float], float]]) -> list[float] | None:
    """Fit, drop points whose log-residual is beyond 2.5 robust sigmas,
    refit. Needs MIN_FIT_ROWS survivors."""
    if len(points) < MIN_FIT_ROWS:
        return None
    coef = _lstsq(points)
    if coef is None:
        return None
    resid = [y - _predict(coef, f) for f, y in points]
    mad = median([abs(r - median(resid)) for r in resid]) * 1.4826
    cut = max(2.5 * mad, 0.15)  # never trim inside a 15% band
    kept = [p for p, r in zip(points, resid, strict=True) if abs(r) <= cut]
    if len(kept) < MIN_FIT_ROWS:
        return None
    return _lstsq(kept)


def _predict(coef: list[float], feats: list[float]) -> float:
    return coef[0] + sum(c * f for c, f in zip(coef[1:], feats, strict=True))


def fit_value_model(listings: list[dict], now_year: int) -> dict | None:
    """Fit ln(price) ~ age [+ mileage/10k] over priced listings with a
    `year` attr. Returns {"full": coef|None, "year_only": coef|None,
    "n": rows used} or None when nothing usable. Salvage/parts rows
    and placeholder prices are excluded. Pure."""
    rows = []
    for li in listings:
        attrs = li.get("attrs") or {}
        price, year = li.get("price"), attrs.get("year")
        if not price or price <= 0 or not year:
            continue
        if attrs.get("salvage") or attrs.get("is_parts"):
            continue
        rows.append((float(price), int(year), attrs.get("mileage")))
    if len(rows) < MIN_FIT_ROWS:
        return None
    floor = median(r[0] for r in rows) * _PLACEHOLDER_FRACTION
    rows = [r for r in rows if r[0] >= floor]
    year_pts = [([float(now_year - y)], math.log(p)) for p, y, _ in rows]
    full_pts = [
        ([float(now_year - y), m / 10_000.0], math.log(p)) for p, y, m in rows if m and m > 0
    ]
    model = {"full": _robust_fit(full_pts), "year_only": _robust_fit(year_pts), "n": len(rows)}
    return model if model["full"] or model["year_only"] else None


def estimate_value(model: dict | None, attrs: dict, now_year: int) -> float | None:
    """Dollar estimate for one listing's year (+ mileage when the full
    model exists and mileage is known); None when it can't be placed."""
    if not model or not attrs.get("year"):
        return None
    age = float(now_year - int(attrs["year"]))
    mileage = attrs.get("mileage")
    if model.get("full") and mileage and mileage > 0:
        return round(math.exp(_predict(model["full"], [age, mileage / 10_000.0])))
    if model.get("year_only"):
        return round(math.exp(_predict(model["year_only"], [age])))
    return None
