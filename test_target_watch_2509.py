#!/usr/bin/env python3
"""ПОЗИТИВНЫЙ КОНТРОЛЬ радара окна 25.09: каждая ветка даёт РОВНО ОДНО сообщение,
повторный прогон того же состояния — НОЛЬ (защёлка по составу поводов).

Зачем именно так. Перенацеливание 11.09 меняет все четыре ветки сразу, и «тихо» после
правки ничего не доказывает: ровно этим две недели занимался прежний радар — был жив,
тикал и не мог предупредить ни о чём ([[dead-watchdog-worse-than-none]]). Поэтому стенд
обязан ЗАЖЕЧЬ каждую ветку на подменённых ответах RPC, а не убедиться, что она молчит
([[test-that-can-only-return-zero]]).

ШОВ — НА RPC, А НЕ НАД ГЕЙТОМ. Подменяются `rpc()` и `call()`, то есть ответы цепи;
`main()`, все гейты, выбор log/alert, сборщик поводов, ключ защёлки и САМ `notify()`
исполняются настоящие ([[seam-stubbed-above-the-defect]]: 38 зелёных тестов над мёртвым
боевым путём — это уже было).

ТРАНСПОРТ ЗАКРЫТ ТРЕМЯ ДВЕРЬМИ, и ни одна не полагается на дисциплину автора теста
([[tests-never-touch-production-channels]]):
  1. MN_WATCH_MUTE=1 — сторож notify.muted() на самом транспорте;
  2. FLEET_INBOX → временный файл: боевая очередь агента не пачкается, и по нему же
     СЧИТАЮТСЯ сообщения (счёт берётся с настоящей доставки, а не со шпиона);
  3. FLEET_DEDUP_STATE → временный файл: иначе стенд засеял бы БОЕВЫЕ ключи дедупа и
     заглушил настоящую тревогу того же состава на 30 суток;
  4. MN_WATCH_STATE → временный файл: 27.08 стенд с замученным каналом всё равно снёс
     боевой target-watch.json и подарил флоту залп из трёх ложных тревог.

Прогон: python3 ~/.fleet-watch/test_target_watch_2509.py
"""
import importlib.util
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="tw2509-")
os.environ["MN_WATCH_MUTE"] = "1"
os.environ["MN_WATCH_STATE"] = os.path.join(_TMP, "target-watch.json")
os.environ["FLEET_INBOX"] = os.path.join(_TMP, "agent-inbox.jsonl")
os.environ["FLEET_DEDUP_STATE"] = os.path.join(_TMP, "notify-dedup.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Путь скрипта переопределяем: перед боевой заменой стенд гоняется по кандидату, а не по
# тому, что сейчас лежит в бою. Умолчание — боевой файл, поэтому забыть переменную безопасно.
_SCRIPT = os.environ.get("MN_WATCH_SCRIPT") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "target-watch.py")
_spec = importlib.util.spec_from_file_location("tw2509", _SCRIPT)
tw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tw)

# Журнал notify — тоже во временный каталог: он не канал, но и засорять боевой файл
# строками стенда незачем (LOG_FILE вычисляется на импорте, поэтому правим модуль).
sys.modules["notify"].LOG_FILE = os.path.join(_TMP, "notify.log")

# Куда notify() кладёт замученные записи (см. notify.INBOX + ".muted").
MUTED = os.environ["FLEET_INBOX"] + ".muted"
KIT = tw.TARGETS[0]["borrower"]
MKT = tw.MARKET_2509
HUB = "0xdbb74bf80bd05a1959c8f21e2b821dcfed87104c"
HEAD = 51_200_000       # растёт на каждый прогон: см. advance() — скан логов
                        # инкрементальный, на неподвижной голове он пуст ПО
                        # ПОСТРОЕНИЮ, и любой лог-тест вернул бы ноль зря.
# Боевые числа 11.09 (PREFLIGHT §1, блок 51161260).
DEBT = {0: 1_005_266_086_395, 1: 167_453_420_000, 2: 119_941_350_000, 3: 51_029_200_000}
COLL = {0: 2_300_000_000, 1: 322_021_556, 2: 266_000_000, 3: 114_002_416}

