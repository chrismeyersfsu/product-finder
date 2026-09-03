"""Scoring contract: extraction from titles, weights, required-rule hard fails."""

from product_finder_core import scoring
from product_finder_core.seed import LAPTOP

TITLE = 'Lenovo ThinkPad X1 Carbon Gen 6 14" FHD IPS i5-8350U 16GB RAM 256GB NVMe SSD'


def test_extract_attrs_laptop_title():
    attrs = scoring.extract_attrs(TITLE, LAPTOP["extractors"])
    assert attrs["ram_gb"] == 16
    assert attrs["storage_gb"] == 256
    assert attrs["nvme"] and attrs["ips"] and attrs["fhd"]
    assert attrs["cpu"] == "i5-8350u"
    assert attrs["carbon_gen"] == 6


def test_extract_tb_normalizes_to_gb():
    attrs = scoring.extract_attrs("1TB NVMe SSD", LAPTOP["extractors"])
    assert attrs["storage_gb"] == 1000


def test_good_listing_scores_high_no_hard_fails():
    merged = {
        **scoring.extract_attrs(TITLE, LAPTOP["extractors"]),
        "seller_rating": 99.6,
        "seller_feedback_count": 2400,
        "price": 300,
    }
    score, fails = scoring.score_listing(merged, LAPTOP["criteria"])
    assert fails == []
    assert score > 0.9


def test_contradicting_required_rule_hard_fails():
    attrs = scoring.extract_attrs(
        "ThinkPad X1 Carbon Gen 6 8GB RAM 256GB SSD", LAPTOP["extractors"]
    )
    _, fails = scoring.score_listing(attrs, LAPTOP["criteria"])
    assert any("16GB" in f for f in fails)


def test_missing_field_is_unknown_not_fail():
    _, fails = scoring.score_listing({"price": 200}, LAPTOP["criteria"])
    assert fails == []


def test_annotate_deals():
    rows = [{"price": 100.0}, {"price": 200.0}, {"price": 300.0}, {"price": None}]
    out = scoring.annotate_deals(rows)
    assert out[0]["median_price"] == 200.0
    assert out[0]["pct_vs_median"] == -50.0
    assert "median_price" not in out[3]


def test_reject_rules_discard_non_product():
    attrs = scoring.extract_attrs(
        "iTEKIRO 65W AC Adapter for Lenovo ThinkPad X1 Carbon", LAPTOP["extractors"]
    )
    assert scoring.rejected(attrs, LAPTOP["criteria"])
    attrs = scoring.extract_attrs(
        "Satechi Slim USB-C 6-in-1 Multi-Port Adapter (Black)", LAPTOP["extractors"]
    )
    assert scoring.rejected(attrs, LAPTOP["criteria"])
    attrs = scoring.extract_attrs(TITLE, LAPTOP["extractors"])
    assert scoring.rejected(attrs, LAPTOP["criteria"]) is None


def _cars(n=40, seed=7):
    """Synthetic market: $30k new, -9%/yr, -5%/10k mi, ±4% noise."""
    import math
    import random

    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        year = rng.randint(2014, 2024)
        miles = rng.randint(5_000, 120_000)
        price = 30_000 * math.exp(-0.09 * (2026 - year) - 0.05 * miles / 10_000)
        price *= 1 + rng.uniform(-0.04, 0.04)
        rows.append({"price": round(price), "attrs": {"year": year, "mileage": miles}})
    return rows


def test_value_model_recovers_age_and_mileage_effects():
    model = scoring.fit_value_model(_cars(), now_year=2026)
    _, b, c = model["full"]
    assert -0.11 < b < -0.07  # per year of age
    assert -0.07 < c < -0.03  # per 10k miles
    est = scoring.estimate_value(model, {"year": 2020, "mileage": 50_000}, 2026)
    assert abs(est - 30_000 * 2.718281828 ** (-0.09 * 6 - 0.25)) / est < 0.05


