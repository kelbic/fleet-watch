#!/usr/bin/env python3
"""СТОРОЖ (б): книга Morpho Midnight ВНЕ Base + поле `midnight` в реестре morpho-org/sdks.

ЗАЧЕМ. Разведка 18.09 (edge-research/docs/TIMER-CLASS-RECON-2026-09-18.md) нашла ЧЕТЫРЕ
деплоя Midnight (Ethereum, Base, Arc, Robinhood) там, где снимок от 05.07 говорил «ни на
одном чейне». Реестр после 12.07 никто не перечитывал два месяца — это ровно тот дефект,
про который память пишет «прежде чем хоронить направление, оставить дешёвый сторож на
границе» ([[hyperevm-direction-closed]]). Книга вне Base на 18.09 = $102.75 точным
прибором. Порог пробуждения $5 000 — на два порядка ниже самой мелкой цели, которую имело
бы смысл брать, и на четыре порядка выше нынешнего шума.

АДРЕСАТ — ИНБОКС АГЕНТА (hil=False).

ПУСТО ≠ ИСПРАВНО ([[test-that-can-only-return-zero]]). Книга вне Base ~$0, поэтому
«тихо» и «прибор сдох» на диске неотличимы. КАЖДЫЙ прогон гонит ТОТ ЖЕ декодер
totalUnits по БОЕВОМУ рынку Base окна 25.09 и требует > $100 000 (там $1.0M+ у одного
кита). Не сошлось — тревога «прибор мёртв», а не молчание ([[dead-watchdog-worse-than-none]]).

ЗАЩЁЛКА ([[escalation-needs-window-and-latch]]): при суточной каденции тревога поднимается
на СМЕНЕ СОСТАВА (книга пересекла порог; в реестре появилась ЦЕПЬ, которой не было),
состав — в state, dedup 7 суток.

РЕЕСТР СУБЪЕКТОВ — ЗДЕСЬ ЖЕ (CHAINS/KNOWN_MIDNIGHT ниже), [[retired-subject-needs-watchdog-sweep]].
"""
from __future__ import annotations
import json, os, re, sys, time, urllib.request

WATCH = os.path.expanduser("~/.fleet-watch")
STATE = os.path.join(WATCH, "midnight-chains.state")
LOG = os.path.join(WATCH, "midnight-chains.log")

# ────────────────────────── РЕЕСТР СУБЪЕКТОВ (правится ЗДЕСЬ) ──────────────────────────
CHAINS = {
    "ethereum": {"rpcs": ["https://rpc.mevblocker.io", "https://eth.drpc.org",
                          "https://ethereum-rpc.publicnode.com"],
                 "core": "0x471686c42792F93528B000beF54bC10E3aa2045f", "from": 25798183,
                 "step": 9000, "multicall": "0xcA11bde05977b3631167028862bE2a173976CA11"},
    "arc":      {"rpcs": ["https://arc.gateway.tenderly.co"],
                 "core": "0x208786922BE56fDE2D1Fa60e6b9eC5D723e8d7b0", "from": 20320779,
                 "step": 40000, "multicall": None},
    "robinhood": {"rpcs": ["https://rpc.mainnet.chain.robinhood.com"],
                  "core": "0x6120765Ba5336150BbdDdD0Cd9108B5bFD369632", "from": 65366296,
                  "step": 40000, "multicall": None},
}
# ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ: боевой рынок Base окна 25.09 (кит $1.005M, см. target-watch.py)
CTRL = {"rpcs": ["https://mainnet.base.org", "https://base-rpc.publicnode.com"],
        "core": "0xAdedD8ab6dE832766Fedf0FaC4992E5C4D3EA18A",
        "market": "0x549cd072daf99328554f3a6d2d4d6f4a07f1c59369e891e6391946f9cf75f221",
        "min_units": int(os.environ.get("MC_CTRL_MIN") or 100_000 * 10**6)}  # заём USDC (6 dec);
        # MC_CTRL_MIN — ручка СТЕНДА: поднять порог и доказать, что провал контроля
        # даёт «прибор мёртв», а не молчаливый ноль по цепям.
