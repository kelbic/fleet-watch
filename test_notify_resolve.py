"""resolve(): вывод разбора обязан лечь в инбокс и пережить параллельный notify().

Тест существует потому, что вывод разбора терялся дважды (20.08, 22.08) — сессию убивал
таймаут, а поле resolution никто не писал. Проверяем ровно то, чем механизм отличается от
ручной перезаписи: атомарность, попадание в НУЖНУЮ запись и сохранность соседних строк.
"""
import importlib
import json
import os

import pytest


@pytest.fixture()
def nt(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_INBOX", str(tmp_path / "inbox.jsonl"))
    monkeypatch.setenv("MN_MUTE", "1")            # никаких боевых каналов из теста
    import notify as _n
    n = importlib.reload(_n)
    monkeypatch.setattr(n, "muted", lambda: False)   # но инбокс — обычный, не .muted
    monkeypatch.setattr(n, "_tg", lambda text: False)
    return n


def _recs(n):
    with open(n.INBOX) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_resolution_lands_on_the_right_record(nt):
    nt.notify("тревога A", source="w", key="k:a")
    nt.notify("тревога B", source="w", key="k:b")
    assert nt.resolve("k:b", "вывод по B") is True
    by_key = {r["key"]: r for r in _recs(nt)}
    assert by_key["k:b"]["resolution"] == "вывод по B"
    assert "resolution" not in by_key["k:a"]        # соседняя тревога не тронута


def test_concurrent_notify_survives_the_rewrite(nt):
    nt.notify("тревога A", source="w", key="k:a")
    nt.resolve("k:a", "черновик")
    nt.notify("приехала ПОКА шёл разбор", source="w", key="k:late")   # append после резолюции
    nt.resolve("k:a", "финал")                                        # вторая перезапись
    keys = [r["key"] for r in _recs(nt)]
    assert "k:late" in keys, "перезапись съела тревогу, приехавшую во время разбора"
    assert [r for r in _recs(nt) if r["key"] == "k:a"][0]["resolution"] == "финал"


def test_unknown_and_handled_keys_do_not_silently_succeed(nt):
    nt.notify("тревога A", source="w", key="k:a")
    assert nt.resolve("k:нет-такого", "в пустоту") is False   # незнание не выдаётся за работу
    recs = _recs(nt)
    recs[0]["handled"] = True
    with open(nt.INBOX, "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    assert nt.resolve("k:a", "поздно") is False               # разобранное не переписываем
    assert "resolution" not in _recs(nt)[0]


def test_broken_inbox_reports_failure_instead_of_claiming_success(nt):
    with open(nt.INBOX, "w") as f:
        f.write("не json\n")
    assert nt.resolve("k:a", "вывод") is False