LOGGED: list[str] = []
tw.log = lambda m: LOGGED.append(m)


def scenario(*, debt=None, coll=None, auth_logs=(), liq_logs=(),
             eth=76_000_000_000_000, usdc=0, code="0x", nonce=18):
    """Подменяет ответы цепи. Всё, что выше RPC, исполняется настоящее."""
    d = {**DEBT, **(debt or {})}      # ключи целочисленные ⇒ только литеральное слияние
    c = {**COLL, **(coll or {})}

    def fake_call(to, data):
        sel = data[:10]
        if to == tw.USDC and sel == tw.SEL_BALANCE_OF:
            return usdc
        for i, t in enumerate(tw.TARGETS):
            tail = tw._w(t["market"]) + tw._w(t["borrower"])
            if not data[10:].startswith(tail):
                continue
            if sel == tw.SEL_DEBT:
                return d[i]
            if sel == tw.SEL_COLL_BITMAP:
                return 1 if c[i] else 0
            if sel == tw.SEL_COLL:
                return c[i]
        raise AssertionError(f"неожиданный eth_call {sel} {data[:80]}")

    def fake_rpc(method, params):
        if method == "eth_blockNumber":
            return hex(HEAD)                     # HEAD двигает advance() в run()
        if method == "eth_getBalance":
            return hex(eth)
        if method == "eth_getTransactionCount":
            return hex(nonce)
        if method == "eth_getCode":
            return code
        if method == "eth_getLogs":
            t0 = (params[0].get("topics") or [None])[0]
            return list(auth_logs) if t0 == tw.TOPIC_SET_AUTH else list(liq_logs)
        raise AssertionError(f"неожиданный RPC {method}")

    tw.call, tw.rpc = fake_call, fake_rpc


def auth_event(borrower, authorized, *, on=True, block=51_170_000):
    """Событие SetIsAuthorized в раскладке, СВЕРЕННОЙ НА ЦЕПИ (блок 49197445):
    три индексированных поля, data = одно слово = bool."""
    p = lambda a: "0x" + a[2:].lower().rjust(64, "0")  # noqa: E731
    return {"topics": [tw.TOPIC_SET_AUTH, p(borrower), p(authorized), p(borrower)],
            "data": "0x" + ("1" if on else "0").rjust(64, "0"),
            "blockNumber": hex(block), "transactionHash": "0x" + "ab" * 32}


def sent_count():
    """Сколько ЖИВЫХ тревог легло в (замученный) инбокс. Надгробия handled не считаем."""
    if not os.path.exists(MUTED):
        return 0
    n = 0
    for ln in open(MUTED):
        try:
            if not json.loads(ln).get("handled"):
                n += 1
        except ValueError:
            pass
    return n


def run(**kw):
    """Один прогон радара. Возвращает, сколько сообщений ушло ИМЕННО за этот прогон."""
    global HEAD
    HEAD += 450                                  # ~15 минут Base между прогонами крона
    before = sent_count()
    tw._REASONS.clear()
    LOGGED.clear()
    scenario(**kw)
    tw.main()
    return sent_count() - before


def reset_state():
    for p in (os.environ["MN_WATCH_STATE"], os.environ["FLEET_DEDUP_STATE"]):
        if os.path.exists(p):
            os.remove(p)


ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("OK    " if cond else "ПРОВАЛ") + "  " + name + (f"   {extra}" if not cond else ""))


# --- 0. БАЗЛАЙН: первый прогон пишет состояние и НЕ шлёт ничего ------------------------
reset_state()
n = run()
check("базлайн молчит", n == 0, f"ушло {n}")
check("базлайн записан", any("базовая линия записана" in m for m in LOGGED))
BASE = json.load(open(os.environ["MN_WATCH_STATE"]))
check("защёлка маркера засеяна из префлайта",
      BASE["auth_seen"].get(KIT.lower()) and HUB in BASE["auth_seen"].get(
          "0xcab6b18d178502d6e18609a5f7228011cbf34f56", []),
      str(BASE.get("auth_seen")))

