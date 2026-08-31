"""Configuration loading/validation, and the on-disk response cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_integrity_agent.cache import ResponseCache, content_key
from test_integrity_agent.config import Config, api_key_present, write_starter_config
from test_integrity_agent.errors import ConfigError


def test_defaults_apply_when_no_config_file(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    assert config.model == "claude-sonnet-4-6"
    assert config.workers == 4
    assert config.config_path is None


def test_config_file_overrides_defaults(tiny_repo: Path) -> None:
    config = Config.load(tiny_repo)
    assert config.mutants_per_req == 2
    assert config.test_command == "python -m pytest -q"
    assert config.config_path is not None and config.config_path.name == "tia.yaml"


def test_unknown_option_is_rejected_with_the_valid_list(tmp_path: Path) -> None:
    (tmp_path / "tia.yaml").write_text("mutants: 5\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Config.load(tmp_path)
    assert "mutants" in exc.value.message
    assert "mutants_per_req" in (exc.value.hint or "")


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("timeout: 0\n", "at least 1 second"),
        ("timeout: 99999\n", "at most 3600"),
        ("mutants_per_req: 0\n", "between 1 and 25"),
        ("workers: 100\n", "between 1 and 64"),
        ("test_command: ''\n", "non-empty string"),
        ("timeout: two\n", "must be an integer"),
        ("fail_on_survivor: sometimes\n", "true or false"),
        ("requirements: []\n", "nothing to check"),
        ("source_paths: []\n", "nothing to mutate"),
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, body: str, match: str) -> None:
    (tmp_path / "tia.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Config.load(tmp_path)
    assert match in str(exc.value)


def test_malformed_yaml_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "tia.yaml").write_text("workers: [1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        Config.load(tmp_path)


def test_missing_explicit_config_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        Config.load(tmp_path, tmp_path / "nope.yaml")


def test_missing_repo_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        Config.load(tmp_path / "nowhere")


def test_missing_requirement_path_is_reported(tmp_path: Path) -> None:
    (tmp_path / "tia.yaml").write_text("requirements:\n  - missing.md\n", encoding="utf-8")
    config = Config.load(tmp_path)
    with pytest.raises(ConfigError, match="Requirements source not found"):
        config.resolved_requirement_paths()


def test_starter_config_round_trips(tmp_path: Path) -> None:
    target = write_starter_config(tmp_path)
    assert target.exists()
    config = Config.load(tmp_path)
    assert config.model == "claude-sonnet-4-6"
    with pytest.raises(ConfigError, match="already exists"):
        write_starter_config(tmp_path)
    assert write_starter_config(tmp_path, force=True) == target


def test_starter_config_never_mentions_a_key_field(tmp_path: Path) -> None:
    text = write_starter_config(tmp_path).read_text(encoding="utf-8")
    assert "api_key" not in text
    assert "ANTHROPIC_API_KEY" in text  # only as an instruction to use the environment


def test_api_key_presence_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert api_key_present() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert api_key_present() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert api_key_present() is True


# --- cache ------------------------------------------------------------------


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c")
    key = content_key("scope", "model", {"b": 1, "a": 2})
    assert cache.get(key) is None
    cache.set(key, {"value": [1, 2, 3]})
    assert cache.get(key) == {"value": [1, 2, 3]}
    assert cache.stats["hits"] == 1


def test_cache_key_is_order_independent_for_dicts() -> None:
    assert content_key("s", {"a": 1, "b": 2}) == content_key("s", {"b": 2, "a": 1})
    assert content_key("s", {"a": 1}) != content_key("s", {"a": 2})


def test_cache_ignores_a_corrupt_entry(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c")
    key = content_key("x")
    cache.set(key, {"ok": True})
    path = tmp_path / "c" / key[:2] / f"{key}.json"
    path.write_text("{not json", encoding="utf-8")
    assert cache.get(key) is None
    assert not path.exists()


def test_cache_writes_a_gitignore(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c")
    cache.set(content_key("x"), 1)
    assert (tmp_path / "c" / ".gitignore").read_text(encoding="utf-8").strip() == "*"


def test_disabled_cache_is_a_noop(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c", enabled=False)
    cache.set(content_key("x"), 1)
    assert cache.get(content_key("x")) is None
    assert not (tmp_path / "c").exists()


def test_clear_removes_entries(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c")
    for i in range(3):
        cache.set(content_key(i), i)
    assert cache.clear() == 3
    assert cache.get(content_key(0)) is None
