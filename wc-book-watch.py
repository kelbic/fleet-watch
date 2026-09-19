#!/usr/bin/env python3
"""СТОРОЖ: книга Morpho Blue на World Chain (chainId 480). Каденция — РАЗ В МЕСЯЦ.

ЗАЧЕМ. Бот wc выведен 01.09.2026 (маркер RETIRED-katana-wc-morphohl.md): за 7 недель он
взял 83 ПЫЛЕВЫЕ ликвидации на ≈$42 repaid — приза на этой книге не было. Направление
закрыто ЗНАНИЕМ о СОСТОЯНИИ книги, а не навсегда: состояние меняется. Единственное, что
обязано меряться дальше, — не появился ли настоящий приз. Мерить это ежедневно незачем
(книга такого размера двигается месяцами), поэтому каденция месячная, и это осознанный
выбор цены надзора, а не небрежность.

АДРЕСАТ — ИНБОКС АГЕНТА (hil=False). Человеку тут решать нечего, пока дело не упрётся в
капитал ([[alerts-only-where-human-acts]]). Возврат бота — отдельное решение владельца,
и вход в него готовит агент.

ЧТО СЧИТАЕТСЯ ПРИЗОМ. Ликвидировать можно только ВОЛАТИЛЬНЫЙ залог: стейбл/стейбл рынок
(sdeUSD/USDC и т.п.) не даёт скачка HF, ради которого гонка вообще существует. Поэтому
книга делится по СОСТАВУ, а не по размеру: «занято всего» на World Chain в июле уже было
$21.1M, и $13.4M из них — один стейбл-рынок. Неизвестный символ считается ВОЛАТИЛЬНЫМ:
незнание обязано закрывать гард в сторону тревоги ([[unknown-must-close-the-gate]]).

ЖИВОСТЬ ДОКАЗЫВАЕТСЯ ПОЛОЖИТЕЛЬНО ([[dead-watchdog-worse-than-none]],
[[test-that-can-only-return-zero]]). Прогон судит книгу ТОЛЬКО после трёх контролей:
  1. markets: pageInfo.countTotal == число полученных рынков (страница не обрезана);
  2. сумма занятого > 0 И оба якорных рынка (sdeUSD/USDC, WBTC/USDC) на месте —
     индексатор, отдавший пустоту, не должен читаться как «книга схлопнулась»;
  3. ликвидации: пустой месяц перепроверяется годовым окном. Ноль за месяц возможен,
     ноль за год при 83 известных ликвидациях нашего же бота — это прибор, а не тишина.
Провал любого = тревога `wc-book:instrument-dead`, и книга в этом прогоне НЕ судится.

ЗАЩЁЛКА, А НЕ ПОВТОР ([[escalation-needs-window-and-latch]]). Порог, пробитый один раз,
остаётся пробитым месяцами: без защёлки сторож звонил бы каждый месяц одним и тем же.
Тревога поднимается на ПЕРЕХОДЕ ложь->истина и на СМЕНЕ СОСТАВА (другой/выросший на
четверть заёмщик), снимается при падении ниже 0.95 порога. dedup 40 суток > каденции.

НАЗВАННЫЕ ПРЕДЕЛЫ ([[named-limit-must-be-closed]]) — они же печатаются в лог:
  * HF берётся у API (MarketPosition.healthFactor) и служит ТОЛЬКО описанием строки.
    Истина о HF — on-chain ([[liq-target-classes-canon]], docs/phantom-api.md: api_hf —
    предфильтр, а не истина). НИ ОДИН порог тревоги на HF не завязан.
  * collateralUsd у API бывает 0 при живом залоге (актив без цены). Такие строки
    помечаются `залог-без-цены`: позиция $37M sdeUSD/USDC показывает HF 0.0058 именно
    поэтому, и это артефакт оценки, а не приз на столе.
  * USD ликвидаций считается по ТЕКУЩЕЙ цене заёмного актива (repaidAssets/10^dec *
    priceUsd): у API с 09.2026 нет полей *Usd на данных ликвидации. Для стейбл-займов
    (а это почти вся книга wc) ошибка пренебрежима, для волатильных — нет.
  * Источник ликвидаций — ИНДЕКСАТОР Morpho, а не логи цепи. Публичные RPC World Chain
    не отдают eth_getLogs; Blockscout остаётся запасным путём (см. BLOCKSCOUT ниже),
    но индексатор даёт суммы, которых в топике нет.

СХЕМА API ПРОВЕРЕНА ИНТРОСПЕКЦИЕЙ 19.09.2026 и РАЗЪЕХАЛАСЬ с копиями в репо
([[live-source-vs-repo-copy]]): Market.uniqueKey -> Market.marketId; корневое поле
transactions -> marketTransactions; тип MarketLiquidation -> Liquidation;
MarketLiquidationTransactionData -> MarketTransactionLiquidationData и БЕЗ полей *Usd.
Старые скрипты wc-liquidator/analysis/*.py на живом API сегодня падают.

РЕЕСТР СУБЪЕКТОВ И ПОРОГОВ — ЗДЕСЬ ЖЕ, В ФАЙЛЕ СТОРОЖА ([[retired-subject-needs-watchdog-sweep]]).
"""
from __future__ import annotations
import json, os, sys, time, urllib.request, urllib.error

