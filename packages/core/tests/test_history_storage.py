"""price_history and backtests storage contract: append-only dedupe, JSON round-trip."""

from product_finder_core import storage


def _conn(tmp_path):
    conn = storage.connect(str(tmp_path / "t.db"))
    storage.upsert_product(conn, {"slug": "w"})
    return conn


def _obs(**kw):
    return {
        "product_slug": "w",
        "site_slug": "ebay",
        "url": "http://x/1",
        "price": 100.0,
        "observed_at": "2026-01-05",
        **kw,
    }


def test_append_dedupes_on_url_and_date(tmp_path):
    conn = _conn(tmp_path)
    assert storage.append_observation(conn, _obs()) is True
    assert storage.append_observation(conn, _obs()) is False  # same url+date
    assert storage.append_observation(conn, _obs(observed_at="2026-01-06")) is True
    assert len(storage.query_observations(conn, "w")) == 2


def test_query_filters_kinds_and_sorts(tmp_path):
    conn = _conn(tmp_path)
    storage.append_observation(conn, _obs(observed_at="2026-02-01", kind="sold"))
    storage.append_observation(conn, _obs(observed_at="2026-01-01", kind="seen"))
    rows = storage.query_observations(conn, "w")
    assert [r["observed_at"] for r in rows] == ["2026-01-01", "2026-02-01"]
    assert [r["kind"] for r in storage.query_observations(conn, "w", kinds=["sold"])] == ["sold"]


def test_history_stats(tmp_path):
    conn = _conn(tmp_path)
    storage.append_observation(conn, _obs())
    storage.append_observation(conn, _obs(site_slug="swappa", url="http://y/2", kind="sold"))
    stats = storage.history_stats(conn, "w")
    assert stats["observations"] == 2
    assert stats["per_site"] == {"ebay": 1, "swappa": 1}
    assert stats["per_kind"] == {"seen": 1, "sold": 1}


def test_backtest_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    bt_id = storage.insert_backtest(conn, "w", {"seed": 1}, {"verdict": ["ok"]})
    row = storage.get_backtest(conn, bt_id)
    assert row["params"] == {"seed": 1} and row["results"]["verdict"] == ["ok"]
    assert storage.list_backtests(conn, "w")[0]["id"] == bt_id
    assert storage.get_backtest(conn, 999) is None
