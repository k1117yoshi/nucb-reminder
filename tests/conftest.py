import importlib.util
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"


def _load(name, path):
    # 両関数とも app.py なので、別名で毎回読み込み直してモジュール内キャッシュもリセットする
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def reminder_app(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "deadlines")
    monkeypatch.setenv("GSI_NAME", "start-date-index")
    monkeypatch.setenv("TOKEN_PARAM", "/line-reminder/channel-access-token")
    monkeypatch.setenv("GROUP_ID_PARAM", "/line-reminder/group-id")
    return _load("reminder_app", SRC / "reminder" / "app.py")


@pytest.fixture
def webhook_app(monkeypatch):
    monkeypatch.setenv("SECRET_PARAM", "/line-reminder/channel-secret")
    return _load("webhook_app", SRC / "webhook" / "app.py")
