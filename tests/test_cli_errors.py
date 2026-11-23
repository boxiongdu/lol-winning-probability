"""Useful diagnostics must not disclose keys, DSNs, or bad file contents."""
import json
import pytest
from src.ingestion.__main__ import main


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    # Never depend on the developer's real environment or root .env.
    monkeypatch.setattr("src.ingestion.__main__.load_environment", lambda: None)
    monkeypatch.setattr("src.config.load_environment", lambda: None)
    for key in ("RIOT_API_KEY", "DATABASE_URL", "RIOT_REGION", "TARGET_MATCH_COUNT",
                "RIOT_QUEUE_ID", "RIOT_REQUEST_INTERVAL"):
        monkeypatch.delenv(key, raising=False)


def seed_file(tmp_path, text=None):
    path = tmp_path / "seeds.json"
    path.write_text(text if text is not None else json.dumps([{"game_name": "Davemon", "tag_line": "NA1"}]))
    return str(path)


def test_missing_key(tmp_path, capsys):
    assert main(["collect", "--seeds", seed_file(tmp_path)]) == 1
    assert "RIOT_API_KEY is missing" in capsys.readouterr().err


def test_missing_database_url(capsys):
    assert main(["init-db"]) == 1
    assert "DATABASE_URL is missing" in capsys.readouterr().err


@pytest.mark.parametrize("text,message", [
    ('{"game_name":"Davemon","tag_line":"NA1"}', "square brackets"),
    ('[{"game_name":"Davemon"}]', "both game_name and tag_line"),
    ('[{"game_name": "DO_NOT_ECHO_THIS",}]', "line 1, column"),
    ('[]', "nonempty JSON array"),
])
def test_invalid_seeds(tmp_path, capsys, text, message):
    assert main(["collect", "--seeds", seed_file(tmp_path, text)]) == 1
    output = capsys.readouterr().err
    assert message in output and "DO_NOT_ECHO_THIS" not in output


@pytest.mark.parametrize("key", ["TARGET_MATCH_COUNT", "RIOT_QUEUE_ID", "RIOT_REQUEST_INTERVAL"])
def test_invalid_environment_numbers(tmp_path, monkeypatch, capsys, key):
    monkeypatch.setenv(key, "DO_NOT_ECHO_THIS")
    assert main(["collect", "--seeds", seed_file(tmp_path)]) == 1
    output = capsys.readouterr().err
    assert key in output and "DO_NOT_ECHO_THIS" not in output


def test_missing_seed_file(tmp_path, capsys):
    assert main(["collect", "--seeds", str(tmp_path / "absent.json")]) == 1
    assert "Input file not found" in capsys.readouterr().err


def test_database_error_redacted(monkeypatch, capsys):
    import psycopg
    def fail(_):
        raise psycopg.OperationalError("postgresql://private:SECRET@localhost/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://private:SECRET@localhost/db")
    monkeypatch.setattr("src.ingestion.__main__.Database", fail)
    assert main(["init-db"]) == 1
    output = capsys.readouterr().err
    assert "Cannot connect to PostgreSQL" in output
    assert "SECRET" not in output
