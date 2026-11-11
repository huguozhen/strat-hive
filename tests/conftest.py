import pytest


@pytest.fixture(autouse=True)
def isolated_user_directories(tmp_path, monkeypatch):
    for variable, directory in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"),
                                ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(variable, str(tmp_path / directory))
