#!/usr/bin/env python3
"""СТОРОЖ (а): книга Term Finance, окно 11.12.2026 15:00:00Z.

ЗАЧЕМ. Гейт по этому окну замерен 18.09.2026 (edge-research/docs/TERM-GATE-2026-09-18.md):
выход доказан исполнением на форке ($166 818 валом), порог стройки — «долг двоих ≥ $1M
к 20.11». Само окно — в 84 сутках, и ОБА прошлых окна нашего Midnight закрылись не
ликвидацией, а заёмщиком (28.08 самопогашение, 27.08 ролл кита за 9.85 ч до срока).
Поэтому единственное, что здесь надо мерить ежедневно, — жива ли книга.

АДРЕСАТ — ИНБОКС АГЕНТА (hil=False): человеку решать нечего, пока дело не упрётся в
капитал ([[alerts-only-where-human-acts]]).

ЖИВОСТЬ ДОКАЗЫВАЕТСЯ ПОЛОЖИТЕЛЬНО ([[dead-watchdog-worse-than-none]]).
Каждый прогон обязан сойтись на ТОЖДЕСТВЕ ПРОТОКОЛА:
    totalOutstandingRepurchaseExposure == obligation(B1) + obligation(B2)
Это не «проверка на ноль»: агрегат и слагаемые читаются РАЗНЫМИ вызовами разных
контрактов, и совпадение до последней единицы доказывает, что узел отдал согласованное
состояние, а не кэш/пустоту ([[test-that-can-only-return-zero]]). Расхождение вверх =
появился ТРЕТИЙ заёмщик (это новость, а не поломка) и тоже едет в инбокс.

ЗАЩЁЛКА, А НЕ ПОВТОР ([[escalation-needs-window-and-latch]]). Суточная каденция при
dedup 1 ч звонила бы КАЖДЫЙ день одним и тем же. Тревога поднимается только на СМЕНЕ
СОСТАВА состояния (новый минимум долга, пересечение порога, новый заёмщик), состав
хранится в state-файле, dedup 7 суток.

РЕЕСТР СУБЪЕКТОВ — ЗДЕСЬ ЖЕ, В ФАЙЛЕ СТОРОЖА (см. SUBJECTS ниже). Урок
[[retired-subject-needs-watchdog-sweep]]: маркер гасит субъект, а реестр живёт в самом
маркере — иначе вывод субъекта не глушит сторожа, а молча убивает его специфичность.
"""
from __future__ import annotations
import json, os, sys, time, urllib.request

WATCH = os.path.expanduser("~/.fleet-watch")
STATE = os.path.join(WATCH, "term-watch.state")
LOG = os.path.join(WATCH, "term-watch.log")
DOC = "/home/claude-agent/edge-research/docs/TERM-GATE-2026-09-18.md"

RPCS = ["https://rpc.mevblocker.io", "https://eth.drpc.org", "https://ethereum-rpc.publicnode.com"]
if os.environ.get("TERM_WATCH_FORCE_RPC") == "1":
    RPCS = ["https://127.0.0.1:1/dead"]   # ручка СТЕНДА: доказать, что мёртвый узел даёт ТРЕВОГУ, а не тишину

# ────────────────────────── РЕЕСТР СУБЪЕКТОВ (правится ЗДЕСЬ) ──────────────────────────
WINDOW_TS = 1797001200          # endOfRepurchaseWindow, 11.12.2026 15:00:00Z
SERVICER = "0xe53c30a308e6d7ad5c44e1dd6fb1f3ea99dfd410"
CM = "0x58c7b1a91e3be97616cf79400667eed5d3ec3c09"
ROLLOVER_MGR = "0xe9bcbb1fe69e9c89b0595912c91ea007787c9ccb"
PT = "0xecfafdc7741323a945a163ed068b5a3c43483957"   # PT-reUSD-10DEC2026, 6 dec
REUSD = "0x5086bf358635B81D8C47C66d1C8b9E567Db70c72"  # Re Protocol, 18 dec (НЕ Resupply)
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
SUBJECTS = {                       # заёмщик -> исходный долг USDC на 18.09.2026
    "0x3976747b82316020a15662761c82860b9785e7f3": 1_656_760.451130,
    "0xd5efcd1bcb9336f4e02598e10554c696dae4ae2b": 1_157_950.241698,
}
if os.environ.get("TERM_WATCH_BASE_ADD"):        # ручка СТЕНДА: доказать, что ветка
    _a = float(os.environ["TERM_WATCH_BASE_ADD"])  # «долг упал» реально поднимает тревогу
    SUBJECTS = {b: v + _a for b, v in SUBJECTS.items()}
