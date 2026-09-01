"""Each package tests only itself; run via ./packages/core/ci.sh."""

from product_finder_core import cli, storage


def test_cli_init_and_seed(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    cli.main(["--db", db, "init"])
    cli.main(["--db", db, "seed"])
    cli.main(["--db", db, "products"])
    out = capsys.readouterr().out
    assert "thin-client-laptop" in out
    conn = storage.connect(db)
    assert storage.get_product(conn, "thin-client-laptop")["criteria"]