WAKE_UNITS = 5_000                              # порог пробуждения, $ (задание владельца)
# ЛЕСТНИЦА СТУПЕНЕЙ (21.09, крон-триаж тревоги ethereum-alive). Нижняя ступень — порог
# владельца, НЕ трогать. Верхние добавлены, потому что прежняя защёлка
# `usd >= WAKE_UNITS and was < WAKE_UNITS` стреляла ТОЛЬКО на первом пересечении: после
# записи в state books.ethereum.usd=5225 рост книги хоть до $5M давал was=5225 => условие
# ложно => МОЛЧАНИЕ НАВСЕГДА. Растяжка на $5k выжигала специфичность сторожа ровно по тому
# событию, ради которого он ставился ([[retired-subject-needs-watchdog-sweep]],
# [[test-that-can-only-return-zero]]). Ключ тревоги несёт НОМИНАЛ ступени — иначе dedup 7
# суток съел бы следующую ступень как повтор.
# $500k — «самая мелкая цель, которую имело бы смысл брать» из докстроки этого файла
# (растяжка стоит на два порядка ниже неё); $50k — промежуточный рост на порядок.
WAKE_TIERS = (WAKE_UNITS, 50_000, 500_000)
KNOWN_MIDNIGHT = {"ethmainnet", "basemainnet", "arcmainnet", "robinhoodmainnet"}
# ^ ИМЕНА РЕЕСТРА, не наши синонимы: первый прогон 18.09 поднял ложную «новая цепь»,
#   потому что сравнивал ChainId.ethMainnet с нашим "ethereum" ([[red-bench-is-not-a-diagnosis]]).
SDK_URL = ("https://raw.githubusercontent.com/morpho-org/sdks/main/"
           "packages/morpho-ts/src/addresses.ts")
# topic0 MarketCreated — сверен с analysis/midnight_day0.py (offline-keccak, две реализации)
SIG_MARKET_CREATED = ("MarketCreated((uint256,address,address,"
                      "(address,uint256,uint256,address)[],uint256,uint256,address,address),bytes32)")
SEL_TOTAL_UNITS = "0x"  # заполняется в main() из keccak
# ───────────────────────────────────────────────────────────────────────────────────────

UA = {"content-type": "application/json", "user-agent": "fleet midnight-chains-watch"}


def k(text: str) -> str:
    from eth_utils import keccak
    return "0x" + keccak(text=text).hex()


def rpc(rpcs, method, params, timeout=30, tries=4):
    """Публичные узлы душат первый (полный) прогон 403/429. Отступаем и пробуем снова:
    иначе первичный посев состояния невозможен, а без него КАЖДЫЙ суточный прогон
    пересканирует всю историю и снова упирается в лимит."""
    last = None
    for attempt in range(tries):
        for u in rpcs:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            try:
                r = json.loads(urllib.request.urlopen(
                    urllib.request.Request(u, data=body, headers=UA), timeout=timeout).read())
                if "result" in r:
                    return r["result"]
                last = r.get("error")
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:70]}"
        time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"узлы отказали: {last}")


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
        who = notify.notify(text, source="midnight-chains", hil=False, key=key, dedup_sec=7 * 86400)
    except Exception as e:
        who = f"notify-сломан:{type(e).__name__}"
    log(f"ТРЕВОГА [{key}] {text} -> {who}")
    return who


def market_ids(cfg, frm, to, t0):
    """Инкрементальный перебор MarketCreated -> {id: loanToken}.

    loanToken берём ЗДЕСЬ ЖЕ: totalUnits номинирован в токене займа, и без его decimals
    сумма — мусор (первый прогон 18.09: $104.6 МЛРД вместо $144, потому что рынки
    WETH/USDS с 18 dec делились на 1e6)."""
    out, b = {}, frm
    while b <= to:
        e = min(b + cfg["step"] - 1, to)
        logs = rpc(cfg["rpcs"], "eth_getLogs",
                   [{"address": cfg["core"], "topics": [t0], "fromBlock": hex(b), "toBlock": hex(e)}])
        for lg in logs:
            if len(lg["topics"]) < 2:
                continue
            d = lg["data"][2:]
            t = int(d[:64], 16) * 2          # смещение до кортежа Market
            out[lg["topics"][1]] = "0x" + d[t + 128:t + 192][-40:]
        b = e + 1
        time.sleep(0.35)          # темп: публичные узлы режут пачки getLogs
    return out


DEC_CACHE = {}


def decimals(cfg, token):
    key = (cfg["core"], token)
    if key in DEC_CACHE:
        return DEC_CACHE[key]
    try:
        v = int(rpc(cfg["rpcs"], "eth_call", [{"to": token, "data": "0x313ce567"}, "latest"]), 16)
    except Exception:
        v = 18                                # безопасно вниз: завысить книгу хуже, чем занизить
    DEC_CACHE[key] = v
    return v