WATCH = os.path.expanduser("~/.fleet-watch")
STATE = os.environ.get("WC_BOOK_STATE") or os.path.join(WATCH, "wc-book-watch.state")
LOG = os.environ.get("WC_BOOK_LOG") or os.path.join(WATCH, "wc-book-watch.log")
JSONL = os.environ.get("WC_BOOK_JSONL") or os.path.join(WATCH, "wc-book-watch.jsonl")
API = os.environ.get("WC_BOOK_API") or "https://blue-api.morpho.org/graphql"
CHAIN_ID = 480
SOURCE = "wc-book"

# ──────────────────────────── РЕЕСТР (правится ЗДЕСЬ) ────────────────────────────
# Стейблы ЯВНЫМ СПИСКОМ: «стейбл/стейбл» — это утверждение о составе, и оно обязано быть
# проверяемым глазами. Любой символ ВНЕ списка = волатильный (в т.ч. LST/LRT ezETH,
# токенизированное золото rXAUt, неизвестное).
STABLES = {
    "USDC", "USDC.E", "USDBC", "USDT", "USDT0", "DAI", "SDAI", "USDS", "SUSDS",
    "USDE", "SUSDE", "DEUSD", "SDEUSD", "EURC", "EURE", "WARS", "SRUSD", "WSRUSD",
    "USD0", "USD0++", "PYUSD", "FDUSD", "TUSD", "LUSD", "CRVUSD", "GHO", "USDA",
    "RLUSD", "USDL", "XSGD", "FRAX", "SFRAX", "USDX", "SUSDX", "USDM", "USDY",
}
# ЯКОРЯ — ПО ПАРЕ СИМВОЛОВ, а не по marketId: id меняется при пересоздании рынка, пара нет.
# Отсутствие обоих якорей = прибор отдал чужое/пустое, а не «рынки закрылись».
ANCHOR_PAIRS = {("SDEUSD", "USDC"), ("WBTC", "USDC")}

VOL_DEBT_ALARM = 30_000_000.0    # волатильный долг всей книги
BIG_POS_ALARM = 1_000_000.0      # крупнейшая ОДНА волатильная позиция
LIQ_N_ALARM = 50                 # ликвидаций за месяц ...
LIQ_USD_ALARM = 50_000.0         # ... И сумма repaid за тот же месяц (И, не ИЛИ)
REARM = 0.95                     # гистерезис снятия защёлки
# ОДИН ГАТ НА СОСТОЯНИЕ — ЗАЩЁЛКА. Дедуп notify для тревог о книге ВЫКЛЮЧЕН (0):
# 19.09 стенд поймал, как два механизма гасят друг друга — защёлка честно перевзводилась
# после возврата значения под порог, а вторую тревогу глушил дедуп (40 сут > каденции
# 32 сут), и НОВОЕ состояние молчало. Повтор одного и того же состава блокирует защёлка
# ДО notify (доказано стендом), поэтому второй гат тут лишний и вреден
# ([[escalation-needs-window-and-latch]]). Цена решения названа: потеря state-файла
# вернёт тревогу на следующем прогоне — это верно по смыслу (порог действительно пробит)
# и видно в логе строкой «state не записан».
DEDUP_SEC = 0.0
DEDUP_FAIL_SEC = 7 * 86400       # отказ прибора: КОРОЧЕ каденции — обязан звонить каждый прогон
DUST_USD = 100.0                 # «непылевая» ликвидация — для честного знаменателя
WINDOW_DAYS = 30
POS_PER_MARKET = 5               # сколько верхних позиций тянуть из КАЖДОГО рынка
MIN_MARKET_USD = 10_000.0        # рынки мельче не опрашиваем позициями

