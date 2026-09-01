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
