#!/usr/bin/env python3
"""Стенд midnight-chains-watch, версия 19.11 (новые цепи Arbitrum/Tempo/Arc/Robinhood).

Что держит стенд (каждое свойство — свой негативный контроль):
  1) миграция state с версии до 19.11 (ступени-индексы, mk без maturity) — тишина;
  2) лестница $25k/$50k по ПОДТВЕРЖДЁННОМУ долгу Σ debt(id,user): пересечение — РОВНО одна
     тревога hil=True с номиналом в ключе и заёмщиком/сроком в тексте; повтор — тишина;
  3) ВОПРОС РЕВЬЮЕРА 19.11: просроченный рынок с totalUnits $30k и нулевым долгом (разовый
     тест погашен) — НИ ступени, ни разрыва; рынок ДО срока с тем же разрывом — одна тревога
     о слепоте (hil=False), не о долге;
  4) провал чтения цепи НЕ сбрасывает ступень, «не читается» — только на 3-м провале подряд;
  5) контроль прибора: книга Ethereum (totalUnits) ниже порога => «ПРИБОР МЁРТВ», rc=1;
  6) байткод ядра не эталонный => цепь не читается, ложного нуля нет;
  7) реестр: новая цепь (hil, одна тревога на состав), смена адреса ядра (hil), пропажа;
  8) токен без цены: тревога один раз, в долг как $1 НЕ входит; мелочь (0.17 ед.) — тихо;
     ТЕСТОВЫЙ токен (TESTUSD, $90k на Tempo 19.11) — $0, без тревог;
  9) провал чтения одного рынка/долга поднимается, а не превращается в 0;
 10) ТРАНСПОРТ: настоящий alarm() -> notify при FLEET_ALERT_MUTE=1 не делает НИ ОДНОГО
     запроса к api.telegram.org и пишет инбокс только во временный файл.

СЕТЬ ЗАКРЫТА ЦЕЛИКОМ: urllib.request.urlopen подменён ДО импорта сторожа и считает попытки.
Шов подменён на границе сети (rpc/market_ids/registry_chains); долг, лестница, цена токена,
контроль, тексты и запись state исполняются настоящие ([[seam-stubbed-above-the-defect]]).
STATE/LOG/инбокс/дедуп уведены во временный каталог ДО импорта.
"""
import hashlib, importlib.util, json, os, sys, tempfile, time, urllib.request

TMP = tempfile.mkdtemp(prefix="mc-stand-")
os.environ["FLEET_ALERT_MUTE"] = "1"
os.environ["MC_STATE"] = os.path.join(TMP, "midnight-chains.state")
os.environ["MC_LOG"] = os.path.join(TMP, "midnight-chains.log")
os.environ["FLEET_INBOX"] = os.path.join(TMP, "agent-inbox.jsonl")
os.environ["FLEET_DEDUP_STATE"] = os.path.join(TMP, "notify-dedup.json")

NET = []          # каждая попытка выйти в сеть


def _no_net(req, *a, **kw):
    NET.append(getattr(req, "full_url", str(req)))
    raise AssertionError("сеть в стенде: " + NET[-1])


urllib.request.urlopen = _no_net

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("MC_SCRIPT") or os.path.join(HERE, "midnight-chains-watch.py")
_spec = importlib.util.spec_from_file_location("mc_stand", SCRIPT)
mc = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mc)
assert mc.STATE.startswith(TMP) and mc.LOG.startswith(TMP), "state стенда не уведён"

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
ODD = "0x" + "77" * 20                     # токен без цены (символ XYZ)
TESTTOK = "0x" + "dc" * 20                 # тестовый токен (символ TESTUSD, как на Tempo 19.11)
USER = "0x" + "bb" * 20
CODE = "0x" + "60" * 64
mc.REF_CODE_SHA = hashlib.sha256(bytes.fromhex(CODE[2:])).hexdigest()[:12]
SEL_DEBT = "0x" + mc.k("debt(bytes32,address)")[2:10]
FUTURE = int(time.time()) + 30 * 86400
PAST = int(time.time()) - 30 * 86400