# Замер 13.07.2026 (снимок wc-liquidator/data/wc_markets.json + разбор той же сессии).
BASE = {"date": "13.07.2026", "total": 21_100_000.0, "stable": 13_400_000.0,
        "volatile": 7_000_000.0,
        "markets": {"WBTC/USDC": 2_900_000.0, "WETH/USDC": 2_600_000.0,
                    "WLD/USDC": 1_600_000.0}}
# Запасной путь для ликвидаций, если индексатор Morpho умрёт (публичные RPC World Chain
# eth_getLogs НЕ отдают — проверено 19.09.2026 замечанием ведущей сессии):
BLOCKSCOUT = ("https://worldchain-mainnet.explorer.alchemy.com/api"
              "?module=logs&action=getLogs&address=0xE741BC7c34758b4caE05062794E8Ae24978AF432")
MORPHO_BLUE_WC = "0xE741BC7c34758b4caE05062794E8Ae24978AF432"
# ─────────────────────────────────────────────────────────────────────────────────

# РУЧКИ СТЕНДА. Пороги переопределяются ТОЛЬКО под явным маркером стенда: иначе забытая
# переменная в окружении крона молча меняет боевой порог, и сторож врёт, не падая
# ([[tests-never-touch-production-channels]]: границу держит механизм, не дисциплина).
BENCH = os.environ.get("WC_BOOK_BENCH") == "1"
_bench_notes = []
if BENCH:
    if os.environ.get("WC_BOOK_ANCHOR_EXTRA"):   # доказать, что контроль якорей ЖИВОЙ
        ANCHOR_PAIRS = set(ANCHOR_PAIRS) | {tuple(os.environ["WC_BOOK_ANCHOR_EXTRA"].split("/"))}
        _bench_notes.append("ANCHOR_EXTRA=" + os.environ["WC_BOOK_ANCHOR_EXTRA"])
    for _v, _n, _cast in (("WC_BOOK_VOL_ALARM", "VOL_DEBT_ALARM", float),
                          ("WC_BOOK_POS_ALARM", "BIG_POS_ALARM", float),
                          ("WC_BOOK_LIQ_N", "LIQ_N_ALARM", int),
                          ("WC_BOOK_LIQ_USD", "LIQ_USD_ALARM", float)):
        if os.environ.get(_v):
            globals()[_n] = _cast(os.environ[_v])
            _bench_notes.append(f"{_n}={globals()[_n]}")

UA = {"Content-Type": "application/json", "User-Agent": "fleet wc-book-watch"}


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def alarm(text, key, compo="", dedup=None):
    """КЛЮЧ ДЕДУПА НЕСЁТ СОСТАВ. Найдено стендом 19.09: защёлка честно перевзводилась, но
    вторую тревогу того же ключа глушил дедуп notify (40 суток > каденции 32 суток) —
    два механизма гасили друг друга, и НОВОЕ состояние молчало. Повтор ОДНОГО И ТОГО ЖЕ
    состояния блокирует защёлка ДО notify; дедуп остаётся страховкой на случай потерянного
    state-файла, и поэтому обязан различать составы ([[escalation-needs-window-and-latch]]).
    У отказов прибора состава нет, и ttl у них КОРОЧЕ каденции — иначе сломанный индексатор
    отзвонил бы один раз и замолчал на 40 суток."""
    import hashlib
    k = key if not compo else f"{key}#{hashlib.sha1(compo.encode()).hexdigest()[:8]}"
    sys.path.insert(0, WATCH)
    try:
        import notify
        who = notify.notify(text, source=SOURCE, hil=False, key=k,
                            dedup_sec=DEDUP_SEC if dedup is None else dedup)
    except Exception as e:                                        # noqa: BLE001
        who = f"notify-сломан:{type(e).__name__}"
    log(f"ТРЕВОГА [{k}] {text} -> {who}")
    return who


