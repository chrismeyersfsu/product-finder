"""units contract: last size wins, multipacks multiply, grams-of-protein
are not a weight, everything lands in oz (or ct as the fallback)."""

import pytest
from product_finder_core import units


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Elevation Vanilla Ready to Drink Protein Shake, 4 x 11 fl oz", (44.0, "oz")),
        ("Premier Protein 30g Vanilla Protein Shake, 12 ct / 11 fl oz", (132.0, "oz")),
        ("WELCH'S Fruit Snacks Family Size, 40 pk / .8 oz", (32.0, "oz")),
        ("Lunch Buddies Fruit Snacks, 22 count, 17.6 oz", (17.6, "oz")),  # comma: total
        ("Milkshake - 4 Pack, 11.5 fl oz, 4 x 11.5 fl oz", (46.0, "oz")),  # last wins
        ("Ensure Max Protein Shake, 42g High Protein, French Vanilla, 14 fl oz", (14.0, "oz")),
        ("Premier Protein 30g Vanilla Protein Shake, 12ct", (12.0, "ct")),  # 30g = protein
        ("Core Power® Vanilla 42g High Protein Shake Bottle", None),
        ("Kroger Grade A Large Eggs, 12 ct", (12.0, "ct")),
        ("Bath bomb gift set 6-pack", (6.0, "ct")),
        ("Ground beef 2 lb", (32.0, "oz")),
        ("Olive oil 750 ml", (25.3605, "oz")),
        ("Trail mix 500 g", (17.637, "oz")),
        ("Milk 1 gal", (128.0, "oz")),
        ('Lenovo ThinkPad X1 Carbon Gen 9 14" FHD 16GB 512GB', None),
        ("Sandisk 256GB SSD SD5SG2-256G-1052E for Thinkpad", None),  # "-256G" is a part no.
        ("ThinkPad X13 Gen 5 4G LTE 16GB", None),  # "4G" is cellular, not grams
        ("Haribo gummy bears 500G bag", None),  # cost of the rule above: uppercase G glued
        ("Haribo gummy bears 500 G bag", (17.637, "oz")),
        ("2019 Honda Fit 45,000 miles", None),
        ("", None),
    ],
)
def test_pack_size(title, expected):
    assert units.pack_size(title) == expected


def test_unit_price():
    assert units.unit_price(6.45, "Shake, 4 x 11 fl oz") == {
        "unit_qty": 44.0,
        "unit": "oz",
        "unit_price": 0.1466,
    }
    assert units.unit_price(3.49, "Eggs, 12 ct") == {
        "unit_qty": 12.0,
        "unit": "ct",
        "unit_price": 0.2908,
    }
    none = {"unit_qty": None, "unit": None, "unit_price": None}
    assert units.unit_price(None, "4 x 11 fl oz") == none
    assert units.unit_price(0, "4 x 11 fl oz") == none
    assert units.unit_price(9.99, "no size here") == none
