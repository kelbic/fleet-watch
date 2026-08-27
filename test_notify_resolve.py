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


def test_empty_key_with_several_records_refuses_instead_of_writing_to_all(nt):
    """Пустой ключ — НЕ адрес: сторожа зовут notify() без key, и таких записей много.

    27.08 в инбоксе лежали ЧЕТЫРЕ тревоги с key="": resolve("", ...) записал бы один вывод
    во все и стёр вывод предыдущей сессии по window-watch. Молчание лучше вывода, разлитого
    по чужим тревогам — там, где адрес неоднозначен, «записал» неотличимо от «затёр».
    """
    nt.notify("тревога про кита-1", source="w")
    nt.notify("тревога про кита-2", source="w")
    assert nt.resolve("", "вывод во все сразу") is False       # отказ, а не залив
    assert all("resolution" not in r for r in _recs(nt)), "вывод разлился по чужим тревогам"


def test_match_and_ts_narrow_to_exactly_one(nt):
    """Уточнители СУЖАЮТ: по подстроке текста и по метке времени адрес становится точным."""
    nt.notify("тревога про кита-1", source="w")
    nt.notify("тревога про кита-2", source="w")
    assert nt.resolve("", "вывод по киту-2", match="кита-2") is True
    # notify() кладёт текст с подписью источника — адресуемся по подстроке, не по равенству
    one = [r for r in _recs(nt) if "кита-2" in r["text"]][0]
    two = [r for r in _recs(nt) if "кита-1" in r["text"]][0]
    assert one["resolution"] == "вывод по киту-2"
    assert "resolution" not in two                             # соседка не тронута
    # ts сужает вместе с match: обе тревоги пришли в одну секунду, одного ts мало
    assert nt.resolve("", "по метке", ts=two["ts"], match="кита-1") is True
    assert [r for r in _recs(nt) if "кита-1" in r["text"]][0]["resolution"] == "по метке"
    assert nt.resolve("", "мимо", match="такого текста нет") is False


def test_narrowing_that_still_matches_many_refuses(nt):
    """Уточнитель, не сузивший до одной записи, — не адрес: пишем НИЧЕГО."""
    nt.notify("кит-1: долг ноль", source="w")
    nt.notify("кит-2: долг ноль", source="w")
    assert nt.resolve("", "вывод", match="долг ноль") is False
    assert all("resolution" not in r for r in _recs(nt))
