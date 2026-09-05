"""Scrape summary contract: per-site report, exit-worthy only on total failure.

The queue tests below are pure/unit: server.rescore_product and
server.run_search are monkeypatched (no network, no real scrape
pipeline) and PF_DB points at a tmp_path db so server.add_product /
storage.get_product see real product rows.
"""

import json
import os
import time
from pathlib import Path

import pytest
from product_finder_mcp import scrape, server
from product_finder_mcp.scrape import process_requests, queue_dir, summarize


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB", str(tmp_path / "t.db"))
    return tmp_path


def test_mixed_results_not_total_failure():
    text, fail = summarize(
        {"laptop": {"stored": 5, "per_site": {"newegg": 5}, "errors": {"ebay": "HTTP 403"}}}
    )
    assert not fail
    assert "laptop: stored 5 (newegg:5); 1 site errors" in text
    assert "ebay: HTTP 403" in text


def test_all_sites_errored_is_total_failure():
    _, fail = summarize(
        {"laptop": {"stored": 0, "per_site": {}, "errors": {"ebay": "403", "newegg": "503"}}}
    )
    assert fail


def test_site_erroring_one_query_but_storing_another_is_not_failed():
    _, fail = summarize(
        {"laptop": {"stored": 2, "per_site": {"bonanza": 2}, "errors": {"bonanza": "no items"}}}
    )
    assert not fail


def test_unknown_product_line_and_no_attempts():
    text, fail = summarize({"ghost": {"error": "no product: ghost"}})
    assert not fail
    assert "ghost: no product: ghost" in text


def test_rescore_counts_lead_the_product_line():
    text, fail = summarize(
        {
            "laptop": {
                "stored": 5,
                "per_site": {"newegg": 5},
                "errors": {},
                "rescored": {"rescored": 40, "rejected": 2, "valued": 0},
            }
        }
    )
    assert not fail
    assert "laptop: rescored 40 (dropped 2); stored 5 (newegg:5); 0 site errors" in text


# -- queue_dir() -------------------------------------------------------------


def test_queue_dir_derives_from_pf_db(monkeypatch):
    monkeypatch.delenv("PF_SCRAPE_QUEUE", raising=False)
    monkeypatch.setenv("PF_DB", "/srv/product-finder/data/product_finder.db")
    assert queue_dir() == Path("/srv/product-finder/data/scrape-now")


def test_queue_dir_env_override_wins(monkeypatch):
    monkeypatch.setenv("PF_DB", "/srv/product-finder/data/product_finder.db")
    monkeypatch.setenv("PF_SCRAPE_QUEUE", "/other/queue")
    assert queue_dir() == Path("/other/queue")


def test_queue_dir_falls_back_without_pf_db(monkeypatch):
    monkeypatch.delenv("PF_SCRAPE_QUEUE", raising=False)
    monkeypatch.delenv("PF_DB", raising=False)
    assert queue_dir() == Path("data/scrape-now")


# -- process_requests() -------------------------------------------------------


