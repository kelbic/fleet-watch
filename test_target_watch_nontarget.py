#!/usr/bin/env python3
"""Гейт направления у нецелевой позиции (правка 13.08): рост — в лог, движение вниз и
структурное — побудка.

Шов подменён НА RPC (`call`), а не над разбираемой веткой: watch_targets и вся логика
выбора log/tg исполняются настоящие ([[seam-stubbed-above-the-defect]] — 38 зелёных тестов
над мёртвым боевым путём). Транспорт (`notify`) перехвачен шпионом ТОЛЬКО ради счёта
побудок; сверх того выставлен MN_WATCH_MUTE=1, чтобы промах шпиона не ударил в живой инбокс
([[tests-never-touch-production-channels]]).

Первые два случая — проверка ВО ВРЕМЕНИ, а не в моменте: прогон на числах инцидента, затем
повторный прогон с тем же состоянием.
"""
import os
import sys

os.environ["MN_WATCH_MUTE"] = "1"          # второй контур: канал закрыт на транспорте
# Стейт — на выброс. Этот стенд боевой target-watch.json НЕ пишет (watch_targets только
# возвращает st), но дверь закрываем ЗАРАНЕЕ: соседний стенд 27.08 снёс им защёлки gone_*
# и подарил флоту залп из трёх ложных тревог. Гард должен стоять до того, как понадобится.
import tempfile
os.environ["MN_WATCH_STATE"] = os.path.join(
    tempfile.mkdtemp(prefix="tw-state-"), "target-watch.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib

tw = importlib.import_module("target-watch".replace("-", "_")) if False else None
# файл с дефисом в имени — грузим по пути
import importlib.util

# Путь скрипта переопределяем (11.09): перед боевой заменой стенд обязан гоняться по
# КАНДИДАТУ, иначе он зелен по старому файлу и о новом не говорит ничего. Умолчание —
# боевой файл, поэтому забыть переменную безопасно.
_SCRIPT = os.environ.get("MN_WATCH_SCRIPT") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "target-watch.py")
_spec = importlib.util.spec_from_file_location("target_watch", _SCRIPT)
tw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tw)

# ЦЕЛИ СТЕНДА — СВОИ, А НЕ БОЕВЫЕ (11.09). Радар перенацелен на окно 25.09, и трёх целей
# 27.08/28.08 в бою больше нет. Но проверяемый здесь МЕХАНИЗМ (гейт по направлению у
# нецелевой позиции, одноразовость звонка при обнулении, допуск на крошку) жив и стоит на
# денежном пути — вывод субъекта не должен убивать его покрытие
# ([[retired-subject-needs-watchdog-sweep]]). Поэтому фикстура инцидента 13.08 переезжает
# в сам стенд: регрессия остаётся проверяемой, а боевой список целей её не тащит.
tw.TARGETS = [
    {"name": "кит-1 27.08 ~$399k (фикстура стенда)",
     "market": "0xf27319855df886a604dda3d5675007f0aa6eee504c99f1d789c86b21075f7c20",
     "borrower": "0xfb94d3404c1d3d9d6f08f79e58041d5ea95accfa",
     "maturity": 1787788800, "loan_dec": 6, "coll_dec": 18, "coll": "WETH",
     "coll_move_pct": 0.0, "coll_move_dust": 10 ** 15},
    {"name": "кит-2 27.08 ~$314k (гейт закрыт, не цель) (фикстура стенда)",
     "market": "0x44495af1cca7842191a65a73978e01ed72238731e193c3b11460083efd60a318",
     "borrower": "0xd75ffb585ff88d3aa50b7cf9230b27a7eb923c20",
     "maturity": 1787788800, "loan_dec": 6, "coll_dec": 18, "coll": "cbBTC-USDC-collat",
     "not_target": True},
    {"name": "кит 28.08 ~$100k cbBTC (роллер 31.07) (фикстура стенда)",
     "market": "0x05959752fdeff325962b9d263edb421efc6e2186a49360dba6c32e86ebf6c84c",
     "borrower": "0xd418224ae3c510b645112fd9275ccfd50f996ee4",
     "maturity": 1787929200, "loan_dec": 6, "coll_dec": 8, "coll": "cbBTC",
     "coll_move_pct": 0.0},
]

