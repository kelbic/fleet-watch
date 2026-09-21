#!/usr/bin/env python3
"""Стенд лестницы порогов midnight-chains (21.09, крон-триаж тревоги ethereum-alive).

ЧТО ПРОВЕРЯЕТСЯ. Прежняя защёлка `usd >= 5000 and was < 5000` стреляла только на ПЕРВОМ
пересечении: после записи books.ethereum.usd=5225 рост книги до $5M давал was=5225 и
МОЛЧАНИЕ. Стенд держит четыре свойства:
  1) первый прогон после апгрейда на той же книге ($5,225) НЕ звонит повтором;
  2) рост $5,225 -> $60,000 даёт РОВНО одну тревогу с номиналом ступени в КЛЮЧЕ
     (иначе dedup 7 суток съел бы её как повтор прежнего ключа);
  3) верхняя ступень зовёт оценить перенос, нижние — ПРЯМО запрещают его как основание
     (прежний текст нёс «Перенос бота — 1–2 дня» на любой сумме);
  4) НЕГАТИВНЫЙ КОНТРОЛЬ: провал чтения цепи не сбрасывает ступень — иначе следующий
     успешный прогон выдал бы ложное «КНИГА ОЖИЛА» на неизменившейся книге.

ШОВ подменён НА ГРАНИЦЕ СЕТИ (rpc/market_ids/total_units/registry) и на канале тревог;
сама лестница, тексты и запись state исполняются настоящие ([[seam-stubbed-above-the-defect]]).
STATE уводится в temp ДО main(): боевой midnight-chains.state — защёлка живого сторожа.
"""
import importlib.util, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("MC_SCRIPT") or os.path.join(HERE, "midnight-chains-watch.py")
_spec = importlib.util.spec_from_file_location("mc_tiers", SCRIPT)
mc = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mc)

TMP = tempfile.mkdtemp(prefix="mc-tiers-")
mc.STATE = os.path.join(TMP, "midnight-chains.state")
mc.LOG = os.path.join(TMP, "midnight-chains.log")
CTRL_OK = hex(1_461_302_956_447)
FIRED = []
mc.alarm = lambda text, key: (FIRED.append((key, text)), "стенд")[1]
mc.registry_chains = lambda: (set(mc.KNOWN_MIDNIGHT), 134293)
mc.market_ids = lambda cfg, frm, to, t0: {}
mc.decimals = lambda cfg, token: 6
MID = "0x" + "ab" * 32
BOOK = {"ethereum": 0.0, "arc": 0.0, "robinhood": 0.0}
DEAD = set()          # цепи, чьё чтение падает в этом прогоне


def fake_rpc(rpcs, method, params, timeout=30, tries=4):
    if method == "eth_blockNumber":
        return hex(26_024_470)
    if method == "eth_call":
        return CTRL_OK                      # положительный контроль прибора
    raise AssertionError("сеть в стенде: " + method)


mc.rpc = fake_rpc


def fake_total_units(cfg, ids, sel):
    name = [n for n, c in mc.CHAINS.items() if c is cfg][0]
    if name in DEAD:
        raise RuntimeError("узлы отказали: стенд")
    raw = int(round(BOOK[name] * 10 ** 6))
    return (raw, {MID: raw} if raw else {})


mc.total_units = fake_total_units


def run(eth_usd, seed=None, dead=()):
    """Один прогон main() на заданной книге. seed=None — брать state предыдущего прогона."""
    global DEAD
    DEAD = set(dead)
    BOOK["ethereum"] = eth_usd
    if seed is not None:
        json.dump(seed, open(mc.STATE, "w"))
    FIRED.clear()
    rc = mc.main()
    assert rc == 0, rc
    return list(FIRED), json.load(open(mc.STATE))


CHAINS_SEED = {n: {"mk": {MID: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"},
                   "scanned": 1, "fails": 0} for n in mc.CHAINS}
# СТАРЫЙ state — ровно тот, что лежит в бою на 21.09: книга уже за растяжкой, ключа tiers нет.
LEGACY = {"ts": 1789976596, "ctrl_units": 1461302956447,
          "books": {"ethereum": {"markets": 817, "usd": 5225.095459690663,
                                 "head": 26024470, "nonzero": 18}},
          "chains": CHAINS_SEED, "reg_new": []}

ok = True


def check(cond, msg):
    global ok
    print(("ok   " if cond else "ПРОВАЛ ") + msg)
    ok = ok and bool(cond)


# 1. Апгрейд на живой книге $5,225 — повтора быть не должно.
fired, st = run(5225.095459690663, seed=LEGACY)
check(not fired, f"апгрейд на прежней книге молчит (было {fired})")
check(st["tiers"]["ethereum"] == 0, f"ступень 0 записана: {st.get('tiers')}")

# 2. Рост до $60,000 — ровно одна тревога, номинал ступени в КЛЮЧЕ.
fired, st = run(60_000.0)
check(len(fired) == 1, f"ровно одна тревога, получено {len(fired)}")
check(fired and fired[0][0] == "midnight-chains:ethereum-alive-50000",
      f"ключ несёт номинал ступени: {fired and fired[0][0]}")
check(fired and "НЕ обоснован" in fired[0][1], "средняя ступень запрещает перенос")
check(fired and "1–2 дня" not in fired[0][1], "средняя ступень не зовёт переносить бота")
check(st["tiers"]["ethereum"] == 1, "ступень 1 записана")

# 3. Тот же уровень ещё раз — молчание (лестница не звенит на месте).
fired, _ = run(59_000.0)
check(not fired, f"на той же ступени молчит (было {fired})")

# 4. НЕГАТИВНЫЙ КОНТРОЛЬ ступени: провал чтения цепи не сбрасывает её.
fired, st = run(60_000.0, dead=("ethereum",))
check(not fired, "упавшее чтение не звонит книгой")
check(st["tiers"].get("ethereum") == 1, f"ступень пережила провал: {st.get('tiers')}")
fired, _ = run(60_000.0)
check(not fired, "после провала нет ложного «КНИГА ОЖИЛА» на той же книге")

# 5. Верхняя ступень — зовёт оценить перенос.
fired, st = run(600_000.0)
check(len(fired) == 1 and fired[0][0] == "midnight-chains:ethereum-alive-500000",
      f"ключ верхней ступени: {fired and fired[0][0]}")
check(fired and "ОЦЕНИТЬ перенос" in fired[0][1], "верхняя ступень зовёт оценить перенос")

# 6. Свежая цепь сразу выше всех ступеней — одна тревога верхней ступени, не залп.
fired, st = run(900_000.0, seed={"chains": CHAINS_SEED})
check(len(fired) == 1 and fired[0][0] == "midnight-chains:ethereum-alive-500000",
      f"с нуля — одна тревога верхней ступени, получено {[f[0] for f in fired]}")

# 7. КОНТРОЛЬ СТЕНДА: со старой защёлкой свойство 2 обязано ПРОВАЛИТЬСЯ.
legacy_fire = 60_000.0 >= mc.WAKE_UNITS and 5225.095459690663 < mc.WAKE_UNITS
check(not legacy_fire, "старая защёлка на росте $5,225 -> $60,000 МОЛЧАЛА (дефект воспроизведён)")

print("ИТОГ:", "ЗЕЛЁНЫЙ" if ok else "КРАСНЫЙ")
sys.exit(0 if ok else 1)
