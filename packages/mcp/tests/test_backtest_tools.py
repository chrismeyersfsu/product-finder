"""Backtest/history tools against a tmp DB; HTTP faked at fetch._get."""

from datetime import UTC, datetime, timedelta

import pytest
from product_finder_mcp import server
from product_finder_sites import fetch

TITLE = "Lenovo ThinkPad X1 Carbon Gen 6 i5-8350U 16GB RAM 256GB NVMe FHD IPS"


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB", str(tmp_path / "t.db"))
    server.seed_defaults()


def _sold_page(rows: list[tuple[str, float, str]]) -> str:
    items = "".join(
        f'<li class="s-item"><a class="s-item__link" href="{url}">'
        f'<div class="s-item__title">{TITLE}</div></a>'
        f'<span class="s-item__price">${price}</span>'
        f'<span class="s-item__caption">Sold {when}</span></li>'
        for url, price, when in rows
    )
    return f"<html><body><ul>{items}</ul></body></html>"


def test_backfill_ebay_sold(monkeypatch):
    d1 = (datetime.now(UTC) - timedelta(days=5)).strftime("%b %-d, %Y")
    d2 = (datetime.now(UTC) - timedelta(days=40)).strftime("%b %-d, %Y")
    page = _sold_page([("https://e/itm/1", 254.00, d1), ("https://e/itm/2", 199.00, d2)])
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: page)

    out = server.backfill_ebay_sold("thin-client-laptop", query="x1 carbon")
    assert out["added"] == 2 and out["errors"] == []
    # idempotent: same sold listings do not duplicate
    assert server.backfill_ebay_sold("thin-client-laptop", query="x1 carbon")["added"] == 0

    stats = server.price_history_stats("thin-client-laptop")
    assert stats["observations"] == 2 and stats["per_kind"] == {"sold": 2}


def test_add_observation_and_run_backtest():
    now = datetime.now(UTC)
    # 70 days of daily $300 sightings plus one $150 deal 20 days back
    for day in range(70):
        server.add_price_observation(
            "thin-client-laptop",
            "ebay" if day % 2 else "swappa",
            300.0,
            (now - timedelta(days=day)).isoformat(),
            title=TITLE,
            url=f"http://x/{day}",
        )
    server.add_price_observation(
        "thin-client-laptop",
        "swappa",
        150.0,
        (now - timedelta(days=20)).isoformat(),
        title=TITLE,
        url="http://x/deal",
    )

    out = server.run_backtest("thin-client-laptop", windows=[3, 28], n_pivots=60, seed=1)
    assert out["backtest_id"] >= 1
    assert out["coverage"]["n_qualifying"] == 71
    assert out["coverage"]["dropped_windows"] == []
    vs = out["windows"]["28"]["vs_shortest_window"]
    assert vs["mean_improvement"] > 0
    assert out["verdict"]

    stored = server.get_backtest(out["backtest_id"])
    assert stored["results"]["windows"]["28"]["mean_best_price"] < 300.0
    assert server.list_backtests("thin-client-laptop")[0]["id"] == out["backtest_id"]


def test_run_search_appends_seen_observations(monkeypatch):
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "ebay.html"
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: fixture.read_text())
    server.run_search("thin-client-laptop", sites=["ebay"], query="x1 carbon")
    stats = server.price_history_stats("thin-client-laptop")
    assert stats["per_kind"].get("seen", 0) == 2


def test_backtest_unknown_product():
    assert "error" in server.run_backtest("nope")
    assert "error" in server.get_backtest(12345)