def _touch(path, mtime_offset=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    if mtime_offset:
        t = time.time() + mtime_offset
        os.utime(path, (t, t))


def test_empty_queue_is_a_noop(tmp_path):
    base = tmp_path / "scrape-now"
    assert process_requests(base) == {}
    assert (base / "queue").is_dir()
    assert (base / "running").is_dir()
    assert (base / "done").is_dir()


def test_processes_queued_slugs_in_mtime_order_and_empties_queue(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    server.add_product("beta", "Beta")
    base = tmp_db / "scrape-now"
    # beta queued first (older mtime) even though created second on disk
    _touch(base / "queue" / "alpha", mtime_offset=10)
    _touch(base / "queue" / "beta", mtime_offset=0)

    order = []

    def fake_rescore(slug):
        order.append(slug)
        return {"rescored": 1, "rejected": 0, "valued": 0}

    def fake_run_search(slug):
        return {"stored": 1, "per_site": {"site": 1}, "errors": {}}

    monkeypatch.setattr(server, "rescore_product", fake_rescore)
    monkeypatch.setattr(server, "run_search", fake_run_search)

    runs = process_requests(base)

    assert order == ["beta", "alpha"]
    assert set(runs) == {"alpha", "beta"}
    assert runs["alpha"]["stored"] == 1
    assert list((base / "queue").iterdir()) == []


def test_running_marker_present_during_and_removed_after(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    base = tmp_db / "scrape-now"
    _touch(base / "queue" / "alpha")

    seen_running = {}

    def fake_run_search(slug):
        seen_running["during"] = (base / "running" / slug).exists()
        return {"stored": 0, "per_site": {}, "errors": {}}

    monkeypatch.setattr(
        server, "rescore_product", lambda slug: {"rescored": 0, "rejected": 0, "valued": 0}
    )
    monkeypatch.setattr(server, "run_search", fake_run_search)

    process_requests(base)

    assert seen_running["during"] is True
    assert not (base / "running" / "alpha").exists()


def test_unknown_product_deletes_queue_file_and_writes_no_product(tmp_db, monkeypatch):
    base = tmp_db / "scrape-now"
    _touch(base / "queue" / "ghost")

    called = []
    monkeypatch.setattr(server, "rescore_product", lambda slug: called.append(slug))
    monkeypatch.setattr(server, "run_search", lambda slug: called.append(slug))

    runs = process_requests(base)

    assert called == []
    assert runs == {"ghost": {"error": "no product"}}
    assert not (base / "queue" / "ghost").exists()
    assert not (base / "running" / "ghost").exists()
    assert (base / "done" / "ghost").read_text().strip() == "ghost: no product"


def test_ignores_junk_filenames(tmp_db):
    base = tmp_db / "scrape-now"
    _touch(base / "queue" / ".hidden")
    _touch(base / "queue" / "Not_A_Slug!")
    _touch(base / "queue" / "UPPER")

    runs = process_requests(base)

    assert runs == {}
    remaining = {p.name for p in (base / "queue").iterdir()}
    assert remaining == {".hidden", "Not_A_Slug!", "UPPER"}


def test_done_file_matches_summarize_output(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    base = tmp_db / "scrape-now"
    _touch(base / "queue" / "alpha")

    monkeypatch.setattr(
        server, "rescore_product", lambda slug: {"rescored": 3, "rejected": 1, "valued": 0}
    )
    monkeypatch.setattr(
        server,
        "run_search",
        lambda slug: {"stored": 2, "per_site": {"ebay": 2}, "errors": {"newegg": "HTTP 500"}},
    )

    runs = process_requests(base)
    expected_text, _ = summarize({"alpha": runs["alpha"]})
    assert (base / "done" / "alpha").read_text() == expected_text + "\n"
    assert "alpha: rescored 3 (dropped 1); stored 2 (ebay:2); 1 site errors" in expected_text
    assert "newegg: HTTP 500" in expected_text


def test_new_request_arriving_mid_run_is_also_drained(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    server.add_product("beta", "Beta")
    base = tmp_db / "scrape-now"
    _touch(base / "queue" / "alpha")

    def fake_run_search(slug):
        if slug == "alpha":
            # simulate a second request landing while alpha is processed
            _touch(base / "queue" / "beta")
        return {"stored": 0, "per_site": {}, "errors": {}}

    monkeypatch.setattr(
        server, "rescore_product", lambda slug: {"rescored": 0, "rejected": 0, "valued": 0}
    )
    monkeypatch.setattr(server, "run_search", fake_run_search)

    runs = process_requests(base)

    assert set(runs) == {"alpha", "beta"}
    assert list((base / "queue").iterdir()) == []


# -- main() --requested routing -----------------------------------------------


def test_main_requested_empty_queue_prints_message_and_returns(monkeypatch, capsys):
    monkeypatch.setattr(scrape, "process_requests", lambda: {})
    scrape.main(["--requested"])
    assert "no scrape requests queued" in capsys.readouterr().out


def test_main_requested_routes_to_process_requests_and_prints_summary(monkeypatch, capsys):
    monkeypatch.setattr(
        scrape,
        "process_requests",
        lambda: {"alpha": {"stored": 4, "per_site": {"ebay": 4}, "errors": {}}},
    )
    scrape.main(["--requested"])
    assert "alpha: stored 4 (ebay:4); 0 site errors" in capsys.readouterr().out


def test_main_requested_exits_1_on_total_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        scrape,
        "process_requests",
        lambda: {"alpha": {"stored": 0, "per_site": {}, "errors": {"ebay": "403"}}},
    )
    with pytest.raises(SystemExit) as exc:
        scrape.main(["--requested"])
    assert exc.value.code == 1


def test_main_without_flag_still_scrapes_all_products(tmp_db, capsys):
    scrape.main([])
    assert "no products configured; nothing to scrape" in capsys.readouterr().out


def test_a_crashing_request_is_recorded_and_the_queue_keeps_draining(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB", str(tmp_path / "x.db"))
    base = tmp_path / "scrape-now"
    (base / "queue").mkdir(parents=True)
    (base / "queue" / "boom").write_text("")
    (base / "queue" / "fine").write_text("")
    os.utime(base / "queue" / "boom", (1, 1))
    monkeypatch.setattr(scrape.storage, "get_product", lambda conn, slug: {"slug": slug})
    monkeypatch.setattr(scrape.server, "_connect", lambda: None)
    monkeypatch.setattr(
        scrape.server, "rescore_product", lambda slug: {"rescored": 0, "rejected": 0}
    )

    def run_search(slug):
        if slug == "boom":
            raise RuntimeError("browser died")
        return {"stored": 1, "per_site": {"ebay": 1}, "errors": {}}

    monkeypatch.setattr(scrape.server, "run_search", run_search)
    runs = scrape.process_requests(base)
    assert runs["boom"] == {"error": "RuntimeError: browser died"}
    assert runs["fine"]["stored"] == 1
    assert (base / "done" / "boom").read_text() == "boom: RuntimeError: browser died\n"
    assert not list((base / "queue").iterdir()) and not list((base / "running").iterdir())


# -- state files ---------------------------------------------------------------


def test_hourly_state_has_current_set_mid_run(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    server.add_product("beta", "Beta")
    state_path = tmp_db / "scrape-now" / "state" / "hourly.json"

    seen = {}

    def fake_run_search(slug):
        data = json.loads(state_path.read_text())
        if slug == "beta":
            seen["current"] = data["current"]
            seen["products"] = data["products"]
            seen["current_started_at"] = data["current_started_at"]
        return {"stored": 1, "per_site": {"site": 1}, "errors": {}}

    monkeypatch.setattr(server, "rescore_product", lambda slug: {"rescored": 0, "rejected": 0})
    monkeypatch.setattr(server, "run_search", fake_run_search)

    scrape.main([])

    assert seen["current"] == "beta"
    assert seen["products"] == ["alpha", "beta"]
    assert seen["current_started_at"] is not None


def test_hourly_state_results_and_finish_set_at_the_end(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    server.add_product("beta", "Beta")
    state_path = tmp_db / "scrape-now" / "state" / "hourly.json"

    monkeypatch.setattr(server, "rescore_product", lambda slug: {"rescored": 0, "rejected": 0})
    monkeypatch.setattr(
        server, "run_search", lambda slug: {"stored": 1, "per_site": {"site": 1}, "errors": {}}
    )

    scrape.main([])

    final = json.loads(state_path.read_text())
    assert final["mode"] == "hourly"
    assert final["current"] is None
    assert final["current_started_at"] is None
    assert final["finished_at"] is not None
    assert final["exit"] == 0
    assert final["results"]["alpha"]["stored"] == 1
    assert final["results"]["alpha"]["errors"] == 0
    assert (
        final["results"]["alpha"]["line"]
        == "alpha: rescored 0 (dropped 0); stored 1 (site:1); 0 site errors"
    )
    assert final["results"]["beta"]["finished_at"] is not None


def test_hourly_state_exit_1_on_total_failure(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    monkeypatch.setattr(server, "rescore_product", lambda slug: {"rescored": 0, "rejected": 0})
    monkeypatch.setattr(
        server, "run_search", lambda slug: {"stored": 0, "per_site": {}, "errors": {"ebay": "403"}}
    )

    with pytest.raises(SystemExit):
        scrape.main([])

    state_path = tmp_db / "scrape-now" / "state" / "hourly.json"
    final = json.loads(state_path.read_text())
    assert final["exit"] == 1
    assert final["finished_at"] is not None


def test_requested_state_products_list_grows_as_requests_drain(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    server.add_product("beta", "Beta")
    base = tmp_db / "scrape-now"
    _touch(base / "queue" / "alpha")
    state_path = base / "state" / "requested.json"

    def fake_run_search(slug):
        if slug == "alpha":
            data = json.loads(state_path.read_text())
            assert data["products"] == ["alpha"]
            assert data["current"] == "alpha"
            _touch(base / "queue" / "beta")
        return {"stored": 0, "per_site": {}, "errors": {}}

    monkeypatch.setattr(server, "rescore_product", lambda slug: {"rescored": 0, "rejected": 0})
    monkeypatch.setattr(server, "run_search", fake_run_search)

    process_requests(base)

    final = json.loads(state_path.read_text())
    assert final["mode"] == "requested"
    assert final["products"] == ["alpha", "beta"]
    assert final["finished_at"] is not None
    assert final["exit"] == 0


def test_requested_state_written_even_when_queue_starts_empty(tmp_path):
    base = tmp_path / "scrape-now"
    process_requests(base)
    final = json.loads((base / "state" / "requested.json").read_text())
    assert final["products"] == []
    assert final["started_at"] is not None
    assert final["finished_at"] is not None
    assert final["exit"] == 0


def test_atomic_state_write_leaves_no_temp_files_behind(tmp_db, monkeypatch):
    server.add_product("alpha", "Alpha")
    monkeypatch.setattr(server, "rescore_product", lambda slug: {"rescored": 0, "rejected": 0})
    monkeypatch.setattr(
        server, "run_search", lambda slug: {"stored": 1, "per_site": {"s": 1}, "errors": {}}
    )

    scrape.main([])

    state_dir = tmp_db / "scrape-now" / "state"
    assert {p.name for p in state_dir.iterdir()} == {"hourly.json"}
