from export import QUERIES, read_env_file


def test_read_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "\n"
        "DATABASE_URL=postgresql://u:p%40ss@host:5432/db\n"
        "QUOTED = 'x=y'\n"
    )
    assert read_env_file(env) == {"DATABASE_URL": "postgresql://u:p%40ss@host:5432/db", "QUOTED": "x=y"}


def test_read_env_file_missing(tmp_path):
    assert read_env_file(tmp_path / "nope") == {}


def test_exports_both_tables():
    assert set(QUERIES) == {"products.csv", "price_changes.csv"}
