"""Tool functions exercised directly against a tmp DB; run via ./packages/mcp/ci.sh.

External HTTP is faked at the sites package's fetch._get seam.
"""

from pathlib import Path

import pytest
from product_finder_mcp import server
from product_finder_sites import fetch

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB", str(tmp_path / "t.db"))
    for var in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "BESTBUY_API_KEY", "WALMART_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_seed_and_product_crud():
    out = server.seed_defaults()
    assert "thin-client-laptop" in out["products"]
    assert len(out["sites"]) == 22
    assert server.get_product("thin-client-laptop")["criteria"]

    server.add_product(
        "gpu",
        "Used GPU",
        queries=["rtx 3080"],
        criteria=[{"field": "price", "op": "lte", "value": 400}],
    )
    assert {p["slug"] for p in server.list_products()} == {"gpu", "thin-client-laptop"}
    assert server.delete_product("gpu")["deleted"]
    assert "error" in server.get_product("gpu")


def test_site_management():
    server.seed_defaults()
    server.add_site("mysite", "My Site", config={"url": "https://x/?q={query}"})
    assert any(s["slug"] == "mysite" for s in server.list_sites())
    assert server.set_site_enabled("amazon", False)["ok"]
    enabled = {s["slug"]: s["enabled"] for s in server.list_sites()}
    assert enabled["amazon"] is False


def test_run_search_scores_and_stores(monkeypatch):
    server.seed_defaults()
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "ebay.html").read_text(),
    )
    summary = server.run_search("thin-client-laptop", sites=["ebay"], query="x1 carbon")
    assert summary["stored"] == 2 and summary["per_site"] == {"ebay": 2}
    assert summary["strategies"] == {"ebay": "browser_css"}  # api unset, no plain-HTML tier

    rows = server.query_listings("thin-client-laptop")
    assert rows and rows[0]["score"] > 0.8
    assert rows[0]["attrs"]["ram_gb"] == 16

    deals = server.best_deals("thin-client-laptop")
    assert deals["deals"][0]["title"].startswith("Lenovo ThinkPad")
    assert "median_price" in deals["deals"][0]
    assert deals["manual_checks"]


def test_run_search_records_site_errors(monkeypatch):
    server.seed_defaults()

    def fail(url, headers=None, timeout=25.0):
        raise fetch.FetchError("HTTP 403")

    monkeypatch.setattr(fetch, "_get", fail)
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: fetch._browser_unwired(url),
    )
    summary = server.run_search("thin-client-laptop", sites=["amazon"])
    assert summary["stored"] == 0
    assert "css: HTTP 403" in summary["errors"]["amazon"]
    assert "browser:" in summary["errors"]["amazon"]


def test_run_search_missing_product():
    assert "error" in server.run_search("nope")


def test_project_tools_scoped_to_root(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "hello.txt").write_text("hi")
    monkeypatch.setenv("PF_PROJECT_ROOT", str(root))

    assert server.project_list_files("*.txt") == ["hello.txt"]
    assert server.project_read_file("hello.txt") == "hi"
    out = server.project_write_file("sub/new.txt", "content")
    assert out["wrote"] == "sub/new.txt"
    assert (root / "sub/new.txt").read_text() == "content"

    with pytest.raises(ValueError, match="escapes"):
        server.project_read_file("../secret")
    with pytest.raises(ValueError, match="escapes"):
        server.project_write_file("../../evil.txt", "x")
    with pytest.raises(ValueError, match=r"\.git"):
        server.project_write_file(".git/config", "x")


def test_tools_registered():
    assert len(server.TOOLS) == 21
