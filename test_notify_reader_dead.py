"""HIL-случай 3 из шапки notify: «сломана сама автоматика доставки — инбокс не разбирается».

30.10: заморозка 14.10 оставила Term-сторожей, но выключила agent-wake. Две главные
тревоги route-watch (exit-collapsed, foreign-pool) легли в инбокс, который никто не
читал; одна из них — с 23.10. Тишина инбокса была неотличима от «всё спокойно».
Живость разборщика доказывается ПОЛОЖИТЕЛЬНО: agent-wake пишет строку в свой лог
каждый прогон (*/20), даже когда «не бужу».
"""
import importlib
import json
import os
import time

import pytest


@pytest.fixture()
def nt(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_INBOX", str(tmp_path / "inbox.jsonl"))
    monkeypatch.setenv("FLEET_DEDUP_STATE", str(tmp_path / "dedup.json"))
    import notify as _n
    n = importlib.reload(_n)
    monkeypatch.setattr(n, "muted", lambda: False)
    return n


def _reader_log(n, age_sec):
    with open(n.READER_LOG, "w") as f:
        f.write("agent-wake: 0 тревог\n")
    t = time.time() - age_sec
    os.utime(n.READER_LOG, (t, t))


def _recs(n):
    with open(n.INBOX) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_stand_never_reads_the_production_reader_log(nt, tmp_path):
    assert os.path.dirname(nt.READER_LOG) == str(tmp_path), \
        f"стенд смотрит на чужой лог разборщика: {nt.READER_LOG}"


def test_live_reader_keeps_the_inbox_route(nt, monkeypatch):
    _reader_log(nt, 60)
    monkeypatch.setattr(nt, "_tg", lambda text: (_ for _ in ()).throw(AssertionError("tg вызван")))
    assert nt.notify("шум", source="t", hil=False, key="a1") == "inbox"


def test_dead_reader_escalates_to_the_human(nt, monkeypatch):
    _reader_log(nt, 16 * 86400)                     # как 14.10 -> 30.10
    sent = []
    monkeypatch.setattr(nt, "_tg", lambda text: sent.append(text) or True)
    assert nt.notify("ВЫХОД ОБВАЛИЛСЯ", source="term-route-watch", hil=False, key="b1") == "tg"
    assert len(sent) == 1, "мёртвый разборщик, а тревога ушла в инбокс молча"
    assert sent[0].startswith("[term-route-watch]"), "потерян автор строки"
    assert "разбор" in sent[0] and "инбокс" in sent[0], f"человеку не сказано почему: {sent[0]}"
    recs = _recs(nt)
    assert recs[0]["escalated"], "в записи нет причины эскалации"
    assert any(r.get("handled") and r.get("outcome") == "tg" for r in recs), \
        "нет tg-надгробия: вернувшийся agent-wake разбудит сессию на доставленное"


def test_missing_reader_log_is_a_dead_reader(nt, monkeypatch):
    sent = []
    monkeypatch.setattr(nt, "_tg", lambda text: sent.append(text) or True)
    assert nt.notify("тревога", source="t", hil=False, key="c1") == "tg"
    assert len(sent) == 1


def test_failed_escalation_keeps_retry_open(nt, monkeypatch):
    """Недоставленная эскалация ведёт себя как недоставленный HIL: дедуп снят."""
    _reader_log(nt, 16 * 86400)
    monkeypatch.setattr(nt, "_tg", lambda text: False)
    assert nt.notify("тревога", source="t", hil=False, key="d1", dedup_sec=3600) == "inbox"
    assert nt.notify("тревога", source="t", hil=False, key="d1", dedup_sec=3600) == "inbox"
