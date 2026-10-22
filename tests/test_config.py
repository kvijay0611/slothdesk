from pathlib import Path

import pytest

from slothdesk.config import Config, ConfigError, load_config


def test_defaults_are_local_and_safe(root):
    c = load_config(root, env={})
    assert c.base_url.startswith("http://127.0.0.1")
    assert c.allow_remote is False and c.min_confidence == 0.6
    assert "Misc" in c.categories


def test_env_overrides_file(root):
    (root / ".slothdesk").mkdir()
    (root / ".slothdesk" / "config.toml").write_text('model = "from-file"\nmax_retries = 1\n')
    c = load_config(root, env={"SLOTHDESK_MODEL": "from-env"})
    assert c.model == "from-env" and c.max_retries == 1


def test_api_key_never_from_file_and_hidden_in_repr(root, tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('api_key = "sk-secret"\n')
    with pytest.raises(ConfigError, match="SLOTHDESK_API_KEY"):
        load_config(root, config_file=f, env={})
    c = load_config(root, env={"SLOTHDESK_API_KEY": "sk-secret"})
    assert "sk-secret" not in repr(c) and c.api_key == "sk-secret"


@pytest.mark.parametrize("bad", ["foo = 1", "max_retries = 'x'", "min_confidence = 0",
                                 "timeout_s = -1", "provider = 'carrier-pigeon'",
                                 "categories = ['Work']", "categories = ['Misc', 'bad name!']",
                                 "categories = ['Misc', 'Misc']", "log_level = 'LOUD'",
                                 "base_url = 'ftp://x'", "allow_remote = 'maybe'",
                                 "max_file_mb = 0", "text_chars = 5"])
def test_invalid_values_rejected(root, tmp_path, bad):
    f = tmp_path / "c.toml"
    f.write_text(bad + "\n")
    with pytest.raises(ConfigError):
        load_config(root, config_file=f, env={})


def test_bad_toml_and_missing_file(root, tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("this is = = not toml")
    with pytest.raises(ConfigError):
        load_config(root, config_file=f, env={})
    with pytest.raises(ConfigError, match="not found"):
        load_config(root, config_file=tmp_path / "nope.toml", env={})


def test_categories_as_csv_and_bool_strings(root):
    c = load_config(root, env={"SLOTHDESK_ALLOW_REMOTE": "yes"})
    assert c.allow_remote is True
    f = root / "c.toml"
    f.write_text('categories = "Work, Misc"\nrequire_ac_power = "off"\n')
    c = load_config(root, config_file=f, env={})
    assert c.categories == ("Work", "Misc") and c.require_ac_power is False


def test_refuses_dangerous_roots(tmp_path):
    with pytest.raises(ConfigError):
        load_config(Path.home(), env={})
    with pytest.raises(ConfigError):
        Config(root=Path(Path.home().anchor))
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing", env={})
    with pytest.raises(ConfigError):
        Config(root=tmp_path / "missing")


def test_derived_paths(cfg):
    assert cfg.sorted_dir.name == "Sorted" and cfg.db_path.parent.name == ".slothdesk"