BASE_TOTAL = 2_814_710.692828
DROP_ALARM = 100_000.0     # падение долга одного заёмщика на столько = окно тает
FLOOR_TOTAL = 1_000_000.0  # порог решения из TERM-GATE: ниже — окно снимается
EXIT_REUSD = 2_708_166     # сколько reUSD даёт полный сейз (замер на форке 18.09)
EXIT_MIN_RATE = 1.09       # порог префлайта T−14д: USDC за reUSD
PREFLIGHT_DAYS = 14
# ───────────────────────────────────────────────────────────────────────────────────────

SEL_OBLIG = "0x2762697d"    # getBorrowerRepurchaseObligation(address)
SEL_TOTAL = "0x9d5d2108"    # totalOutstandingRepurchaseExposure()
SEL_ROLL = "0x34e6c771"     # getRolloverInstructions(address) -> (bidLocker, amount, hash, processed)
UA = {"content-type": "application/json", "user-agent": "fleet term-watch"}


def rpc(method, params, timeout=25):
    last = None
    for u in RPCS:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        try:
            r = json.loads(urllib.request.urlopen(
                urllib.request.Request(u, data=body, headers=UA), timeout=timeout).read())
            if "result" in r:
                return r["result"]
            last = r.get("error")
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:70]}"
    raise RuntimeError(f"все узлы отказали: {last}")


def call_u(to, data):
    return int(rpc("eth_call", [{"to": to, "data": data}, "latest"]), 16)


def call_words(to, data, n):
    """Сырые слова ответа. Короткий/пустой ответ = прибор, а не пустое состояние."""
    r = rpc("eth_call", [{"to": to, "data": data}, "latest"]).replace("0x", "")
    if len(r) < n * 64:
        raise RuntimeError(f"ответ короче {n} слов ({len(r)//2} B) — геттер не тот или узел врёт")
    return [int(r[i * 64:(i + 1) * 64], 16) for i in range(n)]


def ea(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def alarm(text, key):
    sys.path.insert(0, WATCH)
    try:
        import notify
        who = notify.notify(text, source="term-watch", hil=False, key=key, dedup_sec=7 * 86400)
    except Exception as e:
        who = f"notify-сломан:{type(e).__name__}"
    log(f"ТРЕВОГА [{key}] {text} -> {who}")
    return who


def exit_quote(amount_reusd):
    """Квотер выхода reUSD -> USDC (Kyber, бесключевой). Только для префлайта T−14д."""
    u = ("https://aggregator-api.kyberswap.com/ethereum/api/v1/routes"
         f"?tokenIn={REUSD}&tokenOut={USDC}&amountIn={amount_reusd * 10**18}")
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        u, headers={**UA, "x-client-id": "fleet-term-watch"}), timeout=35).read())
    out = int(((d.get("data") or {}).get("routeSummary") or {}).get("amountOut") or 0)
    return out / 1e6 / amount_reusd if out else 0.0


