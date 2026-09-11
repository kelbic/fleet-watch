"""Дедуп и недоставленный HIL: отметка ставится до отправки, значит провал доставки обязан
её снимать — иначе настоящая тревога молчит весь ttl (30 суток у радара 25.09)."""
import importlib
import json

import pytest


@pytest.fixture()
def nt(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_INBOX", str(tmp_path / "inbox.jsonl"))
    monkeypatch.setenv("FLEET_DEDUP_STATE", str(tmp_path / "dedup.json"))
    monkeypatch.setenv("MN_MUTE", "1")
    import notify as _n
    n = importlib.reload(_n)
    monkeypatch.setattr(n, "muted", lambda: False)
    return n


def _keys(n):
    try:
        return set(json.load(open(n.DEDUP_STATE)))
    except Exception:  # noqa: BLE001
        return set()


def test_failed_hil_releases_dedup_and_retries(nt, monkeypatch):
    calls = []
    monkeypatch.setattr(nt, "_tg", lambda text: calls.append(text) or False)
    assert nt.notify("тревога", source="t", hil=True, key="k1", dedup_sec=3600) == "inbox"
    assert "k1" not in _keys(nt), "провал доставки оставил отметку — повтор заглушен"
    assert nt.notify("тревога", source="t", hil=True, key="k1", dedup_sec=3600) == "inbox"
    assert len(calls) == 2, "второй прогон обязан снова пробовать доставить"


def test_delivered_hil_keeps_dedup(nt, monkeypatch):
    calls = []
    monkeypatch.setattr(nt, "_tg", lambda text: calls.append(text) or True)
    assert nt.notify("тревога", source="t", hil=True, key="k2", dedup_sec=3600) == "tg"
    assert "k2" in _keys(nt)
    assert nt.notify("тревога", source="t", hil=True, key="k2", dedup_sec=3600) == "dedup"
    assert len(calls) == 1, "доставленное не должно двоиться"


def test_inbox_only_alert_keeps_dedup(nt, monkeypatch):
    # hil=False никогда не ходит в TG: отметка обязана стоять, иначе агент получит дубли
    monkeypatch.setattr(nt, "_tg", lambda text: (_ for _ in ()).throw(AssertionError("tg вызван")))
    assert nt.notify("шум", source="t", hil=False, key="k3", dedup_sec=3600) == "inbox"
    assert "k3" in _keys(nt)
    assert nt.notify("шум", source="t", hil=False, key="k3", dedup_sec=3600) == "dedup"