# --- 0а. ТИХО: второй прогон на НЕИЗМЕННОМ состоянии — тоже ноль -----------------------
n = run()
check("тишина не будит никого", n == 0, f"ушло {n}")
check("тишина записана в лог", any("поводов нет" in m for m in LOGGED))

# --- 1. ДОЛГ КИТА → 0 -----------------------------------------------------------------
n = run(debt={0: 0}, coll={0: 0})
check("долг→0: ровно одно сообщение", n == 1, f"ушло {n}")
body = json.loads(open(MUTED).readlines()[-1])["text"]
check("долг→0: причина НЕ утверждается", "погашен сам" not in body)
check("долг→0: названа развилка и велено читать логи блока",
      "ПРИЧИНА НЕ УСТАНОВЛЕНА" in body and "РЕФИНАНС" in body and "Разобрать логами" in body,
      body[:200])
check("долг→0: дубля про залог нет", "ЗАЛОГ ДВИНУЛСЯ" not in body)
# защёлка: то же состояние повторно человеку не идёт
n = run(debt={0: 0}, coll={0: 0})
check("долг→0: повтор того же состояния — НОЛЬ", n == 0, f"ушло {n}")
check("повтор погашен защёлкой (цели или состава)",
      any("уже сообщал" in m or "ЗАЩЁЛКА" in m for m in LOGGED), str(LOGGED)[:200])

# --- 2. ПАДЕНИЕ ДОЛГА ≥$5k ------------------------------------------------------------
reset_state()
run()
n = run(debt={0: DEBT[0] - 6_000_000_000})
check("падение $6k: ровно одно сообщение", n == 1, f"ушло {n}")
body = json.loads(open(MUTED).readlines()[-1])["text"]
check("падение: пересчитана достижимость огня", "ЕЩЁ ЦЕЛЬ" in body, body[:200])
check("падение: оценка помечена оценкой", "не цена выхода" in body, body[:200])
n = run(debt={0: DEBT[0] - 6_000_000_000})
check("падение: повтор того же состояния — НОЛЬ", n == 0, f"ушло {n}")
# НЕГАТИВНЫЙ КОНТРОЛЬ: подпороговое падение молчит (порог не поехал)
n = run(debt={0: DEBT[0] - 6_000_000_000 - 4_000_000})
check("подпороговое падение ($4k) молчит", n == 0, f"ушло {n}")

# --- 3. ДВИЖЕНИЕ ЗАЛОГА ---------------------------------------------------------------
reset_state()
run()
n = run(coll={0: COLL[0] - 100_000_000})           # −1 cbBTC, вывод при живом долге
check("залог двинулся: ровно одно сообщение", n == 1, f"ушло {n}")
check("залог: сказано ЗАЛОГ ДВИНУЛСЯ",
      "ЗАЛОГ ДВИНУЛСЯ" in json.loads(open(MUTED).readlines()[-1])["text"])
n = run(coll={0: COLL[0] - 100_000_000})
check("залог: повтор того же состояния — НОЛЬ", n == 0, f"ушло {n}")
# НЕГАТИВНЫЙ КОНТРОЛЬ: крошка ниже coll_move_dust не будит
n = run(coll={0: COLL[0] - 100_000_000 - 1_000})
check("крошка залога (0.00001 cbBTC) молчит", n == 0, f"ушло {n}")

