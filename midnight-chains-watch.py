#!/usr/bin/env python3
"""СТОРОЖ: долг Morpho Midnight на НОВЫХ цепях + поле `midnight` в реестре morpho-org/sdks.

ВЕРСИЯ 19.11.2026 (слово владельца, сессия eb54ac16). Заморозка 14.10 сохраняется для всего
прочего; этот сторож поднят ОТДЕЛЬНЫМ решением владельца: «лёгкий read-only суточный сторож
по новым сетям Midnight — Arbitrum, Tempo, Robinhood, Arc — плюс проверка появления новых
сетей с полем `midnight` в morpho-org/sdks. Только чтение (MarketCreated, totalUnits, долг),
никакого исполнения. Тревога — только если общий долг сети или отдельная позиция превысили
$25k–$50k». Ethereum в ПРЕДМЕТ сторожа не входит (вне списка владельца) — он здесь только
положительный контроль прибора (см. ниже).

ЧТО ИЗМЕРЯЕТСЯ — ПОДТВЕРЖДЁННЫЙ ДОЛГ, НЕ totalUnits (вопрос ревьюера 19.11). totalUnits у
ПРОСРОЧЕННЫХ рынков не убывает при погашении/ликвидации (замер 03.08 в monitor.py: рынок
0x168e3125 держал $106,695 при нулевом долге всех 13 позиций), так что разовый тест на $25k
держал бы ступень вечно. Поэтому:
  • один проход eth_getLogs по ядру собирает MarketCreated (id, токен займа, maturity) И
    заёмщиков — SupplyCollateral.onBehalf + UpdatePosition.user (тот же набор, что у
    analysis/midnight_positions.py, проверенного на Base);
  • у каждого рынка с totalUnits > 0 читается debt(id, user) по каждому известному
    заёмщику; книга цепи = Σ debt в $, крупнейшая позиция = max debt одного заёмщика;
  • лестница считается по ПОДТВЕРЖДЁННОМУ долгу. totalUnits остаётся сверкой: у рынка ДО
    maturity (там 03.08 totalUnits сходился с Σ debt до центов) разрыв ≥ DIVERGE_MIN_USD
    значит, что перечисление заёмщиков что-то упустило — это тревога о слепоте (hil=False,
    один раз на рынок), а не тревога о долге.

ПУСТО ≠ ИСПРАВНО ([[test-that-can-only-return-zero]]). Книги новых цепей ≈ $0, поэтому
«тихо» и «прибор сдох» неотличимы. КАЖДЫЙ прогон тем же кодом (getLogs → декод → totalUnits →
цена) читает книгу Ethereum и требует ≥ MC_CTRL_MIN_USD (умолч. $1M; замер 19.11: $121.5M
totalUnits на 2184 рынках). Прежний контроль (один рынок Base окна 25.09, кит $1.005M) к 19.11
иссяк до $898. Структурный контроль: байткод ядра каждой цепи обязан совпадать с эталоном
(sha256 7884845d7adf…, 24 557 B; 19.11 одинаков у Ethereum/Base/Arbitrum/Tempo/Arc/Robinhood).
Иной код = декодер не проверен = цепь «не читается».

ЗАЩЁЛКА ([[escalation-needs-window-and-latch]]): ступень хранится в state В ДОЛЛАРАХ
(tiers_usd), тревога — только при переходе на БОЛЕЕ ВЫСОКУЮ ступень; ключ несёт номинал.
Провал чтения цепи ступень не сбрасывает ([[metric-fell-is-not-recovered]]).

АДРЕСАТЫ (notify.py). Пересечение ступени, новая цепь в реестре, смена адреса ядра —
hil=True. Слепота прибора (контроль, цепь не читается 3 прогона подряд, токен без цены,
разрыв totalUnits/долг, реестр не читается) — hil=False: инбокс агента, а пока agent-wake
мёртв (заморозка 14.10) notify сам эскалирует человеку.

ТОЛЬКО ЧТЕНИЕ: eth_blockNumber/eth_getLogs/eth_call/eth_getCode + GET реестра. Ни ключей, ни
транзакций. Каденция — cron раз в сутки.

РЕЕСТР СУБЪЕКТОВ — ЗДЕСЬ ЖЕ (CHAINS/KNOWN_MIDNIGHT ниже), [[retired-subject-needs-watchdog-sweep]].
"""
from __future__ import annotations
import hashlib, json, os, re, sys, time, urllib.request