BOOK = {}         # имя цепи -> {id: {"tok", "tu" ($), "debt" ($), "mat"}}
DEAD = set()      # цепи, чьё чтение падает
BADCODE = set()   # цепи с чужим байткодом
FIRED = []


def _name(cfg):
    if cfg is mc.CTRL:
        return mc.CTRL_NAME
    return [n for n, c in mc.CHAINS.items() if c is cfg][0]


def _name_by_rpcs(rpcs):
    if rpcs == mc.CTRL["rpcs"]:
        return mc.CTRL_NAME
    return [n for n, c in mc.CHAINS.items() if c["rpcs"] == rpcs][0]


def _abi_str(s):
    b = s.encode()
    return "0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex() + b.ljust(32, b"\0").hex()


def fake_rpc(rpcs, method, params, timeout=30, tries=4):
    name = _name_by_rpcs(rpcs)
    if name in DEAD:
        raise RuntimeError("узлы отказали: стенд")
    if method == "eth_getCode":
        return "0x" + "fe" * 64 if name in BADCODE else CODE
    if method == "eth_blockNumber":
        return hex(1_000_000)
    if method == "eth_call":
        to, data = params[0]["to"].lower(), params[0]["data"]
        if data == "0x313ce567":
            return hex(6)
        if data == "0x95d89b41":
            return _abi_str({ODD: "XYZ", TESTTOK: "TESTUSD"}.get(to, "USDC"))
        if data.startswith(SEL_DEBT):
            m = "0x" + data[10:74]
            user = "0x" + data[74:][-40:]
            mk = BOOK.get(name, {}).get(m)
            return hex(int(round(mk["debt"] * 1e6)) if (mk and user == USER) else 0)
        m = "0x" + data[10:]
        mk = BOOK.get(name, {}).get(m)
        return hex(int(round(mk["tu"] * 1e6)) if mk else 0)
    raise AssertionError("неожиданный метод: " + method)


def fake_market_ids(cfg, frm, to, t0, tusers=None):
    bk = BOOK.get(_name(cfg), {})
    mk = {m: [v["tok"], v["mat"]] for m, v in bk.items()}
    users = {m: {USER} for m in bk} if tusers else {}
    return mk, users


mc.rpc = fake_rpc
mc.market_ids = fake_market_ids
mc.alarm = lambda text, key, hil=False: (FIRED.append((key, hil, text)), "стенд")[1]
REG = {}
mc.registry_chains = lambda: (dict(REG), 100_000)


def reset_registry():
    REG.clear()
    for cfg in list(mc.CHAINS.values()) + [mc.CTRL]:
        REG[cfg["reg"]] = cfg["core"].lower()
    REG["basemainnet"] = "0xadedd8ab6de832766fedf0fac4992e5c4d3ea18a"


def mid(n):
    return "0x" + f"{n:064x}"


def set_book(name, usd, token=USDC, n=1, debt=None, mat=FUTURE):
    d = usd if debt is None else debt
    BOOK[name] = {mid(1000 * len(name) + i): {"tok": token, "tu": usd / n, "debt": d / n, "mat": mat}
                  for i in range(n)}


def run(seed=None, dead=(), badcode=()):
    DEAD.clear(); DEAD.update(dead)
    BADCODE.clear(); BADCODE.update(badcode)
    if seed is not None:
        json.dump(seed, open(mc.STATE, "w"))
    mc._TOKEN.clear()
    FIRED.clear()
    rc = mc.main()
    return rc, list(FIRED), json.load(open(mc.STATE))


def fresh():
    if os.path.exists(mc.STATE):
        os.remove(mc.STATE)
    BOOK.clear()
    reset_registry()
    set_book("ethereum", 50_000_000, n=3)
    for n in mc.CHAINS:
        set_book(n, 0)


def tier_keys(fired):
    return [(k, h) for k, h, _ in fired if "-tier-" in k]


CASES = []


def case(f):
    CASES.append(f)
    return f