# --- 4. СТОРОННЯЯ АВТОРИЗАЦИЯ (маркер увода) ------------------------------------------
reset_state()
run()
n = run(auth_logs=[auth_event(KIT, HUB)])
check("маркер увода: ровно одно сообщение", n == 1, f"ушло {n}")
body = json.loads(open(MUTED).readlines()[-1])["text"]
check("маркер: хаб назван по имени", "ХАБ-TENOR" in body, body[:200])
check("маркер: форы НЕ обещает", "ФОРЫ ЭТО НЕ ДАЁТ" in body, body[:200])
n = run(auth_logs=[auth_event(KIT, HUB)])
check("маркер: повтор того же состояния — НОЛЬ", n == 0, f"ушло {n}")
# НЕГАТИВНЫЙ КОНТРОЛЬ №1: штатный Bundler маркером не является
reset_state()
run()
n = run(auth_logs=[auth_event(KIT, "0x091183d729be9f808c212b475e387a12e67850a7")])
check("штатный Bundler НЕ будит", n == 0, f"ушло {n}")
check("штатный записан в лог", any("ШТАТНЫЙ адрес" in m for m in LOGGED))
# НЕГАТИВНЫЙ КОНТРОЛЬ №2: СНЯТИЕ авторизации маркером увода не является
reset_state()
run()
n = run(auth_logs=[auth_event(KIT, HUB, on=False)])
check("снятие авторизации НЕ будит", n == 0, f"ушло {n}")
# НЕГАТИВНЫЙ КОНТРОЛЬ №3: уже известная авторизация #4 (засеяна) не звонит
reset_state()
run()
n = run(auth_logs=[auth_event("0xcab6b18d178502d6e18609a5f7228011cbf34f56", HUB)])
check("известный маркер #4 НЕ звонит повторно", n == 0, f"ушло {n}")

# --- 5. КОНКУРЕНТ ОСНАСТИЛСЯ ----------------------------------------------------------
reset_state()
run()
n = run(eth=50_000_000_000_000_000)                # 0.05 ETH > порога 0.02
check("конкурент ETH 0.05: ровно одно сообщение", n == 1, f"ушло {n}")
check("ETH: назван самым ранним признаком",
      "ЗАПРАВЛЯЕТСЯ" in json.loads(open(MUTED).readlines()[-1])["text"])
n = run(eth=50_000_000_000_000_000)
check("конкурент ETH: повтор — НОЛЬ", n == 0, f"ушло {n}")
# USDC-ступень
reset_state()
run()
n = run(usdc=10_500_000_000)                       # $10,500 > ступени $10k
check("конкурент USDC $10.5k: ровно одно сообщение", n == 1, f"ушло {n}")
# появление кода
reset_state()
run()
n = run(code="0xef0100" + "aa" * 20)
check("конкурент обзавёлся кодом: ровно одно сообщение", n == 1, f"ушло {n}")
check("код: распознана делегация 7702",
      "EIP-7702" in json.loads(open(MUTED).readlines()[-1])["text"])
# НЕГАТИВНЫЙ КОНТРОЛЬ: голый nonce (предвестник) НЕ будит
reset_state()
run()
n = run(nonce=25)
check("рост nonce без контракта молчит", n == 0, f"ушло {n}")
check("nonce записан в лог", any("предвестник, в лог" in m for m in LOGGED))

# --- 6. НОВЫЙ ПОВОД ПРОХОДИТ СКВОЗЬ ЗАЩЁЛКУ -------------------------------------------
# Защёлка обязана глушить ТОТ ЖЕ состав, а не затыкать сторожа: это её единственное
# отличие от «мёртвого сторожа», и проверяется оно только так.
reset_state()
run()
n1 = run(eth=50_000_000_000_000_000)
n2 = run(eth=50_000_000_000_000_000)
n3 = run(eth=50_000_000_000_000_000, auth_logs=[auth_event(KIT, HUB)])
check("новый повод проходит защёлку", (n1, n2, n3) == (1, 0, 1), f"{n1},{n2},{n3}")

# --- 7. БОЕВОЙ КАНАЛ НЕ ТРОНУТ --------------------------------------------------------
check("боевой инбокс не тронут", not os.path.exists(
    os.path.expanduser("~/.fleet-watch/agent-inbox.jsonl.TESTMARK")))
check("стенд писал только во временный стейт",
      os.environ["MN_WATCH_STATE"].startswith("/tmp/"))

print("\nИТОГ:", "ВСЁ ЗЕЛЁНОЕ" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