WATCH = os.path.expanduser("~/.fleet-watch")
# STATE/LOG переопределяемы ДО импорта: стенд уводит их во временный каталог, боевой state —
# защёлка живого сторожа ([[tests-never-touch-production-channels]]).
STATE = os.environ.get("MC_STATE") or os.path.join(WATCH, "midnight-chains.state")
LOG = os.environ.get("MC_LOG") or os.path.join(WATCH, "midnight-chains.log")

# ────────────────────────── РЕЕСТР СУБЪЕКТОВ (правится ЗДЕСЬ) ──────────────────────────
# step — проверенный 19.11 потолок окна eth_getLogs ДЛЯ ЗАПРОСА С OR ПО ТРЁМ topic0 (так
# сторож ходит): arb1/Robinhood/Tempo — 100k («query spans 500000 blocks … only 100000 are
# allowed for this request»; для одного topic0 те же узлы давали 1M — потолок зависит от
# запроса, а не от узла), Arc — 500k. publicnode (Arbitrum) на getLogs отвечает 403,
# tempo.drpc.org — 400: они лишь запасные для eth_call/eth_getCode.
# "reg" — имя цепи в реестре morpho-org/sdks (ChainId.<Name>, lower).
CHAINS = {
    "arbitrum":  {"reg": "arbitrummainnet",
                  "rpcs": ["https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"],
                  "core": "0xD2bBF921881c5BeF86D6181EaEd3da7c4661ccfE", "from": 523873663,
                  "step": 100000, "multicall": "0xcA11bde05977b3631167028862bE2a173976CA11"},
    "tempo":     {"reg": "tempomainnet",
                  "rpcs": ["https://rpc.tempo.xyz", "https://tempo.drpc.org"],
                  "core": "0x31Ff800b0FA4AA42071d4ae9D0392e84f256D83D", "from": 42413205,
                  "step": 100000, "multicall": None},
    "arc":       {"reg": "arcmainnet",
                  "rpcs": ["https://arc.gateway.tenderly.co"],
                  "core": "0x208786922BE56fDE2D1Fa60e6b9eC5D723e8d7b0", "from": 20320779,
                  "step": 500000, "multicall": None},
    "robinhood": {"reg": "robinhoodmainnet",
                  "rpcs": ["https://rpc.mainnet.chain.robinhood.com"],
                  "core": "0x6120765Ba5336150BbdDdD0Cd9108B5bFD369632", "from": 65366296,
                  "step": 100000, "multicall": None},
}
# ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ: книга Ethereum (totalUnits) тем же кодом. Не предмет тревог по долгу,
# заёмщики не перечисляются (там их тысячи, а контролю они не нужны).
CTRL_NAME = "ethereum"
CTRL = {"reg": "ethmainnet",
        "rpcs": ["https://ethereum-rpc.publicnode.com", "https://rpc.mevblocker.io",
                 "https://eth.drpc.org"],
        "core": "0x471686c42792F93528B000beF54bC10E3aa2045f", "from": 25798183,
        "step": 9000, "multicall": "0xcA11bde05977b3631167028862bE2a173976CA11"}
CTRL_MIN_USD = float(os.environ.get("MC_CTRL_MIN_USD") or 1_000_000)
# ^ ручка СТЕНДА: поднять порог и доказать, что провал контроля даёт «прибор мёртв».
REF_CODE_SHA = "7884845d7adf"      # префикс sha256 байткода ядра, замер 19.11 (все 6 цепей)
# ЛЕСТНИЦА ВЛАДЕЛЬЦА 19.11: «тревога при $25k–$50k». $25k — нижняя (раньше), $50k — верх
# его диапазона; $100k/$500k — чтобы рост дальше тоже звонил (защёлка одноразового
# пересечения однажды уже проспала рост книги до $5M, 21.09).
TIERS = (25_000, 50_000, 100_000, 500_000)
DIVERGE_MIN_USD = 5_000            # totalUnits − Σdebt у рынка ДО maturity, после которого — «слепота»
KNOWN_MIDNIGHT = {"ethmainnet", "basemainnet", "arbitrummainnet", "tempomainnet",
                  "arcmainnet", "robinhoodmainnet"}
# ^ ИМЕНА РЕЕСТРА, не наши синонимы (18.09: сравнение ethMainnet с "ethereum" дало ложную
#   «новую цепь»). Состав сверен с реестром 19.11 (релизы 12–16.11 добавили Arbitrum/Tempo).
SDK_URL = ("https://raw.githubusercontent.com/morpho-org/sdks/main/"
           "packages/morpho-ts/src/addresses.ts")