def test_value_model_ignores_placeholders_salvage_and_outliers():
    rows = _cars()
    rows += [
        {"price": 1, "attrs": {"year": 2020, "mileage": 50_000}},  # "$1" placeholder
        {"price": 4_000, "attrs": {"year": 2022, "mileage": 10_000, "salvage": True}},
        {"price": 90_000, "attrs": {"year": 2018, "mileage": 80_000}},  # typo-level outlier
    ]
    clean = scoring.fit_value_model(_cars(), 2026)
    noisy = scoring.fit_value_model(rows, 2026)
    for probe in ({"year": 2018, "mileage": 80_000}, {"year": 2023, "mileage": 15_000}):
        a, b = (
            scoring.estimate_value(clean, probe, 2026),
            scoring.estimate_value(noisy, probe, 2026),
        )
        assert abs(a - b) / a < 0.03


def test_value_model_falls_back_to_year_when_mileage_unknown():
    rows = _cars()
    model = scoring.fit_value_model(rows, 2026)
    assert scoring.estimate_value(model, {"year": 2020}, 2026) > scoring.estimate_value(
        model, {"year": 2015}, 2026
    )
    assert scoring.estimate_value(model, {}, 2026) is None
    assert scoring.estimate_value(None, {"year": 2020}, 2026) is None
    # too few rows, or rows with no year at all (non-car products): no model
    assert scoring.fit_value_model(rows[:5], 2026) is None
    assert scoring.fit_value_model([{"price": 9.0, "attrs": {}}] * 30, 2026) is None


def test_annotate_deals_adds_pct_vs_est_when_present():
    rows = [{"price": 90.0, "est_value": 100.0}, {"price": 50.0}]
    scoring.annotate_deals(rows)
    assert rows[0]["pct_vs_est"] == -10.0
    assert "pct_vs_est" not in rows[1]


def test_extractor_fields_search_named_listing_fields():
    ex = {
        "salvage": {
            "pattern": "salvage|wreck city motors",
            "type": "bool",
            "fields": ["title", "condition"],
        }
    }
    assert scoring.extract_attrs(
        "2019 Prius, 40000 mi", ex, {"condition": "used; Wreck City Motors"}
    )
    assert not scoring.extract_attrs(
        "2019 Prius, 40000 mi", ex, {"condition": "used; Toyota of Durham"}
    )["salvage"]
    assert not scoring.extract_attrs("2019 Prius", ex)["salvage"]  # no extra: title alone
    assert scoring.extract_attrs("Salvage 2019 Prius", ex)["salvage"]


def test_flag_rules_surface_a_note_without_hiding_the_row():
    criteria = [
        {
            "field": "salvage",
            "op": "eq",
            "value": False,
            "weight": 2,
            "flag": True,
            "note": "salvage title",
        },
        {"field": "year", "op": "gte", "value": 2016, "weight": 3, "required": True},
    ]
    clean = {"salvage": False, "year": 2018}
    wrecked = {"salvage": True, "year": 2018}
    assert scoring.flags(clean, criteria) == []
    assert scoring.flags(wrecked, criteria) == ["salvage title"]
    assert scoring.flags({"year": 2018}, criteria) == []  # unknown is not a flag
    assert scoring.rejected(wrecked, criteria) is None
    score, hard_fails = scoring.score_listing(wrecked, criteria)
    assert hard_fails == [] and score == 0.6  # loses the rule's weight, stays in deals


def test_int_extractors_accept_thousands_separators():
    ex = {"mileage": {"pattern": r"\b(\d{1,3}(?:,\d{3})+|\d{4,6})\s*(?:mi\b|miles)", "type": "int"}}
    assert scoring.extract_attrs("2015 Avalon XLE 62,000 mi", ex) == {"mileage": 62000}
    assert scoring.extract_attrs("2015 Avalon XLE 62000 miles", ex) == {"mileage": 62000}