def gql(query, variables=None, tries=3):
    """Любой отказ поднимается наверх исключением: «не смог прочитать» обязано быть
    отличимо от «прочитал ноль» — иначе сломанный прибор читается как пустая книга."""
    last = None
    for att in range(tries):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        try:
            r = urllib.request.Request(API, data=body, headers=UA)
            d = json.loads(urllib.request.urlopen(r, timeout=60).read())
            if d.get("errors"):
                raise RuntimeError(json.dumps(d["errors"], ensure_ascii=False)[:300])
            return d["data"]
        except urllib.error.HTTPError as e:
            try:
                last = f"HTTP {e.code}: {e.read().decode()[:220]}"
            except Exception:                                     # noqa: BLE001
                last = f"HTTP {e.code}"
        except Exception as e:                                    # noqa: BLE001
            last = f"{type(e).__name__}: {str(e)[:220]}"
        if att < tries - 1:
            time.sleep(2.0 * (att + 1))
    raise RuntimeError(last or "неизвестный отказ")


Q_MARKETS = """query($first:Int!,$skip:Int!){
 markets(first:$first,skip:$skip,where:{chainId_in:[480]},
         orderBy:BorrowAssetsUsd,orderDirection:Desc){
  pageInfo{countTotal count}
  items{marketId lltv
    loanAsset{symbol address decimals priceUsd}
    collateralAsset{symbol address decimals priceUsd}
    state{borrowAssets borrowAssetsUsd supplyAssetsUsd collateralAssetsUsd utilization}}}}"""

Q_POS = """query($keys:[String!],$first:Int!){
 marketPositions(first:$first,orderBy:BorrowShares,orderDirection:Desc,
   where:{marketUniqueKey_in:$keys, chainId_in:[480], borrowShares_gte:1}){
   items{ healthFactor user{address}
     market{marketId collateralAsset{symbol} loanAsset{symbol}}
     state{ collateralUsd borrowAssetsUsd borrowShares } } } }"""

Q_LIQ = """query($a:Int!,$b:Int!,$first:Int!,$skip:Int!){
 marketTransactions(first:$first,skip:$skip,orderBy:Timestamp,orderDirection:Desc,
  where:{chainId_in:[480],type_in:[Liquidation],timestamp_gte:$a,timestamp_lte:$b}){
  pageInfo{countTotal count}
  items{timestamp market{marketId collateralAsset{symbol} loanAsset{symbol decimals priceUsd}}
    data{__typename ... on MarketTransactionLiquidationData{
      repaidAssets seizedAssets badDebtAssets liquidator}}}}}"""


def sym(a):
    return ((a or {}).get("symbol") or "").upper()


def classify(mk):
    """'stable' | 'volatile' | 'idle'. Idle — рынок без залогового актива: там нечего
    ликвидировать по построению, он не приз и не стейбл-шум."""
    ca, la = mk.get("collateralAsset"), mk.get("loanAsset")
    if not ca or not sym(ca):
        return "idle"
    return "stable" if (sym(ca) in STABLES and sym(la) in STABLES) else "volatile"


def fetch_markets():
    got, skip = [], 0
    total = None
    while True:
        d = gql(Q_MARKETS, {"first": 100, "skip": skip})["markets"]
        if total is None:
            total = d["pageInfo"]["countTotal"]
        got += d["items"]
        if len(d["items"]) < 100 or len(got) >= total or skip >= 900:
            break
        skip += 100
    return got, total