K1, K2 = 0, 1                               # индексы целей: кит-1 (цель), кит-2 (не цель)
# Числа инцидента 13.08 (сверены eth_call на цепи, tx 0xb5504f3d…, блок 49934571)
D2_BEFORE, D2_AFTER = 296_900_881_000, 314_424_246_108
C2_BEFORE, C2_AFTER = 303716699582741949701672, 321614146615165119133621
D1 = 399_189_013_554
C1 = 442731735542517551237

SENT: list[str] = []
LOGGED: list[str] = []


def _install(chain: dict):
    """chain: {индекс цели: (долг_units, {слот: units})}"""
    def fake_call(to, data):
        sel = data[:10]
        for i, t in enumerate(tw.TARGETS):
            tail = tw._w(t["market"]) + tw._w(t["borrower"])
            if not data[10:].startswith(tail):
                continue
            debt, slots = chain[i]
            if sel == tw.SEL_DEBT:
                return debt
            if sel == tw.SEL_COLL_BITMAP:
                return sum(1 << k for k in slots)
            if sel == tw.SEL_COLL:
                return slots.get(int(data[10 + len(tail):], 16), 0)
        raise AssertionError(f"неожиданный вызов {sel}")
    tw.call = fake_call
    # 11.09: ветки копят поводы через alert(); отправка одна на прогон. Шпион — на новой
    # точке, но MN_WATCH_MUTE=1 выше остаётся вторым контуром: промах шпиона не ударит
    # в живой инбокс ([[tests-never-touch-production-channels]]).
    tw.alert = lambda text, **kw: SENT.append(text)
    tw.notify = lambda text, **kw: SENT.append(text)
    tw.log = lambda msg: LOGGED.append(msg)


def run(prev, chain):
    SENT.clear()
    LOGGED.clear()
    _install(chain)
    return tw.watch_targets(dict(prev))


def base_prev():
    return {"gone_0": False, "last_debt_0": D1 / 1e6, "coll_0": f"0:{C1}",
            "gone_1": False, "last_debt_1": D2_BEFORE / 1e6, "coll_1": f"1:{C2_BEFORE}",
            "gone_2": False, "last_debt_2": 100_277.700563, "coll_2": "0:299999996"}


def steady():
    return {K1: (D1, {0: C1}), K2: (D2_AFTER, {1: C2_AFTER}),
            2: (100_277_700_563, {0: 299999996})}


def check(name, cond, extra=""):
    print(("OK   " if cond else "ПРОВАЛ ") + name + (f"  {extra}" if not cond and extra else ""))
    return cond


ok = True

# 1) РЕАЛЬНЫЕ ЧИСЛА ИНЦИДЕНТА: долг +$17,523 и залог +5.89% у нецелевой позиции — тишина
st = run(base_prev(), steady())
ok &= check("инцидент 13.08 не будит агента", SENT == [], f"ушло: {SENT}")
ok &= check("рост долга записан в лог",
            any("рост нецелевой позиции" in m for m in LOGGED), str(LOGGED))
ok &= check("долив залога записан в лог",
            any("долив нецелевой" in m for m in LOGGED), str(LOGGED))
ok &= check("базлайн обновлён долгом", abs(st["last_debt_1"] - D2_AFTER / 1e6) < 1e-6)
ok &= check("базлайн обновлён залогом", st["coll_1"] == f"1:{C2_AFTER}")

# 2) ВО ВРЕМЕНИ: следующий прогон с тем же состоянием — по-прежнему тишина
st2 = run(st, steady())
ok &= check("повторный прогон молчит", SENT == [], f"ушло: {SENT}")

# 3) ДОЛГ ВНИЗ у нецелевой (погашение) — будит
run(st, {**steady(), K2: (D2_AFTER - 25_000_000_000, {1: C2_AFTER})})
ok &= check("погашение нецелевой будит", any("долг" in m for m in SENT), f"ушло: {SENT}")