SIG_MARKET_CREATED = ("MarketCreated((uint256,address,address,"
                      "(address,uint256,uint256,address)[],uint256,uint256,address,address),bytes32)")
SIG_SUPPLY_COLLATERAL = "SupplyCollateral(address,bytes32,address,uint256,address)"  # topics[3]=onBehalf
SIG_UPDATE_POSITION = "UpdatePosition(bytes32,address,uint256,uint256,uint256)"     # topics[2]=user
STATE_VER = 2                      # 2 = mk {id: [loan, maturity]} + users; с v1 — пересканировать
FAILS_TO_ALARM = 3                 # одиночный 429 — не тревога ([[single-sample-of-source-is-not-its-state]])
# ───────────────────────────────────────────────────────────────────────────────────────

UA = {"content-type": "application/json", "user-agent": "fleet midnight-chains-watch"}


def k(text: str) -> str:
    from eth_utils import keccak
    return "0x" + keccak(text=text).hex()


def rpc(rpcs, method, params, timeout=30, tries=4):
    """Публичные узлы душат пачки 403/429 — отступаем и пробуем снова по всем узлам.

    В ошибке — ответ КАЖДОГО узла, а не последнего (19.11: «HTTP 403» запасного publicnode
    прятал настоящую причину у основного arb1 — потолок окна getLogs)."""
    errs = {}
    for attempt in range(tries):
        for u in rpcs:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            try:
                r = json.loads(urllib.request.urlopen(
                    urllib.request.Request(u, data=body, headers=UA), timeout=timeout).read())
                if "result" in r:
                    return r["result"]
                errs[u.split("//")[-1].split("/")[0]] = str(r.get("error"))[:110]
            except Exception as e:
                errs[u.split("//")[-1].split("/")[0]] = f"{type(e).__name__}: {str(e)[:70]}"
        time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"узлы отказали ({method}): {errs}")


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def alarm(text, key, hil=False):
    sys.path.insert(0, WATCH)
    try:
        import notify
        who = notify.notify(text, source="midnight-chains", hil=hil, key=key,
                            dedup_sec=7 * 86400)
    except Exception as e:
        who = f"notify-сломан:{type(e).__name__}"
    log(f"ТРЕВОГА{' HIL' if hil else ''} [{key}] {text} -> {who}")
    return who


def loan_of(v):
    return v if isinstance(v, str) else v[0]


def maturity_of(v):
    return None if isinstance(v, str) else v[1]


def market_ids(cfg, frm, to, t0, tusers=None):
    """Инкрементальный проход по ядру -> ({id: [loanToken, maturity]}, {id: {user,...}}).

    tusers=(topicSupplyCollateral, topicUpdatePosition) — заёмщики тем же запросом (OR по
    topic0). loanToken — слово 2 кортежа Market, maturity — слово 4 (декодер
    analysis/midnight_day0.decode_market_created). Без decimals токена займа сумма — мусор
    (18.09: $104.6 МЛРД вместо $144)."""
    mk, users, b = {}, {}, frm
    topics = [[t0] + list(tusers)] if tusers else [t0]
    ts, tu = (tusers or (None, None))
    while b <= to:
        e = min(b + cfg["step"] - 1, to)
        logs = rpc(cfg["rpcs"], "eth_getLogs",
                   [{"address": cfg["core"], "topics": topics, "fromBlock": hex(b), "toBlock": hex(e)}])
        for lg in logs:
            tp = [x.lower() for x in lg["topics"]]
            if len(tp) < 2:
                continue
            if tp[0] == t0:
                d = lg["data"][2:]
                t = int(d[:64], 16) * 2          # смещение до кортежа Market
                mk[tp[1]] = ["0x" + d[t + 128:t + 192][-40:], int(d[t + 256:t + 320], 16)]
            elif tp[0] == ts and len(tp) >= 4:
                users.setdefault(tp[1], set()).add("0x" + tp[3][26:])
            elif tp[0] == tu and len(tp) >= 3:
                users.setdefault(tp[1], set()).add("0x" + tp[2][26:])
        b = e + 1
        time.sleep(0.35)
    return mk, users