def fetch_liqs(a, b, depth=0):
    """Ликвидации за [a,b]. При countTotal > 9000 окно делится пополам: у API потолок
    skip=10k, и молча обрезанная выборка занизила бы месяц ([[log-line-set-is-union-over-time]])."""
    head = gql(Q_LIQ, {"a": a, "b": b, "first": 1, "skip": 0})["marketTransactions"]
    ct = head["pageInfo"]["countTotal"] or 0
    if ct > 9000 and depth < 6 and b - a > 3600:
        mid = (a + b) // 2
        return fetch_liqs(a, mid, depth + 1) + fetch_liqs(mid + 1, b, depth + 1)
    out, skip = [], 0
    while skip < min(ct, 9001):
        d = gql(Q_LIQ, {"a": a, "b": b, "first": 1000, "skip": skip})["marketTransactions"]
        out += d["items"]
        if len(d["items"]) < 1000:
            break
        skip += 1000
    return out


def liq_usd(it):
    la = ((it.get("market") or {}).get("loanAsset")) or {}
    dec = la.get("decimals")
    px = la.get("priceUsd")
    if dec is None or px is None:
        return None
    return int((it.get("data") or {}).get("repaidAssets") or 0) / 10 ** dec * px


def main():
    now = int(time.time())
    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE))
        except Exception:                                         # noqa: BLE001
            prev = {}
    if _bench_notes:
        log("РУЧКИ СТЕНДА АКТИВНЫ (WC_BOOK_BENCH=1): " + ", ".join(_bench_notes))

    # ── 1. КНИГА ─────────────────────────────────────────────────────────────────
    try:
        items, count_total = fetch_markets()
    except Exception as e:                                        # noqa: BLE001
        alarm(f"книга не читается ({API}): {str(e)[:200]}", "wc-book:instrument-dead", dedup=DEDUP_FAIL_SEC)
        return 1
    if count_total is None or len(items) != count_total:
        alarm(f"выборка рынков обрезана: получено {len(items)} из countTotal={count_total} "
              f"— судить книгу по обрезку нельзя.", "wc-book:instrument-dead", dedup=DEDUP_FAIL_SEC)
        return 1

    buckets = {"stable": 0.0, "volatile": 0.0, "idle": 0.0}
    rows, unpriced, pairs = [], [], set()
    probe_markets, vol_markets = [], set()
    for mk in items:
        st = mk.get("state") or {}
        usd = st.get("borrowAssetsUsd")
        raw = int(st.get("borrowAssets") or 0)
        kind = classify(mk)
        ca, la = sym(mk.get("collateralAsset")) or "-", sym(mk.get("loanAsset")) or "?"
        pairs.add((ca, la))
        if raw > 0 and not usd:
            # рынок с живым долгом, но без цены заёмного актива: он НЕ ноль, он НЕПРОЦЕНЕН.
            unpriced.append({"pair": f"{ca}/{la}", "marketId": mk["marketId"],
                             "borrowAssets": str(raw), "kind": kind})
        usd = float(usd or 0.0)
        buckets[kind] += usd
        rows.append({"pair": f"{ca}/{la}", "marketId": mk["marketId"], "kind": kind,
                     "borrowUsd": usd, "lltv": str(mk.get("lltv")),
                     "utilization": (st.get("utilization") or 0.0)})
        if usd >= MIN_MARKET_USD:
            probe_markets.append(mk["marketId"])
            if kind == "volatile":
                vol_markets.add(mk["marketId"])
    total = sum(buckets.values())

    missing = [f"{a}/{b}" for a, b in ANCHOR_PAIRS if (a, b) not in pairs]
    if total <= 0 or missing:
        alarm(f"КОНТРОЛЬ НЕ ПРОЙДЕН: занято всего ${total:,.0f}, якорные рынки отсутствуют: "
              f"{missing or 'нет'} ({len(items)} рынков). Индексатор отдал пустоту/чужое — "
              f"книга в этом прогоне НЕ судится.", "wc-book:instrument-dead", dedup=DEDUP_FAIL_SEC)
        return 1

    rows.sort(key=lambda r: -r["borrowUsd"])
    top5 = rows[:5]

    # ── 2. КРУПНЕЙШИЕ ПОЗИЦИИ ────────────────────────────────────────────────────
    # ПО РЫНКАМ, а не одним глобальным запросом: borrowShares НЕСОПОСТАВИМЫ между рынками
    # (разные активы и масштабы), и глобальная сортировка по ним молча теряет крупную
    # позицию в «мелкошаговом» рынке. ВНУТРИ рынка shares монотонны активам — порядок точен.
    # ПО ОДНОМУ РЫНКУ ЗА ЗАПРОС. Пакетный `marketUniqueKey_in` с first=N отдаёт top-N по
    # ГЛОБАЛЬНОМУ borrowShares всего пакета, а не top-N в каждом рынке: смоук 19.09 на
    # пакете из 10 рынков вернул одни ezETH/WETH и WBTC/WETH (у них крупные shares) и
    # ПОТЕРЯЛ кита $37M sdeUSD и все позиции WBTC/USDC. Тихо обрезанная выборка дала бы
    # ложное «крупных позиций нет» ([[test-that-can-only-return-zero]]).
    positions = []
    pos_err = ""
    try:
        for mid in probe_markets:
            d = gql(Q_POS, {"keys": [mid], "first": POS_PER_MARKET})
            positions += d["marketPositions"]["items"]
    except Exception as e:                                        # noqa: BLE001
        pos_err = str(e)[:180]
        alarm(f"позиции не читаются: {pos_err}", "wc-book:positions-dead", dedup=DEDUP_FAIL_SEC)
    plist = []
    for p in positions:
        st = p.get("state") or {}
        m = p.get("market") or {}
        debt = float(st.get("borrowAssetsUsd") or 0.0)
        if debt <= 0:
            continue
        coll = float(st.get("collateralUsd") or 0.0)
        plist.append({"user": (p.get("user") or {}).get("address", "?"),
                      "pair": f"{sym(m.get('collateralAsset')) or '-'}/{sym(m.get('loanAsset'))}",
                      "marketId": m.get("marketId", ""),
                      "debtUsd": debt, "collUsd": coll,
                      "hf_api": p.get("healthFactor"),
                      "volatile": m.get("marketId") in vol_markets,
                      "coll_unpriced": coll <= 0})
    plist.sort(key=lambda x: -x["debtUsd"])
    top_pos = plist[:5]                                  # ВСЯ книга — для census-строки
    vol_pos = [x for x in plist if x["volatile"]]
    top_vol_pos = vol_pos[:5]
    biggest = vol_pos[0] if vol_pos else None            # порог — только по ВОЛАТИЛЬНОЙ

    # ── 3. ЛИКВИДАЦИИ ЗА МЕСЯЦ ───────────────────────────────────────────────────
    a = now - WINDOW_DAYS * 86400
    try:
        liqs = fetch_liqs(a, now)
    except Exception as e:                                        # noqa: BLE001
        alarm(f"ликвидации не читаются: {str(e)[:200]}", "wc-book:instrument-dead", dedup=DEDUP_FAIL_SEC)
        return 1
    liq_n = len(liqs)
    liq_sum = 0.0
    liq_nodust = 0
    liq_unpriced = 0
    for it in liqs:
        v = liq_usd(it)
        if v is None:
            liq_unpriced += 1
            continue
        liq_sum += v
        if v >= DUST_USD:
            liq_nodust += 1
    if liq_n == 0:
        # ПОЗИТИВНЫЙ КОНТРОЛЬ пустоты: за год ликвидации ТОЧНО были (наш же бот взял 83).
        try:
            y = gql(Q_LIQ, {"a": now - 365 * 86400, "b": now, "first": 1,
                            "skip": 0})["marketTransactions"]["pageInfo"]["countTotal"] or 0
        except Exception as e:                                    # noqa: BLE001
            y = -1
            log(f"годовой контроль пустоты не отработал: {str(e)[:120]}")
        if y <= 0:
            alarm(f"КОНТРОЛЬ ПУСТОТЫ НЕ ПРОЙДЕН: 0 ликвидаций за месяц И {y} за год при "
                  f"83 известных у нашего же бота ⇒ индексатор, а не тишина.",
                  "wc-book:instrument-dead", dedup=DEDUP_FAIL_SEC)
            return 1
        log(f"ликвидаций за месяц 0, но за год {y} — пустой месяц настоящий")

    # ── 4. ЗАЩЁЛКИ И ТРЕВОГИ ─────────────────────────────────────────────────────
    prev_latch = dict(prev.get("latch") or {})      # СНИМОК: читаем отсюда, пишем в latch
    latch = dict(prev_latch)
    vol = buckets["volatile"]
    fired = []

    def hot(name, value, thr):
        """Порог с гистерезисом: взводится на thr, снимается только ниже 0.95*thr.
        Иначе значение, качающееся вокруг порога, звонило бы каждый месяц."""
        on = bool((prev_latch.get(name) or {}).get("on"))
        return value >= (thr * REARM if on else thr)

    def edge(name, is_hot, compo=""):
        """True = ТРЕВОГУ поднимать. Порог, пробитый месяц назад, молчит; СМЕНА СОСТАВА
        (другой заёмщик / заметно иной размер) — нет ([[escalation-needs-window-and-latch]])."""
        was = bool((prev_latch.get(name) or {}).get("on"))
        was_compo = (prev_latch.get(name) or {}).get("compo", "")
        latch[name] = {"on": bool(is_hot), "compo": compo if is_hot else "", "ts": now}
        return bool(is_hot) and ((not was) or compo != was_compo)

    vol_compo = "|".join(f"{r['pair']}:{r['borrowUsd']:.0f}"
                         for r in rows if r["kind"] == "volatile" and r["borrowUsd"] >= 1e6)
    if edge("vol_debt", hot("vol_debt", vol, VOL_DEBT_ALARM), vol_compo):
        fired.append(("wc-book:volatile-debt", vol_compo,
                      f"ВОЛАТИЛЬНЫЙ ДОЛГ ${vol:,.0f} ≥ порога ${VOL_DEBT_ALARM:,.0f} "
                      f"(было ${BASE['volatile']:,.0f} на {BASE['date']}). Книга всего "
                      f"${total:,.0f}, из них стейбл/стейбл ${buckets['stable']:,.0f}. "
                      f"Волатильные рынки: " + ", ".join(
                          f"{r['pair']} ${r['borrowUsd']:,.0f}" for r in rows
                          if r["kind"] == "volatile" and r["borrowUsd"] >= 1e6)))

    pos_compo = (f"{biggest['user'][:12]}|{biggest['pair']}|"
                 f"{round(biggest['debtUsd'] / 250_000)}") if biggest else ""
    if edge("big_pos", bool(biggest) and hot("big_pos", biggest["debtUsd"], BIG_POS_ALARM),
            pos_compo):
        fired.append(("wc-book:big-position", pos_compo,
                      f"КРУПНАЯ ВОЛАТИЛЬНАЯ ПОЗИЦИЯ: {biggest['user']} "
                      f"{biggest['pair']} долг ${biggest['debtUsd']:,.0f} ≥ порога "
                      f"${BIG_POS_ALARM:,.0f} (HF по API {biggest['hf_api']}"
                      + (", ЗАЛОГ БЕЗ ЦЕНЫ — HF недостоверен" if biggest["coll_unpriced"] else "")
                      + "). HF перемерить on-chain до любых выводов."))

    # И, а не ИЛИ: на World Chain счётчик штук набивается пылью (тысячи ликвидаций на
    # десятки долларов), поэтому различает здесь именно СУММА.
    liq_hot = (liq_n >= (LIQ_N_ALARM * REARM if (prev_latch.get("liq_flow") or {}).get("on")
                         else LIQ_N_ALARM)
               and hot("liq_flow", liq_sum, LIQ_USD_ALARM))
    liq_compo = f"{liq_n // 100}|{round(liq_sum / 25_000)}"
    if edge("liq_flow", liq_hot, liq_compo):
        fired.append(("wc-book:liq-flow", liq_compo,
                      f"ПОТОК ЛИКВИДАЦИЙ: {liq_n} шт за {WINDOW_DAYS} сут на "
                      f"${liq_sum:,.0f} repaid (пороги {LIQ_N_ALARM} шт И "
                      f"${LIQ_USD_ALARM:,.0f}); непылевых (≥${DUST_USD:.0f}) {liq_nodust}. "
                      f"Было 83 шт на ≈$42 за 7 недель у нашего бота."))

    for key, compo, text in fired:
        alarm(text + " Условие возврата бота wc — см. RETIRED-katana-wc-morphohl.md.",
              key, compo=compo)

    # ── 5. ЗАПИСЬ ────────────────────────────────────────────────────────────────
    rec = {"ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "chainId": CHAIN_ID, "markets_n": len(items),
           "total_borrow_usd": round(total, 2),
           "stable_borrow_usd": round(buckets["stable"], 2),
           "volatile_borrow_usd": round(buckets["volatile"], 2),
           "idle_borrow_usd": round(buckets["idle"], 2),
           "stables_list": sorted(STABLES),
           "unpriced_markets": unpriced,
           "top5_markets": top5,
           "top5_positions": top_pos,
           "top5_volatile_positions": top_vol_pos,
           "positions_error": pos_err,
           "liq_window_days": WINDOW_DAYS, "liq_n": liq_n,
           "liq_repaid_usd": round(liq_sum, 2), "liq_nodust_n": liq_nodust,
           "liq_unpriced_n": liq_unpriced,
           "liq_source": "morpho-indexer(marketTransactions)",
           "thresholds": {"vol_debt": VOL_DEBT_ALARM, "big_pos": BIG_POS_ALARM,
                          "liq_n": LIQ_N_ALARM, "liq_usd": LIQ_USD_ALARM},
           "latch": latch, "fired": [k for k, _, _ in fired], "bench": BENCH,
           "baseline": BASE}
    try:
        with open(JSONL, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"JSONL не записан: {e}")
    tmp = STATE + ".tmp"
    try:
        json.dump({"ts": now, "total": total, "stable": buckets["stable"],
                   "volatile": buckets["volatile"], "latch": latch,
                   "liq_n": liq_n, "liq_sum": liq_sum,
                   "biggest": biggest}, open(tmp, "w"), ensure_ascii=False)
        os.replace(tmp, STATE)
    except OSError as e:
        log(f"state не записан: {e}")

    d_tot = total - BASE["total"]
    d_vol = buckets["volatile"] - BASE["volatile"]
    def _pline(x):
        return (f"{x['user'][:12]}… {x['pair']} ${x['debtUsd']:,.0f} "
                f"HF-API={x['hf_api']:.4f}"
                + (" залог-без-цены" if x["coll_unpriced"] else ""))
    log(f"OK книга ${total:,.0f} ({d_tot:+,.0f} к {BASE['date']}) | "
        f"стейбл/стейбл ${buckets['stable']:,.0f} | ВОЛАТИЛЬНЫЙ ${vol:,.0f} ({d_vol:+,.0f}) | "
        f"рынков {len(items)} непроценено {len(unpriced)} | "
        + "топ-5 рынков: " + ", ".join(f"{r['pair']} ${r['borrowUsd']:,.0f}" for r in top5)
        + " | топ-5 позиций книги: " + ("; ".join(_pline(x) for x in top_pos) or "нет")
        + " | крупнейшая ВОЛАТИЛЬНАЯ: " + (_pline(biggest) if biggest else "нет")
        + f" | ликвидаций {WINDOW_DAYS}сут {liq_n} шт ${liq_sum:,.0f} repaid "
          f"(непылевых≥${DUST_USD:.0f}: {liq_nodust}, без цены {liq_unpriced}) "
          f"[индексатор Morpho, USD по ТЕКУЩЕЙ цене] | "
        + f"пороги vol≥${VOL_DEBT_ALARM:,.0f} pos≥${BIG_POS_ALARM:,.0f} "
          f"liq≥{LIQ_N_ALARM}шт И ≥${LIQ_USD_ALARM:,.0f} | "
        + ("тревог нет" if not fired else "ТРЕВОГИ: " + ",".join(k for k, _, _ in fired)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