@case
def migration_quiet():
    fresh()
    old = {"ts": 1, "tiers": {"ethereum": 1, "arc": -1, "robinhood": -1},
           "books": {"ethereum": {"usd": 128731.0}, "arc": {"usd": 2.98}, "robinhood": {"usd": 35.3}},
           "chains": {"arc": {"mk": {mid(5): USDC}, "scanned": 999_000, "fails": 0}}, "reg_new": []}
    set_book("arc", 2.98); set_book("robinhood", 35.3)
    rc, fired, st = run(seed=old)
    assert rc == 0, rc
    assert fired == [], fired
    assert st["tiers_usd"].get("arc") == 0 and st["tiers_usd"].get("robinhood") == 0, st["tiers_usd"]
    assert st["chains"]["arc"]["ver"] == mc.STATE_VER and mid(5) not in st["chains"]["arc"]["mk"], \
        "state v1 не пересканирован с деплоя"


@case
def ladder_once_per_tier():
    fresh()
    run()
    set_book("arbitrum", 30_000, n=3)
    rc, fired, st = run()
    assert tier_keys(fired) == [("midnight-chains:arbitrum-tier-25000", True)], fired
    txt = fired[0][2]
    assert "$30,000" in txt and "$10,000" in txt and USER in txt and "срок" in txt, txt
    rc, fired, _ = run()
    assert tier_keys(fired) == [], fired                                         # та же книга — тишина
    set_book("arbitrum", 60_000)
    rc, fired, _ = run()
    assert tier_keys(fired) == [("midnight-chains:arbitrum-tier-50000", True)], fired
    set_book("tempo", 24_999)
    rc, fired, _ = run()
    assert tier_keys(fired) == [], fired                                         # ниже $25k — тишина


@case
def repaid_after_maturity_is_quiet():
    """Вопрос ревьюера: totalUnits просроченного рынка застрял на $30k, долг погашен."""
    fresh()
    set_book("robinhood", 30_000, debt=0, mat=PAST)
    rc, fired, st = run()
    assert fired == [], fired
    b = st["books"]["robinhood"]
    assert b["usd"] == 0 and b["total_units_usd"] == 30_000 and b["diverged"] == {}, b


@case
def unexplained_units_before_maturity_alarm_once():
    fresh()
    set_book("robinhood", 30_000, debt=0, mat=FUTURE)
    rc, fired, st = run()
    assert tier_keys(fired) == [], fired                                         # не тревога о долге
    keys = [(k, h) for k, h, _ in fired]
    assert len(keys) == 1 and keys[0][0].startswith("midnight-chains:robinhood-diverged-") \
        and keys[0][1] is False, fired
    rc, fired, _ = run()
    assert fired == [], fired


@case
def failure_keeps_tier_and_alarms_on_third():
    fresh()
    set_book("robinhood", 30_000)
    run()
    for i in (1, 2):
        rc, fired, st = run(dead=("robinhood",))
        assert fired == [], (i, fired)
        assert st["tiers_usd"]["robinhood"] == 25_000, st["tiers_usd"]
    rc, fired, st = run(dead=("robinhood",))
    assert [(k, h) for k, h, _ in fired] == [("midnight-chains:robinhood-dead", False)], fired
    rc, fired, _ = run()
    assert fired == [], fired                         # ожил на прежней книге — не «ожило»
    assert json.load(open(mc.STATE))["chains"]["robinhood"]["fails"] == 0


@case
def control_dead():
    fresh()
    set_book("ethereum", 900)
    rc, fired, _ = run()
    assert rc == 1, rc
    assert [(k, h) for k, h, _ in fired] == [("midnight-chains:instrument-dead", False)], fired


@case
def control_unreadable_is_not_silent_ok():
    fresh()
    rc, fired, st = run(dead=("ethereum",))
    assert rc == 1 and fired == [] and st["chains"]["ethereum"]["fails"] == 1, (rc, fired)


@case
def foreign_bytecode_is_a_failure_not_a_zero():
    fresh()
    set_book("arc", 80_000)
    rc, fired, st = run(badcode=("arc",))
    assert tier_keys(fired) == [], fired
    assert "error" in st["books"]["arc"] and "эталон" in st["books"]["arc"]["error"], st["books"]["arc"]