def main():
    now = int(time.time())
    left = (WINDOW_TS - now) / 86400.0
    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE))
        except Exception:
            prev = {}

    # ---- чтение по цепи. Любой обрыв = ТРЕВОГА, а не тишина.
    try:
        obligations = {b: call_u(SERVICER, SEL_OBLIG + ea(b)) / 1e6 for b in SUBJECTS}
        total = call_u(SERVICER, SEL_TOTAL) / 1e6
        head = int(rpc("eth_blockNumber", []), 16)
    except Exception as e:
        alarm(f"прибор не читается: {str(e)[:150]}", "term-watch:instrument-dead")
        return 1

    # ---- ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ: тождество агрегата и слагаемых
    s = sum(obligations.values())
    consistent = abs(total - s) < 1e-5
    if total <= 0:
        state = dict(prev, ts=now, total=0.0, obligations=obligations)
        json.dump(state, open(STATE, "w"))
        if not prev.get("closed"):
            state["closed"] = True
            json.dump(state, open(STATE, "w"))
            alarm(f"КНИГА ЗАКРЫТА: totalOutstandingRepurchaseExposure = 0 "
                  f"(было ${BASE_TOTAL:,.0f}). Окно 11.12 больше не приз — снять с рассмотрения "
                  f"и выключить сторож.", "term-watch:book-closed")
        log(f"книга 0 — субъект исчерпан (осталось {left:.1f} сут)")
        return 0
    if not consistent:
        alarm(f"РАСХОЖДЕНИЕ: total=${total:,.2f}, сумма двух известных=${s:,.2f}, "
              f"разница ${total - s:+,.2f} ⇒ у Term появился ТРЕТИЙ заёмщик в этом репо "
              f"(книга выросла) либо прибор врёт. Проверить руками.",
              "term-watch:third-borrower")

    # ---- ЗАЩЁЛКА: тревожим на СМЕНЕ СОСТАВА, а не каждые сутки
    fired = []
    for b, base in SUBJECTS.items():
        cur = obligations[b]
        drop = base - cur
        low = prev.get("low", {}).get(b, base)
        if drop >= DROP_ALARM and cur < low - 1.0:
            fired.append(f"{b[:10]}… долг ${base:,.0f} -> ${cur:,.0f} (−${drop:,.0f})")
    if fired and consistent:
        alarm("ДОЛГ ТАЕТ (ранний признак погашения/ролла): " + "; ".join(fired) +
              f". Всего ${total:,.0f}, до окна {left:.1f} сут. Порог решения ${FLOOR_TOTAL:,.0f}.",
              "term-watch:debt-drop")
    if total < FLOOR_TOTAL and not prev.get("below_floor"):
        alarm(f"ПОРОГ ПРОБИТ: книга ${total:,.0f} < ${FLOOR_TOTAL:,.0f}. По вердикту "
              f"TERM-GATE-2026-09-18 окно 11.12 снимается с рассмотрения.",
              "term-watch:below-floor")

    # ---- РОЛЛ: СОСТОЯНИЕ, А НЕ ИСТОРИЯ.
    # Кита Midnight 27.08 увели РОЛЛОМ за 9.85 ч до срока, и увидеть это по падению
    # долга нельзя: election — намерение, долг гасится только в момент maturity.
    # Читаем намерение прямо ([[log-line-set-is-union-over-time]]: набор событий за
    # время ≠ снимок состояния). Реверт/короткий ответ = ПРИБОР, а не «ролла нет»:
    # контроль 18.09 — тот же вызов с 0xdEaD отдаёт нули, значит нули тут настоящие.
    rolls = {}
    try:
        for b in SUBJECTS:
            w = call_words(ROLLOVER_MGR, SEL_ROLL + ea(b), 4)
            rolls[b] = {"bidLocker": hex(w[0]), "amount": w[1] / 1e6, "processed": bool(w[3])}
    except Exception as e:
        alarm(f"ролл-геттер не читается: {str(e)[:130]}", "term-watch:rollover-dead")
    for b, r in rolls.items():
        was = (prev.get("rolls") or {}).get(b, {}).get("amount", 0.0)
        if r["amount"] > 0 and was <= 0:
            alarm(f"РОЛЛ ЗАЯВЛЕН: {b[:10]}… роллирует ${r['amount']:,.0f} "
                  f"(bidLocker {r['bidLocker'][:12]}…, processed={r['processed']}). "
                  f"Ровно так у нас увели кита Midnight 27.08 за 9.85 ч до срока: долг "
                  f"до самого maturity выглядит целым. До окна {left:.1f} сут.",
                  "term-watch:rollover-elected")
        elif r["amount"] <= 0 < was:
            alarm(f"РОЛЛ ОТОЗВАН: {b[:10]}… (было ${was:,.0f}) — цель вернулась.",
                  "term-watch:rollover-cancelled")

    # ---- ПРЕФЛАЙТ T−14д: выход мерян 18.09, окно 11.12 — число обязано быть перемерено
    exit_rate = prev.get("exit_rate")
    if 0 < left <= PREFLIGHT_DAYS:
        try:
            exit_rate = exit_quote(EXIT_REUSD)
            log(f"префлайт выхода: {EXIT_REUSD:,} reUSD -> {exit_rate:.6f} USDC/reUSD "
                f"(порог {EXIT_MIN_RATE})")
            if exit_rate < EXIT_MIN_RATE and not prev.get("exit_alarmed"):
                alarm(f"ВЫХОД ПРОСЕЛ: {EXIT_REUSD:,} reUSD котируются {exit_rate:.4f} USDC/reUSD "
                      f"против {EXIT_MIN_RATE} порога (18.09 было 1.1009, исполнено на форке). "
                      f"Ликвидность Fluid reUSD/USDT ушла — приз съеден.", "term-watch:exit-thin")
                prev["exit_alarmed"] = True
        except Exception as e:
            alarm(f"префлайт выхода не отработал: {str(e)[:120]}", "term-watch:exit-quote-dead")

    json.dump({"ts": now, "head": head, "total": total, "obligations": obligations,
               "rolls": rolls,
               "low": {b: min(obligations[b], prev.get("low", {}).get(b, obligations[b]))
                       for b in SUBJECTS},
               "below_floor": total < FLOOR_TOTAL, "closed": False,
               "exit_rate": exit_rate, "exit_alarmed": prev.get("exit_alarmed", False),
               "consistent": consistent}, open(STATE, "w"))
    log(f"OK блок {head} книга ${total:,.2f} "
        + " ".join(f"{b[:8]}=${v:,.0f}" for b, v in obligations.items())
        + f" ролл={'нет' if all(r['amount'] <= 0 for r in rolls.values()) else 'ЕСТЬ'}"
        + f" тождество={'сошлось' if consistent else 'РАСХОЖДЕНИЕ'} до окна {left:.1f} сут")
    return 0


if __name__ == "__main__":
    sys.exit(main())
