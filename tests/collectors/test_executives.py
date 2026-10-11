"""Tests de collectors/executives.py : search_news (frontière réseau) est mocké, jamais le vrai réseau."""
import json

from collectors import executives


def test_load_config_returns_empty_list_when_file_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(executives, "CONFIG", tmp_path / "missing.json")

    assert executives.load_config() == []


def test_load_config_returns_executives_list_from_a_valid_file(monkeypatch, tmp_path):
    cfg = tmp_path / "executives.json"
    cfg.write_text(json.dumps({"executives": [{"slug": "x", "name": "X", "ticker": "X", "query": "X"}]}), encoding="utf-8")
    monkeypatch.setattr(executives, "CONFIG", cfg)

    result = executives.load_config()

    assert result[0]["slug"] == "x"


def test_load_config_returns_empty_list_when_file_is_invalid_json(monkeypatch, tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text("{ceci n'est pas du json", encoding="utf-8")
    monkeypatch.setattr(executives, "CONFIG", cfg)

    assert executives.load_config() == []


def test_fetch_mentions_delegates_to_search_news_with_the_executives_query(monkeypatch):
    captured = {}

    def fake_search_news(query, days, limit):
        captured["query"] = query
        return [{"title": "t", "url": "u", "source": "s", "published": "p"}]

    monkeypatch.setattr(executives, "search_news", fake_search_news)

    result = executives.fetch_mentions({"query": "Jensen Huang NVIDIA"})

    assert captured["query"] == "Jensen Huang NVIDIA"
    assert result[0]["title"] == "t"
