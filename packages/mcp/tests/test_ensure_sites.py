"""_ensure_sites adds newly introduced builtins without clobbering customized rows."""

from product_finder_core import storage
from product_finder_mcp import server
from product_finder_sites.spec import BUILTIN_SITES


def test_new_builtin_added_existing_config_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB", str(tmp_path / "t.db"))
    conn = server._connect()
    custom = dict(BUILTIN_SITES[0])
    custom["config"] = {**custom["config"], "url": "https://example.test/{query}"}
    storage.upsert_site(conn, custom)
    server._ensure_sites(conn)
    sites = {s["slug"]: s for s in storage.list_sites(conn)}
    assert len(sites) == len(BUILTIN_SITES)
    assert sites[custom["slug"]]["config"]["url"] == "https://example.test/{query}"