# 4) ЗАЛОГ ВНИЗ у нецелевой (вывод) — будит
run(st, {**steady(), K2: (D2_AFTER, {1: int(C2_AFTER * 0.94)})})
ok &= check("вывод залога нецелевой будит",
            any("ЗАЛОГ ДВИНУЛСЯ" in m for m in SENT), f"ушло: {SENT}")

# 5) СТРУКТУРНОЕ (появился слот) у нецелевой — будит
run(st, {**steady(), K2: (D2_AFTER, {0: 5_000_000, 1: C2_AFTER})})
ok &= check("новый слот у нецелевой будит",
            any("состав слотов изменился" in m for m in SENT), f"ушло: {SENT}")

# 6) ДОЛГ → 0 у нецелевой — будит, ветка выше флага
run(st, {**steady(), K2: (0, {1: C2_AFTER})})
ok &= check("обнуление долга нецелевой будит",
            any("ДОЛГ ЦЕЛИ → $0" in m for m in SENT), f"ушло: {SENT}")

# 6a) ПРИЧИНУ СТОРОЖ НЕ НАЗЫВАЕТ (правка 26.08). Прежний текст утверждал «погашен сам»
# и ровно этим соврал про кит-1: позиция не умерла, а уехала на Morpho. Сторож видит
# ОДНО ЧИСЛО — про причину он обязан молчать вслух, иначе агент снимет планирование
# с живой цели ([[autopsy-verdict-from-protocol-logs]]).
ok &= check("причина НЕ утверждается",
            not any("погашен сам" in m for m in SENT), f"ушло: {SENT}")
ok &= check("названа развилка причин",
            any("ПРИЧИНА НЕ УСТАНОВЛЕНА" in m and "РЕФИНАНС" in m for m in SENT),
            f"ушло: {SENT}")

# 6b) ИНЦИДЕНТ 26.08 ЦЕЛИКОМ: у ЦЕЛИ (кит-1) долг → 0 И слоты исчезли той же tx
# (ролл Midnight → Morpho, блок 50481964). Это ОДНО событие, и звонков должно быть
# РОВНО ОДИН: 26.08 их пришло два с интервалом в секунду, второй — «ЗАЛОГ ДВИНУЛСЯ».
run(base_prev(), {**steady(), K1: (0, {})})
ok &= check("ролл кита-1 будит ровно одним звонком", len(SENT) == 1, f"ушло: {SENT}")
ok &= check("дубля про залог нет",
            not any("ЗАЛОГ ДВИНУЛСЯ" in m for m in SENT), f"ушло: {SENT}")
ok &= check("исчезновение слотов при нулевом долге записано в лог",
            any("второй звонок не шлю" in m for m in LOGGED), str(LOGGED))

# 6в) НЕГАТИВНЫЙ КОНТРОЛЬ К ГЕЙТУ: залог, выведенный при ЖИВОМ долге, будит по-прежнему
# — гейт стоит на d==0, а не на «слотов не стало».
run(base_prev(), {**steady(), K1: (D1, {})})
ok &= check("вывод залога при живом долге будит",
            any("ЗАЛОГ ДВИНУЛСЯ" in m for m in SENT), f"ушло: {SENT}")

# 7) КОНТРОЛЬ: у ЦЕЛИ (кит-1) рост по-прежнему будит — гейт не поехал на всех
run(st, {**steady(), K1: (D1 + 12_000_000_000, {0: C1})})
ok &= check("рост ЦЕЛИ по-прежнему будит", any("долг" in m for m in SENT), f"ушло: {SENT}")

# 8) КОНТРОЛЬ: рост ниже порогов молчит у обеих (пороги не тронуты)
run(st, {**steady(), K1: (D1 + 100_000, {0: C1})})
ok &= check("подпороговый рост цели молчит", SENT == [], f"ушло: {SENT}")

print("\nИТОГ:", "ВСЁ ЗЕЛЁНОЕ" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
