#!/usr/bin/env python3
"""Гейт чужих ликвидаций: судить ПРИЗОМ и не терять исход в переписи (инцидент 27.08).

Два дефекта одного прохода 27.08 00:30:03Z, у каждого свой негативный контроль:
  1) гейт сравнивал с полом огня РЕПЕЙ (размер вложения конкурента), а пол стоит на ЧИСТОМ
     профите ⇒ две тревоги «мы могли её взять и не взяли» на призах $23.94 и $3.11 против
     пола $300;
  2) ветка базлайна делала `continue` на ПЕРВОМ событии нового адреса ⇒ ликвидация кита-2
     на $398,259.83 (блок 50500435, первый по возрастанию) не попала в лог ВООБЩЕ, а крошка
     блоком позже дала алерт.

Шов подменён НА RPC и на источник событий, а НЕ над разбираемой веткой: гейт, выбор
log/tg и расчёт приза исполняются настоящие ([[seam-stubbed-above-the-defect]]).
"""
import importlib.util
import os
import sys
import tempfile

os.environ["MN_WATCH_MUTE"] = "1"          # второй контур: канал закрыт на транспорте
# И ОТДЕЛЬНО — СТЕЙТ. Замутить канал НЕДОСТАТОЧНО: _scan_foreign пишет защёлку
# json.dump(cur, open(STATE,"w")), и первая версия этого стенда снесла боевой
# target-watch.json — крон-прогон 27.08 01:15:02Z прочитал пустое prev и выдал залп из трёх
# ложных «ДОЛГ ЦЕЛИ → $0». Путь уводим ДО импорта модуля: STATE вычисляется на импорте.
os.environ["MN_WATCH_STATE"] = os.path.join(
    tempfile.mkdtemp(prefix="tw-state-"), "target-watch.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Путь скрипта переопределяем (11.09): перед боевой заменой стенд обязан гоняться по
# КАНДИДАТУ, иначе он зелен по старому файлу и о новом не говорит ничего. Умолчание —
# боевой файл, поэтому забыть переменную безопасно.
_SCRIPT = os.environ.get("MN_WATCH_SCRIPT") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "target-watch.py")
_spec = importlib.util.spec_from_file_location("tw_fg", _SCRIPT)
tw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tw)

M2 = "0x44495af1cca7842191a65a73978e01ed72238731e193c3b11460083efd60a318"   # рынок кита-2
M1 = "0xf27319855df886a604dda3d5675007f0aa6eee504c99f1d789c86b21075f7c20"   # рынок кита-1
CALLER = "0x5d183c7ec27e2bc7d07d7c2d6e206ebd667c1c5b"
COLL = "0x" + "11" * 20
# Числа инцидента, сверены логами цепи (tx 0xd734fb0e…, блок 50500435).
WHALE = {"caller": CALLER, "market": M2, "collateral": COLL,
         "seized": 397040212952344958252731, "repaid": 398259832936, "post": True,
         "block": 50500435}
CRUMB = {"caller": CALLER, "market": M1, "collateral": COLL,
         "seized": 10033170075429062467676, "repaid": 10063886027, "post": True,
         "block": 50500436}
# Приз считается как seized*price/scale − repaid. Обёртка ≈$1.0 ⇒ приз ≈ 0 при репее $398k.
PRICE_1USD = tw.ORACLE_PRICE_SCALE * 10 ** 6 // 10 ** 18      # $1.00 за юнит 18-знакового

META = {"loan_sym": "USDC", "loan_dec": 6, "usd": True,
        "floor_units": 300_000_000, "colls": {COLL: ("0x" + "22" * 20, 18, "wrap")}}


def _run(events, prev, price=PRICE_1USD):
    sent, logged = [], []
    tw.market_meta = lambda: {M1.lower(): META, M2.lower(): META}
    tw.foreign_liquidations = lambda a, b: list(events)
    tw.rpc = lambda m, p: (hex(price) if m == "eth_call" else
                           (hex(50500700) if m == "eth_blockNumber" else "0x0"))
    # 11.09: точка отправки переехала — ветки больше не зовут notify()/tg() напрямую,
    # они КОПЯТ поводы через alert(), и отправка одна на прогон (flush_reasons).
    # Шпион переставлен на новую точку; сам гейт (приз vs пол, базлайн) исполняется
    # настоящий — шов как был на RPC и на источнике событий, а не над разбираемой веткой.
    tw.alert = lambda text, **kw: sent.append(text)
    tw.notify = lambda text, **kw: sent.append(text)
    tw.log = lambda msg: logged.append(msg)
    tw._scan_foreign(prev, dict(prev), "27.08 00:30Z", 50500700)
    return sent, logged


def _money_alerts(sent):
    return [s for s in sent if "ДЕНЬГИ ПРОШЛИ МИМО" in s]


def test_prize_below_floor_does_not_claim_missed_money():
    """Крошка: репей $10,063.89 ВЫШЕ пола $300, приз $23.94 — НИЖЕ. Денег мимо не прошло."""
    sent, logged = _run([CRUMB], {"seen_callers": [CALLER]})
    assert not _money_alerts(sent), f"тревога на призе ниже пола: {sent}"
    assert any("ниже пола огня по приз" in m for m in logged), logged
    # НЕГАТИВНЫЙ КОНТРОЛЬ: сравнение по репею (снятый фикс) эту позицию бы заалертило.
    assert CRUMB["repaid"] > META["floor_units"], "стенд не воспроизводит дефект"


def test_prize_above_floor_still_alerts():
    """Позитивный контроль: приз ВЫШЕ пола обязан будить — гейт не заглушен целиком."""
    rich = dict(CRUMB, seized=20_000 * 10 ** 18, repaid=10_000_000_000)   # приз ≈ $10k
    sent, _ = _run([rich], {"seen_callers": [CALLER]})
    assert _money_alerts(sent), "приз $10k выше пола $300, а тревоги нет"


def test_unknown_prize_falls_back_to_repay_not_to_silence():
    """Нет цены оракула ⇒ судим репеем: ошибка в сторону лишнего алерта, а не тишины."""
    sent, _ = _run([CRUMB], {"seen_callers": [CALLER]}, price=0)     # оракул молчит
    assert _money_alerts(sent), "незнание приза выдано за «денег не было»"


def test_baseline_records_the_outcome_it_used_to_swallow():
    """Кит-2 на $398k приходит ПЕРВЫМ у НОВОГО адреса на первом проходе.

    Раньше ветка базлайна делала continue и событие исчезало бесследно. Теперь базлайн
    гасит только свой алерт 🆕, а исход считается общим правилом и виден в логе.
    """
    sent, logged = _run([WHALE, CRUMB], {})                 # prev без seen_callers ⇒ baseline
    assert not any("НОВЫЙ ЛИКВИДАТОР" in s for s in sent), "базлайн обязан молчать про 🆕"
    whale_seen = [m for m in logged if str(WHALE["block"]) in m]
    assert whale_seen, f"событие $398k снова исчезло бесследно: {logged}"
    assert any("ниже пола огня" in m for m in whale_seen), whale_seen