@case
def registry_new_moved_gone():
    fresh()
    REG["inkmainnet"] = "0x" + "11" * 20
    rc, fired, _ = run()
    assert [(k, h) for k, h, _ in fired] == [("midnight-chains:new-chain-inkmainnet", True)], fired
    rc, fired, _ = run()
    assert fired == [], fired                         # тот же состав — не повтор
    REG["tempomainnet"] = "0x" + "22" * 20
    rc, fired, _ = run()
    assert [(k, h) for k, h, _ in fired] == [("midnight-chains:moved-tempomainnet22222222", True)], fired
    reset_registry(); del REG["arcmainnet"]
    rc, fired, _ = run()
    assert [k for k, _, _ in fired] == ["midnight-chains:registry-gone-arcmainnet"], fired


@case
def unpriced_once_and_excluded():
    fresh()
    set_book("tempo", 70_000, token=ODD)
    rc, fired, st = run()
    keys = [k for k, _, _ in fired]
    assert len(keys) == 1 and keys[0].startswith("midnight-chains:tempo-unpriced-"), fired
    assert "XYZ" in fired[0][2]
    assert st["books"]["tempo"]["usd"] == 0, st["books"]["tempo"]   # НЕ $70k по 1:1
    rc, fired, _ = run()
    assert fired == [], fired


@case
def unpriced_dust_is_quiet():
    fresh()
    set_book("robinhood", 0.17, token=ODD)
    rc, fired, st = run()
    assert fired == [], fired
    assert ODD in st["books"]["robinhood"]["unpriced"], st["books"]["robinhood"]


@case
def test_token_is_zero_and_quiet():
    fresh()
    BOOK["tempo"] = {mid(1): {"tok": TESTTOK, "tu": 90_742.0, "debt": 90_742.0, "mat": FUTURE},
                     mid(2): {"tok": USDC, "tu": 0.10, "debt": 0.10, "mat": FUTURE}}
    rc, fired, st = run()
    assert fired == [], fired                                    # ни ступени, ни «без цены»
    b = st["books"]["tempo"]
    assert abs(b["usd"] - 0.10) < 1e-6, b
    assert b["test_tokens"] == {TESTTOK: 90742.0}, b
    BOOK["tempo"][mid(3)] = {"tok": USDC, "tu": 30_000.0, "debt": 30_000.0, "mat": FUTURE}
    rc, fired, st = run()
    assert tier_keys(fired) == [("midnight-chains:tempo-tier-25000", True)], fired
    assert "в USDC" in fired[0][2], fired[0][2]                  # символ крупнейшей позиции в тексте


@case
def market_and_debt_read_failure_raises():
    def flaky(rpcs, method, params, timeout=30, tries=4):
        if params[0]["data"].endswith("0002") or params[0]["data"].endswith("bb" * 20):
            raise RuntimeError("узлы отказали: стенд")
        return hex(5)
    saved = mc.rpc
    mc.rpc = flaky
    try:
        for f, args in ((mc.total_units, (mc.CHAINS["tempo"], [mid(1), mid(2), mid(3)], "0x12345678")),
                        (mc.read_debt, (mc.CHAINS["tempo"], SEL_DEBT, mid(1), USER))):
            try:
                f(*args)
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"провал чтения в {f.__name__} превратился в ноль")
    finally:
        mc.rpc = saved


@case
def transport_closed():
    real = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(real)
    before = len(NET)
    who = real.alarm("стенд: проверка транспорта", "midnight-chains:stand-transport", hil=True)
    tg = [u for u in NET[before:] if "telegram" in u]
    assert tg == [], tg
    assert who == "muted", who
    assert os.path.exists(os.environ["FLEET_INBOX"] + ".muted"), "инбокс-дублёр не записан"


if __name__ == "__main__":
    bad = 0
    for f in CASES:
        try:
            f()
            print(f"ok   {f.__name__}")
        except Exception as e:
            bad += 1
            print(f"FAIL {f.__name__}: {type(e).__name__}: {e}")
    tg = [u for u in NET if "telegram" in u]
    print(f"попыток в сеть: {len(NET)}, из них к telegram: {len(tg)}")
    if tg:
        bad += 1
    print("ИТОГ:", "ЗЕЛЁНЫЙ" if not bad else f"КРАСНЫЙ ({bad})")
    sys.exit(1 if bad else 0)