def total_units(cfg, ids, sel):
    """Сумма totalUnits по рынкам. Multicall3 там, где он есть; иначе поштучно."""
    tot, per = 0, {}
    mc = cfg.get("multicall")
    if mc and len(ids) > 20:
        CH = 400
        for i in range(0, len(ids), CH):
            batch = ids[i:i + CH]
            # aggregate((address,bytes)[]) -> (uint256, bytes[])
            head = "0x252dba42" + hex(32)[2:].rjust(64, "0") + hex(len(batch))[2:].rjust(64, "0")
            off, body = "", ""
            base = len(batch) * 32
            for j, mid in enumerate(batch):
                cd = sel[2:] + mid[2:]
                off += hex(base + len(body) // 2)[2:].rjust(64, "0")
                body += (cfg["core"][2:].lower().rjust(64, "0") + hex(64)[2:].rjust(64, "0")
                         + hex(len(cd) // 2)[2:].rjust(64, "0") + cd.ljust(64, "0"))
            r = rpc(cfg["rpcs"], "eth_call", [{"to": mc, "data": head + off + body}, "latest"])
            d = r[2:]
            arr_off = int(d[64:128], 16) * 2
            n = int(d[arr_off:arr_off + 64], 16)
            for j in range(n):
                o = arr_off + 64 + int(d[arr_off + 64 + j * 64: arr_off + 128 + j * 64], 16) * 2
                ln = int(d[o:o + 64], 16)
                v = int(d[o + 64:o + 64 + ln * 2] or "0", 16) if ln else 0
                tot += v
                if v:
                    per[batch[j]] = v
        return tot, per
    for mid in ids:
        try:
            v = int(rpc(cfg["rpcs"], "eth_call",
                        [{"to": cfg["core"], "data": sel + mid[2:]}, "latest"]), 16)
        except Exception:
            v = 0
        tot += v
        if v:
            per[mid] = v
    return tot, per


def tier_of(usd: float) -> int:
    """Индекс достигнутой ступени лестницы; -1 = книга ниже растяжки."""
    t = -1
    for i, lvl in enumerate(WAKE_TIERS):
        if usd >= lvl:
            t = i
    return t


def tier_verdict(t: int) -> str:
    """ВЕРДИКТ, А НЕ РЕШЕНИЕ. Прежний текст растяжки нёс «Перенос бота — 1–2 дня» на ЛЮБОЙ
    сумме: 21.09 он приехал на книге $5,225, где вся Ethereum-книга — одна позиция $5,038
    USDC, и звал на 1–2 дня работы под приз с долга в $5k. Решение, протащенное в растяжку;
    на нижних ступенях текст теперь говорит ПРЯМО, что переносить нечего."""
    if t >= len(WAKE_TIERS) - 1:
        return ("Сопоставимо с минимальной целью, которую имело бы смысл брать — ОЦЕНИТЬ "
                "перенос (1–2 дня, то же ядро и тот же topic0), начав с размера крупнейшей "
                "ОДНОЙ позиции, а не суммы книги.")
    if t <= 0:
        return (f"Это РАСТЯЖКА: на два порядка ниже минимальной осмысленной цели "
                f"(~${WAKE_TIERS[-1]:,}). НЕ основание для переноса бота — только отметка, "
                f"что цепь перестала быть пустой.")
    return (f"Рост на порядок, но всё ещё ниже минимальной осмысленной цели "
            f"(~${WAKE_TIERS[-1]:,}). Перенос НЕ обоснован; следующая ступень — "
            f"${WAKE_TIERS[-1]:,}.")


def registry_chains():
    """Поле `midnight` в каноническом реестре morpho-org/sdks."""
    txt = urllib.request.urlopen(urllib.request.Request(
        SDK_URL, headers={"user-agent": "fleet midnight-chains-watch"}), timeout=35).read().decode()
    out, cur = set(), None
    for line in txt.splitlines():
        m = re.search(r'\[\s*ChainId\.(\w+)\s*\]\s*:', line)
        if m:
            cur = m.group(1).lower()
        if cur and re.search(r'\bmidnight\s*:\s*["\']0x[0-9a-fA-F]{40}', line):
            out.add(cur)
    return out, len(txt)


def main():
    sel = "0x" + k("totalUnits(bytes32)")[2:10]
    t0 = k(SIG_MARKET_CREATED)
    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE))
        except Exception:
            prev = {}

    # ── ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ ПРИБОРА (тот же декодер, боевой рынок Base) ──
    try:
        ctrl = int(rpc(CTRL["rpcs"], "eth_call",
                       [{"to": CTRL["core"], "data": sel + CTRL["market"][2:]}, "latest"]), 16)
    except Exception as e:
        alarm(f"контроль прибора не отработал: {str(e)[:130]}", "midnight-chains:ctrl-dead")
        return 1
    if ctrl < CTRL["min_units"]:
        alarm(f"ПРИБОР МЁРТВ: totalUnits боевого рынка Base 25.09 = {ctrl / 1e6:,.2f} "
              f"(ожидалось > ${CTRL['min_units'] / 1e6:,.0f}). Нули по Ethereum/Arc/Robinhood "
              f"в этом прогоне НИЧЕГО не значат.", "midnight-chains:instrument-dead")
        return 1
    log(f"контроль прибора: Base 25.09 totalUnits = ${ctrl / 1e6:,.2f} — прибор жив")

    # ── книга вне Base ──
    books, state_chains = {}, dict(prev.get("chains") or {})
    for name, cfg in CHAINS.items():
        try:
            head = int(rpc(cfg["rpcs"], "eth_blockNumber", []), 16)
            seen = state_chains.get(name, {})
            seen_m = dict(seen.get("mk") or {})
            frm = int(seen.get("scanned") or cfg["from"])
            seen_m.update(market_ids(cfg, frm, head, t0))
            ids = list(seen_m)
            tot_raw, per = total_units(cfg, ids, sel)
            # НОРМИРОВКА ПО ТОКЕНУ ЗАЙМА: без неё сумма бессмысленна (см. market_ids)
            usd = sum(v / 10 ** decimals(cfg, seen_m[m]) for m, v in per.items())
            books[name] = {"markets": len(ids), "usd": usd, "head": head, "nonzero": len(per)}
            state_chains[name] = {"mk": seen_m, "scanned": head + 1, "fails": 0}
            log(f"{name}: рынков {len(ids)}, книга ${usd:,.2f} (нормировано по decimals "
                f"токена займа), ненулевых {len(per)}, блок {head}")
        except Exception as e:
            # 429/сетевой сбой одного прогона — НЕ тревога: суточная каденция превратила бы
            # её в ежедневный звон. Тревога после трёх подряд (== трёх суток слепоты).
            fails = int((state_chains.get(name) or {}).get("fails") or 0) + 1
            state_chains.setdefault(name, {})["fails"] = fails
            books[name] = {"error": str(e)[:110], "fails": fails}
            log(f"{name}: НЕ ПРОЧИТАН ({str(e)[:110]}) — подряд {fails}")
            if fails >= 3:
                alarm(f"{name}: не читается {fails} прогонов подряд ({str(e)[:100]}). "
                      f"Нули по этой цепи ничего не значат, пока узел не отвечает.",
                      f"midnight-chains:{name}-dead")

    # ── реестр: новые цепи с полем midnight ──
    try:
        reg, size = registry_chains()
        new = reg - KNOWN_MIDNIGHT
        log(f"реестр morpho-org/sdks: midnight у {len(reg)} цепей {sorted(reg)} (файл {size} B)")
        if new and set(prev.get("reg_new") or []) != new:
            alarm(f"НОВАЯ ЦЕПЬ MIDNIGHT В РЕЕСТРЕ: {sorted(new)}. Это тот сторож, который "
                  f"мы проспали с 05.07 по 18.09 (Ethereum/Arc/Robinhood появились молча). "
                  f"Перечитать календарь окон и книгу.", "midnight-chains:new-chain")
        prev["reg_new"] = sorted(new)
        if len(reg) < len(KNOWN_MIDNIGHT):
            alarm(f"реестр отдал МЕНЬШЕ цепей ({sorted(reg)}), чем известно "
                  f"({sorted(KNOWN_MIDNIGHT)}) — парсер или источник сломан.",
                  "midnight-chains:registry-parse")
    except Exception as e:
        alarm(f"реестр morpho-org/sdks не прочитан: {str(e)[:120]}", "midnight-chains:registry-dead")

    # ── защёлка по книге: ЛЕСТНИЦА, а не одно пересечение ──
    tiers_prev = dict(prev.get("tiers") or {})
    if not tiers_prev:                      # первый прогон после апгрейда: ступень берём из
        for nm, ob in (prev.get("books") or {}).items():   # уже записанной книги, чтобы не
            if isinstance(ob, dict) and "usd" in ob:       # выстрелить повтором по ступени,
                tiers_prev[nm] = tier_of(ob["usd"])        # которая уже отзвонила.
    tiers_now = dict(tiers_prev)
    for name, b in books.items():
        if "usd" not in b:
            # Чтение цепи упало — ступень НЕ сбрасываем. Иначе неудачный прогон обнулял бы
            # `was` до 0 и следующий успешный выдал бы ложное «КНИГА ОЖИЛА» на той же
            # книге ([[metric-fell-is-not-recovered]]).
            continue
        usd = b["usd"]
        t_now, t_was = tier_of(usd), int(tiers_prev.get(name, -1))
        tiers_now[name] = t_now
        if t_now > t_was:
            lvl = WAKE_TIERS[t_now]
            alarm(f"КНИГА ОЖИЛА: Midnight/{name} = ${usd:,.2f} на {b['markets']} рынках "
                  f"(ступень ${lvl:,}, прежняя ступень "
                  f"{('$%s' % format(WAKE_TIERS[t_was], ',')) if t_was >= 0 else 'ниже растяжки'}). "
                  f"{tier_verdict(t_now)}", f"midnight-chains:{name}-alive-{lvl}")

    json.dump({"ts": int(time.time()), "ctrl_units": ctrl, "books": books,
               "chains": state_chains, "reg_new": prev.get("reg_new"),
               "tiers": tiers_now}, open(STATE, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
