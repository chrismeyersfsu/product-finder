"""Engine contract: window edges, pivot constraints, truncation, CIs, site wins.

Synthetic observations with known answers; seeded so every assertion
is deterministic. No I/O anywhere.
"""

from datetime import UTC, datetime, timedelta

from product_finder_backtest import engine

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _obs(days_ago: float, price: float, site: str = "ebay", **kw):
    when = (NOW - timedelta(days=days_ago)).isoformat()
    return {"site_slug": site, "price": price, "observed_at": when, "score": 0.9, **kw}


def _daily(n_days: int, price=200.0, site="ebay"):
    return [_obs(d, price, site) for d in range(n_days)]


def test_qualify_filters():
    obs = [
        _obs(1, 100),
        _obs(2, 0),  # no price
        _obs(3, 100, score=0.2),  # below min_score
        _obs(4, 100, hard_fails=["8GB"]),
        {"site_slug": "ebay", "price": 100, "observed_at": _obs(5, 1)["observed_at"]},  # no score
    ]
    assert len(engine._qualify(obs, 0.5)) == 2


def test_window_interval_is_half_open():
    # window (pivot-3d, pivot]: obs exactly at pivot counts, at pivot-3d does not
    pivot = NOW
    obs = [_obs(0, 100), _obs(3, 50)]  # 50 sits exactly on the boundary
    res = engine.run(obs, windows=[3], pivots=[pivot.isoformat()], now=NOW)
    assert res["windows"]["3"]["min_best_price"] == 100.0


def test_pivot_range_respects_largest_window():
    obs = _daily(150)
    res = engine.run(obs, windows=[3, 112], n_pivots=50, seed=1, now=NOW)
    start, end = res["coverage"]["pivot_range"]
    earliest = min(o["observed_at"] for o in obs)
    assert start >= (datetime.fromisoformat(earliest) + timedelta(days=112)).isoformat()
    assert end <= max(o["observed_at"] for o in obs)


def test_short_span_drops_big_windows():
    res = engine.run(_daily(10), now=NOW, seed=1)
    assert res["coverage"]["dropped_windows"] == [14, 28, 56, 112]
    assert set(res["windows"]) <= {"3", "7"}


def test_no_data_at_all():
    res = engine.run([], now=NOW)
    assert "No qualifying observations" in res["verdict"][0]


def test_longer_window_finds_rare_deal():
    # steady $300 market with one $100 deal 20 days back: 28d window sees
    # it from most pivots, 3d window almost never does
    obs = [*_daily(60, price=300.0), _obs(20, 100.0)]
    res = engine.run(obs, windows=[3, 28], n_pivots=100, seed=2, now=NOW)
    w3, w28 = res["windows"]["3"], res["windows"]["28"]
    assert w28["mean_best_price"] < w3["mean_best_price"]
    vs = w28["vs_shortest_window"]
    assert vs["mean_improvement"] > 0
    lo, hi = vs["ci95"]
    assert lo <= vs["mean_improvement"] <= hi
    assert 0 < vs["frac_pivots_improved"] <= 1
    assert any("improves" in line for line in res["verdict"])


def test_site_win_rates():
    # swappa always undercuts ebay by $50 -> wins every pivot-window
    obs = _daily(120, price=250.0, site="ebay") + _daily(120, price=200.0, site="swappa")
    res = engine.run(obs, windows=[3, 7, 14], n_pivots=60, seed=3, now=NOW)
    assert res["sites"]["swappa"]["win_rate"] == 1.0
    assert "swappa" in res["verdict"][-1]
    assert res["sites"]["swappa"]["mean_winning_price"] == 200.0


def test_insufficient_pivots_flagged():
    res = engine.run(_daily(30), windows=[3, 7], n_pivots=10, seed=4, now=NOW)
    assert res["windows"]["3"].get("insufficient_data") is True
    assert any("Insufficient data" in line for line in res["verdict"])


def test_deterministic_for_seed():
    obs = [*_daily(90, price=300.0), _obs(30, 120.0)]
    a = engine.run(obs, n_pivots=50, seed=7, now=NOW)
    b = engine.run(obs, n_pivots=50, seed=7, now=NOW)
    assert a == b


def test_result_is_json_serializable():
    import json

    res = engine.run(_daily(120), n_pivots=40, seed=5, now=NOW)
    json.dumps(res)


def test_year_cap_excludes_stale_history():
    obs = [*_daily(30), _obs(500, 1.0)]  # absurd deal but >1yr old
    res = engine.run(obs, windows=[3, 7], n_pivots=40, seed=6, now=NOW)
    span_start = res["coverage"]["span_start"]
    assert span_start >= (NOW - timedelta(days=365)).isoformat()
    prices = [res["windows"][w].get("min_best_price") for w in res["windows"]]
    assert 1.0 not in prices