# ── ЦЕНА ТОКЕНА ЗАЙМА ──────────────────────────────────────────────────────────────────
# Незнание обязано быть ВИДНО, а не тихо считаться 1:1 ([[unknown-must-close-the-gate]]):
# токен без цены попадает в `unpriced`, и новый такой токен с заметным долгом — тревога.
# Цена по символу: *USD*/DAI/GHO/FRAX — $1 (депег вниз только ЗАВЫШАЕТ книгу — лишняя
# тревога, не пропуск); ETH-семья и BTC-семья — Chainlink мейннета.
STABLES = {"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",   # USDC eth
           "0xdac17f958d2ee523a2206206994597c13d831ec7",   # USDT eth
           "0xdc035d45d973e3ec169d2276ddab16f1e407384f",   # USDS eth
           "0x6b175474e89094c44da98b954eedeac495271d0f",   # DAI eth
           "0x3600000000000000000000000000000000000000",   # USDC arc (замер 19.11)
           "0x5fc5360d0400a0fd4f2af552add042d716f1d168",   # USDG robinhood (замер 19.11)
           "0xaf88d065e77c8cc2239327c5edb3a432268e5831"}   # USDC arbitrum
# ТЕСТОВЫЕ ТОКЕНЫ (19.11, сухой прогон): книга Tempo показала $90,742, из них $90,742 — рынки
# в TESTUSD (0xdc53ad29…), который правило «*USD* = $1» оценило как доллары. Живая версия
# прислала бы владельцу ложное «Tempo прошла $50k» в первый же вечер. Тестовые символы
# стоят $0, в книгу не входят, тревог не дают — только строка в логе.
TEST_RE = re.compile(r"TEST|MOCK|FAKE|DUMMY|DEMO", re.I)
# Токен БЕЗ цены тревожит, только если по количеству МОЖЕТ дотянуть до нижней ступени при
# правдоподобном потолке цены за единицу (токенизированные акции/RWA ниже $10k; BTC-семья
# оценивается фидом). 19.11: SPCX на Robinhood = 0.17 ед. — шум, не тревога.
UNPRICED_CEIL_USD = 10_000
ETH_SYMS = {"WETH", "ETH", "WSTETH", "STETH", "WEETH", "CBETH", "RETH"}
BTC_SYMS = {"WBTC", "CBBTC", "BTC", "TBTC", "LBTC", "BTC.B", "UBTC"}
FEEDS = {"ETH": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",     # Chainlink ETH/USD
         "BTC": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"}     # Chainlink BTC/USD
FEED_BOUNDS = {"ETH": (100, 100_000), "BTC": (1_000, 10_000_000)}
_FEED_PX: dict = {}
_TOKEN: dict = {}


def feed_px(kind):
    if kind not in _FEED_PX:
        try:
            r = rpc(CTRL["rpcs"], "eth_call", [{"to": FEEDS[kind], "data": "0xfeaf968c"}, "latest"])[2:]
            p = int(r[64:128], 16) / 1e8
            lo, hi = FEED_BOUNDS[kind]
            _FEED_PX[kind] = p if lo < p < hi else None
        except Exception:
            _FEED_PX[kind] = None
    return _FEED_PX[kind]


def _symbol(cfg, token):
    try:
        r = rpc(cfg["rpcs"], "eth_call", [{"to": token, "data": "0x95d89b41"}, "latest"])
        d = bytes.fromhex(r[2:])
        if len(d) >= 96:
            n = int.from_bytes(d[32:64], "big")
            return d[64:64 + n].decode(errors="replace").strip()
        return d.rstrip(b"\0").decode(errors="replace").strip()
    except Exception:
        return ""


def token_info(cfg, token):
    """-> (decimals | None, цена за единицу | None, символ). None = неизвестно (НЕ 18 и НЕ 1.0);
    0.0 = тестовый токен."""
    key = (cfg["core"], token.lower())
    if key in _TOKEN:
        return _TOKEN[key]
    try:
        dec = int(rpc(cfg["rpcs"], "eth_call", [{"to": token, "data": "0x313ce567"}, "latest"]), 16)
    except Exception:
        dec = None
    sym = _symbol(cfg, token)
    s = sym.upper()
    if TEST_RE.search(sym) and token.lower() not in STABLES:
        px = 0.0
    elif token.lower() in STABLES or "USD" in s or s in {"DAI", "GHO", "FRAX"}:
        px = 1.0
    elif s in ETH_SYMS:
        px = feed_px("ETH")
    elif s in BTC_SYMS:
        px = feed_px("BTC")
    else:
        px = None
    _TOKEN[key] = (dec, px, sym)
    return _TOKEN[key]


def total_units(cfg, ids, sel):
    """totalUnits по рынкам -> {id: raw}. Multicall3 там, где он есть; иначе поштучно.
    Провал чтения рынка ПОДНИМАЕТСЯ (раньше молча давал 0 — ложный ноль хуже отказа)."""
    per = {}
    mc = cfg.get("multicall")
    if mc and len(ids) > 20:
        CH = 400
        for i in range(0, len(ids), CH):
            batch = ids[i:i + CH]
            # aggregate((address,bytes)[]) -> (uint256, bytes[]); ревертит целиком при сбое
            head = "0x252dba42" + hex(32)[2:].rjust(64, "0") + hex(len(batch))[2:].rjust(64, "0")
            off, body = "", ""
            base = len(batch) * 32
            for mid in batch:
                cd = sel[2:] + mid[2:]
                off += hex(base + len(body) // 2)[2:].rjust(64, "0")
                body += (cfg["core"][2:].lower().rjust(64, "0") + hex(64)[2:].rjust(64, "0")
                         + hex(len(cd) // 2)[2:].rjust(64, "0") + cd.ljust(64, "0"))
            r = rpc(cfg["rpcs"], "eth_call", [{"to": mc, "data": head + off + body}, "latest"])
            d = r[2:]
            arr_off = int(d[64:128], 16) * 2
            n = int(d[arr_off:arr_off + 64], 16)
            if n != len(batch):
                raise RuntimeError(f"multicall вернул {n} ответов на {len(batch)} запросов")
            for j in range(n):
                o = arr_off + 64 + int(d[arr_off + 64 + j * 64: arr_off + 128 + j * 64], 16) * 2
                ln = int(d[o:o + 64], 16)
                v = int(d[o + 64:o + 64 + ln * 2] or "0", 16) if ln else 0
                if v:
                    per[batch[j]] = v
        return per
    for mid in ids:
        v = int(rpc(cfg["rpcs"], "eth_call", [{"to": cfg["core"], "data": sel + mid[2:]}, "latest"]), 16)
        if v:
            per[mid] = v
    return per


def read_debt(cfg, sel_debt, mid, user):
    """debt(id, user) в единицах токена займа. Провал ПОДНИМАЕТСЯ (не ноль)."""
    return int(rpc(cfg["rpcs"], "eth_call",
                   [{"to": cfg["core"], "data": sel_debt + mid[2:] + user[2:].lower().rjust(64, "0")},
                    "latest"]), 16)


def code_sha(cfg):
    code = rpc(cfg["rpcs"], "eth_getCode", [cfg["core"], "latest"])
    return hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()


def _iso(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "?"


def read_book(name, cfg, seen_state, sels, now, track_debt):
    """Одна цепь целиком -> (book, new_chain_state). Бросает при любом сбое чтения.

    track_debt=True (предмет): книга = Σ debt(id,user) по известным заёмщикам рынков с
    totalUnits > 0. track_debt=False (контроль Ethereum): книга = Σ totalUnits."""
    sha = code_sha(cfg)
    if not sha.startswith(REF_CODE_SHA):
        raise RuntimeError(f"байткод ядра {cfg['core']} не эталонный (sha {sha[:12]}) — "
                           f"декодер на этом коде не проверен")
    head = int(rpc(cfg["rpcs"], "eth_blockNumber", []), 16)
    seen_m = dict(seen_state.get("mk") or {})
    users = {m: set(u) for m, u in (seen_state.get("users") or {}).items()}
    frm = int(seen_state.get("scanned") or cfg["from"])
    if track_debt and seen_state.get("ver") != STATE_VER:
        # state до v2 не знает maturity и заёмщиков — один раз пересканировать с деплоя
        seen_m, users, frm = {}, {}, cfg["from"]
    new_m, new_u = market_ids(cfg, frm, head, sels["t0"], sels["tusers"] if track_debt else None)
    seen_m.update(new_m)
    for m, us in new_u.items():
        users.setdefault(m, set()).update(us)
    per = total_units(cfg, list(seen_m), sels["tu"])

    usd = tu_usd = 0.0
    top = (0.0, None, None, "", None)          # ($, рынок, заёмщик, символ, maturity)
    unpriced, test, diverged, borrowers = {}, {}, {}, 0
    for m, v in per.items():
        tok, mat = loan_of(seen_m[m]), maturity_of(seen_m[m])
        dec, px, sym = token_info(cfg, tok)
        if track_debt:
            debts = {u: read_debt(cfg, sels["debt"], m, u) for u in sorted(users.get(m, ()))}
            debts = {u: d for u, d in debts.items() if d}
            borrowers += len(debts)
            raw = sum(debts.values())
        else:
            debts, raw = {}, v
        if dec is None or px is None:
            if raw:
                u = unpriced.setdefault(tok, {"sym": sym or "?", "units": 0.0})
                # decimals неизвестны => количество неизвестно => None (ворота закрыты: тревога)
                u["units"] = None if (dec is None or u["units"] is None) else u["units"] + raw / 10 ** dec
            continue
        if px == 0.0:
            test[tok] = round(test.get(tok, 0.0) + v / 10 ** dec, 4)
            continue
        m_debt, m_tu = raw / 10 ** dec * px, v / 10 ** dec * px
        usd += m_debt
        tu_usd += m_tu
        if track_debt:
            for u, d in debts.items():
                top = max(top, (d / 10 ** dec * px, m, u, sym, mat), key=lambda x: x[0])
            if mat and mat > now and m_tu - m_debt >= DIVERGE_MIN_USD:
                diverged[m] = {"sym": sym, "maturity": mat, "total_units_usd": round(m_tu, 2),
                               "debt_usd": round(m_debt, 2)}
        else:
            top = max(top, (m_debt, m, None, sym, mat), key=lambda x: x[0])
    book = {"markets": len(seen_m), "new_markets": len(new_m), "usd": usd,
            "total_units_usd": tu_usd, "head": head, "nonzero": len(per), "borrowers": borrowers,
            "top_usd": top[0], "top_market": top[1], "top_borrower": top[2], "top_sym": top[3],
            "top_maturity": top[4], "unpriced": unpriced, "test_tokens": test, "diverged": diverged}
    st = {"mk": seen_m, "scanned": head + 1, "fails": 0}
    if track_debt:
        st["users"] = {m: sorted(u) for m, u in users.items()}
        st["ver"] = STATE_VER
    return book, st


def tier_level(usd: float) -> int:
    """Номинал достигнутой ступени в $; 0 = ниже нижней."""
    lvl = 0
    for t in TIERS:
        if usd >= t:
            lvl = t
    return lvl


def registry_chains():
    """Поле `midnight` реестра morpho-org/sdks -> ({имя_цепи: адрес_ядра}, размер файла)."""
    txt = urllib.request.urlopen(urllib.request.Request(
        SDK_URL, headers={"user-agent": "fleet midnight-chains-watch"}), timeout=35).read().decode()
    out, cur = {}, None
    for line in txt.splitlines():
        m = re.search(r'\[\s*ChainId\.(\w+)\s*\]\s*:', line)
        if m:
            cur = m.group(1).lower()
        a = re.search(r'\bmidnight\s*:\s*["\'](0x[0-9a-fA-F]{40})', line)
        if cur and a and cur not in out:      # первое вхождение — блок адресов, не блок блоков
            out[cur] = a.group(1).lower()
    return out, len(txt)


def save_state(obj):
    """Атомарно: open(p,'w') усекает файл до записи — падение посреди dump стёрло бы защёлки."""
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, STATE)


def main():
    sels = {"tu": "0x" + k("totalUnits(bytes32)")[2:10],
            "debt": "0x" + k("debt(bytes32,address)")[2:10],
            "t0": k(SIG_MARKET_CREATED),
            "tusers": (k(SIG_SUPPLY_COLLATERAL), k(SIG_UPDATE_POSITION))}
    now = int(time.time())
    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE))
        except Exception:
            prev = {}
    st_chains = dict(prev.get("chains") or {})
    rc = 0

    def fail(name, e):
        fails = int((st_chains.get(name) or {}).get("fails") or 0) + 1
        st_chains.setdefault(name, {})["fails"] = fails
        log(f"{name}: НЕ ПРОЧИТАН ({str(e)[:140]}) — подряд {fails}")
        if fails >= FAILS_TO_ALARM:
            alarm(f"Midnight-сторож: {name} не читается {fails} прогонов подряд ({str(e)[:110]}). "
                  f"Нули по этой цепи ничего не значат, пока узел не отвечает.",
                  f"midnight-chains:{name}-dead")
        return {"error": str(e)[:140], "fails": fails}

    # ── ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ ПРИБОРА: книга Ethereum тем же кодом ──
    try:
        cb, st_chains[CTRL_NAME] = read_book(CTRL_NAME, CTRL, st_chains.get(CTRL_NAME) or {},
                                             sels, now, track_debt=False)
        if cb["usd"] < CTRL_MIN_USD:
            rc = 1
            alarm(f"Midnight-сторож: ПРИБОР МЁРТВ — контрольная книга Ethereum = ${cb['usd']:,.0f} "
                  f"(ожидалось ≥ ${CTRL_MIN_USD:,.0f}; 19.11 было $121.5M). Нули по "
                  f"Arbitrum/Tempo/Arc/Robinhood в этом прогоне НИЧЕГО не значат.",
                  "midnight-chains:instrument-dead")
        else:
            log(f"контроль прибора: книга Ethereum (totalUnits) ${cb['usd']:,.0f} на {cb['markets']} "
                f"рынках (+{cb['new_markets']} новых) — прибор жив")
        books = {CTRL_NAME: cb}
    except Exception as e:
        books = {CTRL_NAME: fail(CTRL_NAME, e)}
        rc = 1

    # ── предмет: подтверждённый долг новых цепей ──
    for name, cfg in CHAINS.items():
        try:
            b, st_chains[name] = read_book(name, cfg, st_chains.get(name) or {}, sels, now,
                                           track_debt=True)
            books[name] = b
            log(f"{name}: рынков {b['markets']} (+{b['new_markets']} новых), ненулевых {b['nonzero']}, "
                f"ДОЛГ ${b['usd']:,.2f} у {b['borrowers']} позиций (totalUnits ${b['total_units_usd']:,.2f}), "
                f"крупнейшая позиция ${b['top_usd']:,.2f}"
                + (f" [{b['top_sym']}, срок {_iso(b['top_maturity'])}]" if b["top_market"] else "")
                + (f", БЕЗ ЦЕНЫ {b['unpriced']}" if b["unpriced"] else "")
                + (f", тестовые токены (=$0, по totalUnits) {b['test_tokens']}" if b["test_tokens"] else "")
                + (f", РАЗРЫВ totalUnits/долг {b['diverged']}" if b["diverged"] else "")
                + f", блок {b['head']}")
        except Exception as e:
            books[name] = fail(name, e)

    # ── слепые пятна: токен без цены и разрыв totalUnits/долг (один раз на объект) ──
    unp_prev = {n: set(v) for n, v in (prev.get("unpriced_alerted") or {}).items()}
    div_prev = {n: set(v) for n, v in (prev.get("diverged_alerted") or {}).items()}
    unp_now, div_now = dict(unp_prev), dict(div_prev)
    for name in CHAINS:
        b = books.get(name) or {}
        if "usd" not in b:
            continue
        can_matter = {t for t, u in b["unpriced"].items()
                      if u["units"] is None or u["units"] * UNPRICED_CEIL_USD >= TIERS[0]}
        fresh = can_matter - unp_prev.get(name, set())
        if fresh:
            alarm(f"Midnight-сторож: на {name} долг в токене займа БЕЗ ЦЕНЫ: "
                  + ", ".join(f"{t} ({b['unpriced'][t]['sym']}, "
                              f"{'кол-во неизвестно' if b['unpriced'][t]['units'] is None else format(b['unpriced'][t]['units'], ',.2f') + ' ед.'})"
                              for t in sorted(fresh))
                  + f". Этот долг НЕ входит в ${b['usd']:,.0f} — книга занижена. "
                    f"Добавить цену в STABLES/ETH_SYMS/BTC_SYMS сторожа.",
                  f"midnight-chains:{name}-unpriced-{'-'.join(sorted(fresh))[:90]}")
        unp_now[name] = unp_prev.get(name, set()) | can_matter
        dfresh = set(b["diverged"]) - div_prev.get(name, set())
        if dfresh:
            alarm(f"Midnight-сторож: на {name} у рынков ДО срока totalUnits больше подтверждённого "
                  f"долга заёмщиков: " + "; ".join(
                      f"{m[:12]}… {b['diverged'][m]['sym']} срок {_iso(b['diverged'][m]['maturity'])}: "
                      f"totalUnits ${b['diverged'][m]['total_units_usd']:,.0f} vs долг "
                      f"${b['diverged'][m]['debt_usd']:,.0f}" for m in sorted(dfresh))
                  + ". Перечисление заёмщиков что-то упустило — долг может быть больше, чем видит сторож.",
                  f"midnight-chains:{name}-diverged-{'-'.join(m[2:10] for m in sorted(dfresh))[:90]}")
        div_now[name] = div_prev.get(name, set()) | dfresh

    # ── реестр: новые цепи и смена адресов ядра ──
    try:
        reg, size = registry_chains()
        log(f"реестр morpho-org/sdks: midnight у {len(reg)} цепей {sorted(reg)} (файл {size} B)")
        new = set(reg) - KNOWN_MIDNIGHT
        if new and set(prev.get("reg_new") or []) != new:
            alarm("Midnight: НОВАЯ ЦЕПЬ в реестре morpho-org/sdks: "
                  + ", ".join(f"{c} ({reg[c]})" for c in sorted(new))
                  + ". Сторож её пока НЕ читает — добавить в CHAINS (RPC, блок деплоя).",
                  "midnight-chains:new-chain-" + "-".join(sorted(new)), hil=True)
        prev["reg_new"] = sorted(new)
        watched = {cfg["reg"]: cfg["core"].lower() for cfg in list(CHAINS.values()) + [CTRL]}
        moved = {c: reg[c] for c, a in watched.items() if c in reg and reg[c] != a}
        if moved:
            alarm("Midnight: реестр сменил АДРЕС ЯДРА у " + ", ".join(
                  f"{c}: {watched[c]} → {a}" for c, a in sorted(moved.items()))
                  + ". Сторож читает старое ядро — книга по этой цепи больше не настоящая.",
                  "midnight-chains:moved-" + "-".join(f"{c}{a[2:10]}" for c, a in sorted(moved.items())),
                  hil=True)
        gone = set(watched) - set(reg)
        if gone:
            alarm(f"Midnight-сторож: реестр больше не содержит {sorted(gone)} (файл {size} B) — "
                  f"парсер или источник сломан, либо Morpho убрала цепь.",
                  "midnight-chains:registry-gone-" + "-".join(sorted(gone)))
    except Exception as e:
        rfails = int(prev.get("reg_fails") or 0) + 1
        prev["reg_fails"] = rfails
        log(f"реестр morpho-org/sdks НЕ прочитан ({str(e)[:120]}) — подряд {rfails}")
        if rfails >= FAILS_TO_ALARM:
            alarm(f"Midnight-сторож: реестр morpho-org/sdks не читается {rfails} прогонов подряд: "
                  f"{str(e)[:120]}", "midnight-chains:registry-dead")
    else:
        prev["reg_fails"] = 0

    # ── лестница по ПОДТВЕРЖДЁННОМУ долгу: только предмет, только рост ступени ──
    tiers_prev = {n: int(v) for n, v in (prev.get("tiers_usd") or {}).items()}
    if not tiers_prev:   # миграция с версии до 19.11 (ступени-индексы): берём из книги в state
        for nm, ob in (prev.get("books") or {}).items():
            if isinstance(ob, dict) and isinstance(ob.get("usd"), (int, float)):
                tiers_prev[nm] = tier_level(ob["usd"])
    tiers_now = dict(tiers_prev)
    for name in CHAINS:
        b = books.get(name) or {}
        if "usd" not in b:
            continue           # провал чтения ступень НЕ сбрасывает
        lvl, was = tier_level(b["usd"]), int(tiers_prev.get(name, 0))
        tiers_now[name] = lvl
        if lvl > was:
            alarm(f"💰 Midnight/{name}: подтверждённый долг ${b['usd']:,.0f} (Σ debt по {b['borrowers']} "
                  f"позициям) — пройдена ступень ${lvl:,} "
                  f"(прежняя {'$%s' % format(was, ',') if was else 'ниже $25,000'}). "
                  f"Крупнейшая позиция ${b['top_usd']:,.0f} в {b['top_sym'] or '?'}, срок "
                  f"{_iso(b['top_maturity'])}, заёмщик {b['top_borrower']}, рынок {b['top_market']}. "
                  f"totalUnits книги ${b['total_units_usd']:,.0f}. Сторож только читает.",
                  f"midnight-chains:{name}-tier-{lvl}", hil=True)

    save_state({"ts": now, "books": books, "chains": st_chains,
                "reg_new": prev.get("reg_new"), "reg_fails": prev.get("reg_fails", 0),
                "tiers_usd": tiers_now,
                "unpriced_alerted": {n: sorted(v) for n, v in unp_now.items()},
                "diverged_alerted": {n: sorted(v) for n, v in div_now.items()}})
    log(f"прогон завершён rc={rc}: " + ", ".join(
        f"{n} ${b['usd']:,.0f}" if "usd" in b else f"{n} ОШИБКА" for n, b in books.items()))
    return rc


if __name__ == "__main__":
    sys.exit(main())
