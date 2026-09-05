"""Scrape summary contract: per-site report, exit-worthy only on total failure."""

from product_finder_mcp.scrape import summarize


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
