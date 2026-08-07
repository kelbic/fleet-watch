#!/usr/bin/env python3
"""Радар окна 31.07 (Morpho Midnight, Base): цель + оснащение конкурента + чужие укусы.

Зачем отдельно от бота: до maturity бот цель НЕ ВИДИТ вовсе (позиция здорова, здоровье 59%
ниже порога hot 90%), поэтому все досрочные исходы — погашение, укус конкурента, появление
нового игрока — проходят мимо него. Радар смотрит туда, куда бот не смотрит.

МОДЕЛЬ УГРОЗЫ (замеры 27.07). Конкурент 0x6cf59693 — тот же оператор, что выиграл
same-block гонку у wc на World Chain. На WC его почерк: EOA -> СВОЙ КОНТРАКТ
(0xe741bc7c…, селектор 0xd8eabcb8), шесть tx в одном блоке (nonce 52596-52601). На Base
у него сегодня: код 0x (контракта НЕТ), USDC $6.10, ETH $0.85, nonce 580.
Отсюда三 сигнала оснащения, в порядке появления:
  1) ETH-пополнение  — нужен и для repay-из-кармана, и для деплоя; самый ранний;
  2) исходящая tx / ДЕПЛОЙ КОНТРАКТА — на Base флешлоун (Morpho/Aave/Balancer/Uniswap)
     даёт $100k без капитала, поэтому баланс USDC угрозу НЕ ловит: ловит только контракт.
     Адрес будущего контракта детерминирован (CREATE = keccak(rlp([addr, nonce]))[12:]),
     поэтому проверяем код по нему, не дожидаясь события Liquidate;
  3) USDC-фондирование — ступенчато, см. пороги ниже.

ЧУВСТВИТЕЛЬНОСТЬ К ЧАСТИЧНОМУ УКУСУ. RCF на post-maturity ОТКЛЮЧЁН (Midnight.sol: "In the
post-maturity mode ... the RCF is deactivated") ⇒ close factor 100% и любой частичный укус
разрешён с первой секунды. Наш net ≈ (1−f)·(1.22·t − 260) при укусе доли f, поэтому пол
$3,000 достигается на t=(3000/(1−f)+260)/1.22: f=0.10 -> +49мин, f=0.27 -> +59.7мин,
**f≥0.28 -> НИКОГДА** (упирается в потолок рампы 3600с) — бот молча не выстрелит. Поэтому
при падении долга радар СРАЗУ считает, достижим ли ещё пол, и говорит это в алерте.

Только чтение. Ничего не пишет, кроме своего state. Алертит на СОБЫТИЕ, не по расписанию.

cron (каждые 15 минут):
  */15 * * * * flock -n /tmp/mn-target-watch.lock /home/claude-agent/.fleet-watch/target-watch.py \
    >> /home/claude-agent/.fleet-watch/target-watch.log 2>&1
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/home/claude-agent/midnight-liquidator")
sys.path.insert(0, os.path.expanduser("~/.fleet-watch"))
from notify import notify  # noqa: E402
from analysis.keccak import keccak256  # noqa: E402  (pure-stdlib, без зависимостей)

MIDNIGHT = "0xAdedD8ab6dE832766Fedf0FaC4992E5C4D3EA18A"
# ЦЕЛИ ПОД НАБЛЮДЕНИЕМ. Было — одна захардкоженная цель окна 31.07; окно прошло, и вотчер
# с 15:00Z следил за нулём. 31.07 книга 27.08 разрезана бакетами: 86% её объёма — ДВЕ
# позиции, и это не книга, а две ставки «орёл-решка». Обе под наблюдение: когда опцион
# испарится (как $100,214 у 0xd418224ae3), мы узнаем в момент гашения, а не на предбоевом
# скане, и не потратим планирование на несуществующую цель.
TARGETS = [
    # coll_move_pct: 0 — залог ЧИСТЫЙ WETH, начисления нет вовсе ⇒ любое движение слота =
    # действие заёмщика. Общий допуск 2% ставился под обёртку кита-2 и здесь глушил бы
    # реальные вводы/выводы: 05.08 довзнос +4.83 WETH (+1.17%, tx 0xf7e70b95…, долив плеча
    # через Tenor как 04.08) ушёл в лог с ложной пометкой «начисление» — а тихий ВЫВОД
    # <2% WETH (сигнал подготовки к погашению) прошёл бы так же молча.
    # 06.08 04:06-04:21Z третий долив, теперь серией: 8 Tenor-траншей ($4k/$5k/$9k, маркер
    # "tenor\x01", кредитор-исполнитель 0x4b0711b1 = EIP-7702 аккаунт) — долг $359.2k →
    # $399.2k (+$40k), залог +24.77 WETH. Метка ниже обновлена $346k → $399k.
    {"name": "кит-1 27.08 ~$399k",
     "market": "0xf27319855df886a604dda3d5675007f0aa6eee504c99f1d789c86b21075f7c20",
     "borrower": "0xfb94d3404c1d3d9d6f08f79e58041d5ea95accfa",
     "maturity": 1787788800, "loan_dec": 6, "coll_dec": 18, "coll": "WETH",
     "coll_move_pct": 0.0},
    # 04.08 разбор тревог «+$12k долг / залог +6.16%»: кит долил плечо через Tenor
    # (tx 0xe94dcc27…, блок 49515712) — кредитор 0x78266e3c внёс $12,000, долг вырос на
    # $12,008.10, выручка + $314.21 своих USDC заёмщика реинвестированы в залог. Слот 1 —
    # НЕ cbBTC: это обёртка 'cbBTC-USDC-collat' 0xf6a70085 (18 знаков, ≈$1.003/юнит,
    # USDC в Morpho-vault 0xb9093c5e) — растёт начислением, отсюда и допуск 0f76f1a.
    # 06.08 04:18-04:19Z два Tenor-транша (tx 0x7d91fde2…/0x539794df…, блоки 49600278/
    # 49600301) ВНУТРИ серии кита-1 и тем же исполнителем 0x4b0711b1: долг $237.6k →
    # $247.6k (+$10,004), слот 1 +10,228.0 юнитов (сумма траншей бит-точно). Один
    # Tenor-исполнитель обслуживает обоих китов; одна ли это рука — неизвестно.
    # 06.08 форк-экзамен: сейз обёртки НЕВОЗМОЖЕН ни для кого (гейт шэров, белый список =
    # ядро Midnight) — кит-2 больше не цель огня, но остаётся под радаром: его движения =
    # разведка книги (Tenor-серии общие с китом-1).
    # 07.08 06:07:25Z четвёртый Tenor-долив (tx 0x50c68b49…, блок 49646749): кредитор-EOA
    # 0x788b8b3e внёс $11,000 + $284.88 своих USDC заёмщика → обёртка → слот 1
    # +11,249.157 юнитов (бит-точно), долг +$11,004.16 → $263.8k. Фандер КАЖДЫЙ РАЗ новый
    # (0x78266e3c → 0x4b0711b1 → 0x788b8b3e) — похоже на ордербук Tenor, не на одну руку.
    # Между алертами 06-07.08 прошли молча микро-транши $2,001+$3,001 (по-шаговый порог
    # $5k, штатно). Метка ниже обновлена $248k → $264k.
    {"name": "кит-2 27.08 ~$264k (гейт закрыт, не цель)",
     "market": "0x44495af1cca7842191a65a73978e01ed72238731e193c3b11460083efd60a318",
     "borrower": "0xd75ffb585ff88d3aa50b7cf9230b27a7eb923c20",
     "maturity": 1787788800, "loan_dec": 6, "coll_dec": 18, "coll": "cbBTC-USDC-collat"},
    # 06.08: окно 28.08 15:00Z несёт $100,277.70 в ГОЛОМ cbBTC (маршрут = репетиция 27.07,
    # потолок ~$4.1k на полной рампе). Заёмщик — 0xd418224ae3, ТОТ ЖЕ, кто погасил $100,214
    # за 25ч до окна 31.07 (серийный роллер): ждём повторного самогашения накануне, радар
    # нужен ровно чтобы узнать это В МОМЕНТ, а не на предбоевом скане.
    {"name": "кит 28.08 ~$100k cbBTC (роллер 31.07)",
     "market": "0x05959752fdeff325962b9d263edb421efc6e2186a49360dba6c32e86ebf6c84c",
     "borrower": "0xd418224ae3c510b645112fd9275ccfd50f996ee4",
     "maturity": 1787929200, "loan_dec": 6, "coll_dec": 8, "coll": "cbBTC",
     "coll_move_pct": 0.0},
]
# Совместимость с остальным файлом (секции конкурента и чужих ликвидаций): «главная» цель.
MARKET = TARGETS[0]["market"]
BORROWER = TARGETS[0]["borrower"]
MATURITY = TARGETS[0]["maturity"]           # 2026-08-27 00:00:00 UTC
SEL_DEBT = "0x93af51c2"                     # debt(bytes32,address)
SEL_COLL = "0xecdcc72d"                     # collateral(bytes32,address,uint256)
SEL_COLL_BITMAP = "0xb502e1f9"              # collateralBitmap(bytes32,address)
SEL_BALANCE_OF = "0x70a08231"               # balanceOf(address)
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
LOAN_DEC, COLL_DEC = 6, 8                   # USDC / cbBTC

COMPETITOR = "0x6cf59693571329db4a613f9a398205e6de04d05f"

# Liquidate(address,bytes32,address,uint256,uint256,address,bool,address,address,
#           uint256,uint256,uint256) — indexed: id, collateral, borrower
TOPIC_LIQUIDATE = "0x" + keccak256(
    b"Liquidate(address,bytes32,address,uint256,uint256,address,bool,address,address,"
    b"uint256,uint256,uint256)").hex()

# Рынки БЛИЖАЙШИХ окон календаря (06.08: было 11 рынков ПРОШЕДШЕГО окна 31.07 — ветка
# «чужой укус» смотрела на мёртвую воду и не увидела бы игрока, репетирующего на нашем
# окне). Сейчас: 4 рынка окна 27.08 00:00Z + 11 рынков окна 28.08 15:00Z. Чужой укус по
# ЛЮБОМУ из них означает, что игрок работает наш календарь. После окон список обновить
# на следующие (24.09 и далее) — иначе снова мёртвая вода.
WINDOW_MARKETS = [
    # окно 27.08 00:00Z
    "0xf27319855df886a604dda3d5675007f0aa6eee504c99f1d789c86b21075f7c20",
    "0x44495af1cca7842191a65a73978e01ed72238731e193c3b11460083efd60a318",
    "0x13cdfedf56f731817322c6932b48f496da937c36a51c7178ac63ed915d02fc98",
    "0xf08073b575bebb2571b4bc6d1ec3fc4ea9ac3315c4e5c95cef284957d756166f",
    # окно 28.08 15:00Z
    "0x10a033a31e0143f28ea28af165b8c931764f5679843754dea86a0c6320655eb2",
    "0xe3045f234b57647db1490129ab744992049f14fdf0db4c752ea802a68ac6bb84",
    "0x82b93776fe7d9e0f5ae95a943fa17f2f3b343424dd94c4a51c2c65b1408f28d3",
    "0xa28cffd5ae5f8b59335d974ef541aaf4c3d3beee5d12e28079eebfd1c5e2669f",
    "0x55f1ab8766b38ef512ccbe60db2c5d48639f59f9bb6251a6262fdd61ef774434",
    "0xa576dd655f54b31772a868edb3c65645f077ae61ae5de3b2a9b936b2fd8a21eb",
    "0xc864824eddb0b69b861b33f9cac4f48f139db416757c2ca13dbad2032ffce3c3",
    "0x0b8fe70a0597e371bdcdbf1bbca019826f819d9a1a434e5d54a6793c3393c5ab",
    "0x53bd80c2dbe4657d41051b01e660d70511e09df9fb0a0e679067381b3c27207c",
    "0x05959752fdeff325962b9d263edb421efc6e2186a49360dba6c32e86ebf6c84c",
    "0x247e4562cfa7c66dd6ddd666041ff3e1a1470bb8e27122fc300329b6a2b82358",
]

# Известные ликвидаторы Midnight на 27.07 (6 шт., включая наш контракт). Любой ДРУГОЙ
# caller на рынках окна = новый игрок, в т.ч. «сиблинг» того же оператора со свежего адреса.
KNOWN_LIQUIDATORS = {
    "0x4a2b91e0a5a7ef056900d3d10ce751f2831a56f4",
    "0x5f2f456266831dbbe57d3845187a93e1bd5ef1ba",
    "0x6ba008e3f6ec55dc6412e459ac67949c6d1620c5",
    "0x6cf59693571329db4a613f9a398205e6de04d05f",
    "0x6d9dea0ae96156862a534e5016173d3e001cb7d0",
    "0x8e0f31cde0d2037a74522b2627f3453f5b5966cd",   # наш
}

RPCS = ["https://mainnet.base.org", "https://base-rpc.publicnode.com",
        "https://gateway.tenderly.co/public/base", "https://base.drpc.org"]
STATE = os.path.expanduser("~/.fleet-watch/target-watch.json")
# «видимый/достижимый горизонт»: ближе этого срока до окна конкурент снова важен
COMP_HORIZON_SEC = float(os.environ.get("MN_COMP_HORIZON_DAYS", "7")) * 86400
# Отправка ушла в общефлотский маршрутизатор (~/.fleet-watch/notify.py): токен, чат,
# подпись отправителя и сторож транспорта теперь в одной точке на весь флот.

REPAY_ALERT_USD = float(os.environ.get("MN_WATCH_REPAY_USD", "5000"))
COLL_EPS = 1e-6
# порог «залог реально двинулся», % от слота: выше суточного начисления обёртки
COLL_MOVE_PCT = float(os.environ.get("MN_WATCH_COLL_MOVE_PCT", "2"))
# Порог «чужой ликвидатор» в units репея. Займ рынков окна — USDC (6 знаков) ⇒ units ≈
# микродоллары, 300_000_000 ≈ $300 = НАШ ПОЛ ОГНЯ (MN_THRESHOLD). Смысл порога именно в
# этом: ниже пола мы бы не стреляли НИКОГДА, значит чужое взятие — не упущенные деньги,
# а перепись. Алерт = деньги, которые могли быть нашими, прошли мимо (сигнал по ИСХОДУ).
# Было $50 + полный обход для postMaturity — см. ветку ниже, почему обход снят.
FOREIGN_MIN_UNITS = int(os.environ.get("MN_WATCH_FOREIGN_UNITS", "300000000"))
ETH_ARM_WEI = int(float(os.environ.get("MN_WATCH_ETH_ARM", "0.02")) * 10 ** 18)
# Ступени USDC: заметка / пересмотр пола / конфигурация гонки. Верхняя ступень стоит на
# $28k, а не на $50k: ровно с этого укуса наш пол $3,000 становится НЕДОСТИЖИМ (см. шапку).
USDC_TIERS = [(1_000, "📎 заметка"), (10_000, "⚠️ пересмотреть пол"),
              (28_000, "🚨 конфигурация гонки")]
FLOOR_USD = float(os.environ.get("MN_WATCH_FLOOR", "3000"))
RAMP_SEC, RAMP_SLOPE, RAMP_FIX = 3600.0, 1.22, 260.0   # net(t) ≈ 1.22·t − 260 при f=0

# --- автоперевод в race ---------------------------------------------------------------
# Сигнал и действие раньше были разведены: радар слал в TG «переключи», а нажимал человек.
# Дырка закрыта — радар зовёт переключатель сам. ДВА ПРЕДОХРАНИТЕЛЯ:
#  1) ТОЛЬКО ДО ОКНА. Переключение = рестарт бота = 40-60с слепоты. До maturity эти секунды
#     не стоят ничего (ловить нечего), ВНУТРИ окна это ровно время боя. Позже границы —
#     только громкий алерт, профиль не трогаем.
#  2) ТЕСТОВЫЙ РЕЖИМ НЕ ДЕЙСТВУЕТ. Гард привязан к тому же MN_WATCH_MUTE, что закрывает TG:
#     один флаг закрывает и болталку, и руки — забыть отдельный невозможно.
PROFILE_SH = "/home/claude-agent/.fleet-watch/mn-profile.sh"
MN_ENV = "/home/claude-agent/.midnight-bot/env"
# 120с = двойной запас на рестарт (cron поднимает ≤60с): переключение не должно
# «оседлать» границу maturity.
SWITCH_MARGIN_SEC = float(os.environ.get("MN_WATCH_SWITCH_MARGIN", "120"))


def _w(x: str) -> str:
    return x.lower().replace("0x", "").rjust(64, "0")


def rpc(method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    last = None
    for url in RPCS:
        try:
            # UA обязателен: часть публичных RPC отдаёт 403 на дефолтный urllib-UA
            # (флот-урок SEND-3 17.07; здесь 19 перемежающихся отказов скана за 02-04.08)
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "fleet-watch/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
            if "result" in d:
                return d["result"]
            last = d.get("error")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"все RPC недоступны: {last}")


def call(to: str, data: str) -> int:
    r = rpc("eth_call", [{"to": to, "data": data}, "latest"])
    return int(r, 16) if r and r != "0x" else 0


def _rlp_create(addr: str, nonce: int) -> str:
    """CREATE-адрес = keccak(rlp([sender, nonce]))[12:] — детерминирован, поэтому будущий
    контракт конкурента виден ДО того, как он им воспользуется."""
    a = bytes.fromhex(addr.lower().replace("0x", ""))
    item_a = b"\x94" + a                                    # 0x80+20
    if nonce == 0:
        item_n = b"\x80"
    elif nonce < 0x80:
        item_n = bytes([nonce])
    else:
        nb = nonce.to_bytes((nonce.bit_length() + 7) // 8, "big")
        item_n = bytes([0x80 + len(nb)]) + nb
    payload = item_a + item_n
    prefix = (bytes([0xC0 + len(payload)]) if len(payload) < 56
              else bytes([0xF7 + ((len(payload).bit_length() + 7) // 8)])
              + len(payload).to_bytes((len(payload).bit_length() + 7) // 8, "big"))
    return "0x" + keccak256(prefix + payload).hex()[24:]


def floor_reachable(remaining_usd: float) -> tuple[bool, float]:
    """Достижим ли пол FLOOR_USD на остатке позиции и когда. См. шапку: при укусе доли f
    наш net ≈ (1−f)·(1.22t − 260), поэтому t = (FLOOR/(1−f) + 260)/1.22."""
    full = 100214.49
    frac = max(remaining_usd, 0.0) / full
    if frac <= 0:
        return False, 0.0
    t = (FLOOR_USD / frac + RAMP_FIX) / RAMP_SLOPE
    return t <= RAMP_SEC, t


def current_floor() -> float:
    """Пол из боевого env (источник правды о профиле)."""
    try:
        with open(MN_ENV) as f:
            for ln in f:
                if ln.startswith("export MN_MIN_PROFIT="):
                    return float(ln.split("=", 1)[1].split()[0].split("#")[0])
    except Exception:  # noqa: BLE001
        pass
    return -1.0


def arm_race(reason: str, dt: int) -> None:
    """Перевести бота в race-профиль. Идемпотентно; молчит, если уже race."""
    floor = current_floor()
    if 0 <= floor <= 100:
        log(f"race уже включён (пол ${floor:,.0f}) — {reason}, действий не нужно")
        return
    if dt <= SWITCH_MARGIN_SEC:
        # ЕДИНСТВЕННЫЙ HIL этого вотчера: рестарт в боевом окне стоит 40-60с слепоты, и
        # платить эту цену — распоряжение живым огнём, а не техническая починка. Агент
        # такое не решает (пункт 4 критерия), поэтому будим человека.
        tg(f"🚨 [midnight] {reason}\nПРОФИЛЬ НЕ ТРОГАЮ: до/после окна осталось "
           f"{dt}с (<{SWITCH_MARGIN_SEC:.0f}с) — рестарт стоит 40-60с слепоты, а это уже "
           f"время боя. НУЖНО РЕШЕНИЕ ЧЕЛОВЕКА: {PROFILE_SH} race",
           hil=True, key="race-margin", dedup_sec=3600)
        log(f"АЛЕРТ: {reason}; автоперевод заблокирован (dt={dt}с)")
        return
    # Гард НА САМОМ ДЕЙСТВИИ, тот же флаг, что закрывает TG: забыть отдельный невозможно.
    if os.environ.get("MN_WATCH_MUTE") == "1":
        print(f"[act muted] переключил бы в race: {reason}")
        return
    import subprocess
    try:
        r = subprocess.run([PROFILE_SH, "race"], capture_output=True, text=True, timeout=180)
        ok = r.returncode == 0
        tail = (r.stdout or r.stderr).strip().splitlines()[-3:]
        tg(f"{'✅' if ok else '⚠️'} [midnight] АВТОПЕРЕВОД В RACE {'выполнен' if ok else 'СБОЙ'}\n"
           f"Причина: {reason}\n" + "\n".join(tail) +
           (f"\nПроверить: {PROFILE_SH} status" if ok else
            f"\nСДЕЛАТЬ РУКАМИ: {PROFILE_SH} race"))
        log(f"АВТОПЕРЕВОД race rc={r.returncode}: {reason}")
    except Exception as e:  # noqa: BLE001
        tg(f"⚠️ [midnight] АВТОПЕРЕВОД В RACE УПАЛ ({e}). Причина: {reason}. "
           f"СДЕЛАТЬ РУКАМИ: {PROFILE_SH} race")
        log(f"автоперевод упал: {e}")


def tg(text: str, *, hil: bool = False, key: str = "", dedup_sec: float = 0.0) -> None:
    """Тревога вотчера. АДРЕСАТ ПО УМОЛЧАНИЮ — АГЕНТ, не человек (решение 03.08).

    Почти всё, что видит этот вотчер, агент разбирает сам: конкурент заправился, залог
    кита двинулся, чужой забрал позицию, долг изменился. Человеку тут решать нечего, пока
    дело не упрётся в его подпись — и такой случай здесь ровно один (см. arm_race).

    Сторож на транспорте живёт теперь в notify.muted(): MN_WATCH_MUTE=1 по-прежнему
    закрывает канал, плюс добавилось автоопределение тестового прогона.
    """
    notify(text, source="midnight", hil=hil, key=key, dedup_sec=dedup_sec)


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def foreign_liquidations(from_block: int, to_block: int) -> list[dict]:
    """События Liquidate на рынках ОКНА от caller вне белого списка.

    Раскладка события (сверено с analysis/midnight_day0.decode_liquidate):
      indexed: id_, collateral, borrower;
      data:    caller(0), seizedAssets(1), repaidUnits(2), postMaturityMode(3),
               receiver(4), payer(5), badDebt(6), lossFactor(7), feeCredit(8).
    """
    if from_block > to_block:
        return []
    logs = rpc("eth_getLogs", [{
        "address": MIDNIGHT, "fromBlock": hex(from_block), "toBlock": hex(to_block),
        "topics": [TOPIC_LIQUIDATE, [m for m in WINDOW_MARKETS]],
    }])
    out = []
    for lg in logs or []:
        data = lg["data"][2:]
        caller = "0x" + data[24:64]
        if caller.lower() in KNOWN_LIQUIDATORS:
            continue
        out.append({"caller": caller.lower(), "market": lg["topics"][1],
                    "repaid": int(data[64 * 2:64 * 3], 16),
                    "post": bool(int(data[64 * 3:64 * 4], 16)),
                    "block": int(lg["blockNumber"], 16)})
    return out


def _slots(fp: str) -> dict:
    """Отпечаток «k:v,k:v» → {слот: units}. «нет» / пустое → {}."""
    out = {}
    for part in (fp or "").split(","):
        if ":" in part:
            k, _, v = part.partition(":")
            try:
                out[int(k)] = int(v)
            except ValueError:
                pass
    return out


def watch_targets(prev: dict) -> dict:
    """Долг/залог по КАЖДОЙ цели. Алерт одноразовый на цель (защёлка `gone_<i>`), как у
    прежней одиночной ветки: 30.07 состояние-вместо-перехода давало 20 сообщений в час."""
    st = {}
    for i, t in enumerate(TARGETS):
        try:
            d = call(MIDNIGHT, SEL_DEBT + _w(t["market"]) + _w(t["borrower"]))
            # ЗАЛОГ — ПОСЛОТНО И БЕЗ ОБЩЕЙ ЕДИНИЦЫ. У кита-2 на рынке два коллатерала
            # с РАЗНЫМИ десятичными (cbBTC 8 и обёртка 18): слот 0 дал бы «0.000000»
            # при живом долге $186,907, а сумма слотов — «1912598661918925 cbBTC».
            # Оба варианта — молчаливая дезинформация, поэтому держим сырые значения
            # по слотам: они нужны только для ДЕТЕКТА ИЗМЕНЕНИЯ, не для показа в $.
            bm = call(MIDNIGHT, SEL_COLL_BITMAP + _w(t["market"]) + _w(t["borrower"]))
            slots = {k: call(MIDNIGHT, SEL_COLL + _w(t["market"]) + _w(t["borrower"])
                             + _w(hex(k))) for k in range(16) if bm >> k & 1}
        except Exception as e:  # noqa: BLE001 — одна цель не валит радар
            log(f"{t['name']}: чтение не удалось ({e})")
            # БАЗЛАЙН ПЕРЕЖИВАЕТ СБОЙ ЧТЕНИЯ. Без переноса prev-ключей упавший запуск
            # записывал стейт БЕЗ этой цели, и следующий успешный проход сравнивал с
            # пустотой: 03.08 один 403 в 14:00 съел оба алерта кита-2 (долг +$5,003 и
            # залог +2.64% в 14:15 прошли молча). Сбой RPC — не наблюдение «изменений нет».
            for k in (f"gone_{i}", f"last_debt_{i}", f"coll_{i}"):
                if k in prev:
                    st[k] = prev[k]
            continue
        d_usd = d / 10 ** t["loan_dec"]
        c_fp = ",".join(f"{k}:{v}" for k, v in sorted(slots.items())) or "нет"
        left = (t["maturity"] - int(time.time())) / 3600
        log(f"{t['name']}: долг ${d_usd:,.2f} залог(слоты, сырые) {c_fp} "
            f"до окна {left:.1f}ч")
        gone_key, dbt_key, col_key = f"gone_{i}", f"last_debt_{i}", f"coll_{i}"
        p_d = prev.get(dbt_key)
        p_c = prev.get(col_key)
        st[gone_key] = d == 0
        st[dbt_key] = d_usd if d_usd > 0 else prev.get(dbt_key, 0.0)
        st[col_key] = c_fp
        if d == 0:
            if not prev.get(gone_key, False):
                was = prev.get(dbt_key, 0.0)
                tg(f"🚨 [midnight] ОПЦИОН ИСПАРИЛСЯ: {t['name']} погашен сам — долг "
                   f"${was:,.0f} → $0 за {left:.1f}ч до maturity. Планирование на эту "
                   f"цель снять. (Прецедент 30.07: $100,214 ушли за 24ч55м до срока.)")
                log(f"АЛЕРТ: {t['name']} долг обнулился (было ${was:,.0f})")
            else:
                log(f"{t['name']}: долг ноль — уже сообщал, TG молчит")
        elif p_d is not None and abs(p_d - d_usd) >= REPAY_ALERT_USD:
            # Движение долга крупной цели = смена размера опциона. У position.debt в
            # Midnight аккруала НЕТ вовсе (зеро-купон в units: растёт только добором
            # через Take, падает только Repay/Liquidate — сверено с Midnight.sol 03.08),
            # так что ЛЮБОЕ движение долга = действие заёмщика. Порог $5k — чистая
            # существенность: мелкий добор не меняет планирование на окно.
            tg(f"{'⚠️' if p_d > d_usd else '📈'} [midnight] {t['name']}: долг "
               f"${p_d:,.0f} → ${d_usd:,.0f} ({d_usd - p_d:+,.0f}), до окна {left:.1f}ч.")
            log(f"АЛЕРТ: {t['name']} долг {p_d:,.0f} → {d_usd:,.0f}")
        if p_c is not None and c_fp != p_c:
            # ДОПУСК НА НАЧИСЛЕНИЕ. Отпечаток — сырые units, а обёрточный залог РАСТЁТ сам
            # (замер 01.08: +1.02 из 191,264 за 30 мин = 0.0005%, ~9%/год). Строгое
            # равенство строк на таком балансе алертит на каждый тик начисления — 01.08 это
            # дало две тревоги за полчаса и продолжалось бы вечно. Сигналим только на то,
            # что делает ЗАЁМЩИК: появление/исчезновение слота (структурное изменение) или
            # движение слота ≥COLL_MOVE_PCT. Реальный ввод/вывод залога — десятки процентов,
            # начисление за 25 дней до окна — доли процента, порог их разделяет с запасом.
            was, now_ = _slots(p_c), _slots(c_fp)
            structural = set(was) != set(now_)
            move_pct = t.get("coll_move_pct", COLL_MOVE_PCT)
            moved = [(k, was[k], now_[k]) for k in set(was) & set(now_)
                     if was[k] and abs(now_[k] - was[k]) / was[k] * 100 > move_pct]
            if structural or moved:
                what = ("состав слотов изменился" if structural else
                        "; ".join(f"слот {k}: {100 * (b - a) / a:+.2f}%" for k, a, b in moved))
                tg(f"🔧 [midnight] {t['name']}: ЗАЛОГ ДВИНУЛСЯ — {what} "
                   f"({p_c} → {c_fp}), до окна {left:.1f}ч.")
                log(f"АЛЕРТ: {t['name']} залог {p_c} → {c_fp} ({what})")
            else:
                d_pct = max((abs(now_[k] - was[k]) / was[k] * 100
                             for k in set(was) & set(now_) if was[k]), default=0.0)
                log(f"{t['name']}: залог +{d_pct:.4f}% — ниже порога "
                    f"{move_pct}% (начисление обёртки), в лог")
    return st


def main() -> int:
    comp_usdc = call(USDC, SEL_BALANCE_OF + _w(COMPETITOR))
    comp_eth = int(rpc("eth_getBalance", [COMPETITOR, "latest"]), 16)
    comp_nonce = int(rpc("eth_getTransactionCount", [COMPETITOR, "latest"]), 16)
    head = int(rpc("eth_blockNumber", []), 16)

    cu = comp_usdc / 10 ** LOAN_DEC
    now = int(time.time())
    dt = MATURITY - now

    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE))
        except Exception:  # noqa: BLE001
            prev = {}
    # Защёлка «цель исчезла»: ветка debt==0 проверяет СОСТОЯНИЕ, а не переход, поэтому без
    # неё алерт повторяется каждый запуск — при кроне */3 в дни окна это 20 сообщений в час
    # (поймано в бою 30.07). Защёлка равна текущему состоянию: вернётся долг — снимется сама.
    # цели окна 27.08 — после загрузки prev (защёлки на цель живут в том же стейте)
    tgt_state = watch_targets(prev)
    # последний НЕнулевой долг: иначе в одноразовом алерте печатается «Было $0» (p_d уже ноль
    # из прошлого запуска) — сообщение теряет ровно ту цифру, ради которой оно шлётся
    cur = {**tgt_state,
           "comp_usd": cu, "comp_eth": comp_eth, "comp_nonce": comp_nonce,
           "head": head, "ts": now}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(cur, open(STATE, "w"), indent=1)

    log(f"конкурент USDC ${cu:,.2f} ETH {comp_eth/1e18:.6f} nonce {comp_nonce}")
    if not prev:
        log("базовая линия записана — TG молчит")
        return 0

    when = f"до окна {dt/3600:.1f}ч" if dt > 0 else f"ПОСЛЕ окна +{-dt/60:.0f}мин"
    p_u = prev.get("comp_usd", cu)
    p_e = prev.get("comp_eth", comp_eth)
    p_n = prev.get("comp_nonce", comp_nonce)

    # Секция «цель» УДАЛЕНА: она читала ОДНУ захардкоженную позицию и после
    # перевода на список целей 27.08 стала считать залог кита-1 (WETH, 18 знаков)
    # в COLL_DEC=8 — прогон выдал «залог 4099327697207 cbBTC» и два ложных алерта.
    # Вся логика цели теперь в watch_targets(): свои десятичные на каждую цель.

    # --- 2. оснащение конкурента ------------------------------------------------------
    # ГЕЙТ (решение kelbic 30.07): следим за конкурентом ТОЛЬКО когда есть цель на видимом
    # горизонте. Иначе алерты бессмысленны: за 30.07 сторож дал 4 сообщения за 21 минуту
    # (nonce 584→627→662→690→693) и НИ ОДНОГО изменённого решения; за всю сессию три разбора
    # конкурентов кончились одинаково — «не угроза». Nonce — прокси активности, а не угрозы:
    # адрес крутит сотню транзакций в день при балансе $0.30 и не опасен, а тот, кто реально
    # придёт за окном, скорее всего вообще не в списке наблюдаемых.
    # Гейт снимается САМ, как только у цели снова появится долг и окно окажется близко.
    # «цель жива» теперь = жива ЛЮБАЯ из наблюдаемых целей (было: одна захардкоженная)
    target_alive = any(not tgt_state.get(f"gone_{i}", False) for i in range(len(TARGETS)))
    horizon_ok = 0 < dt <= COMP_HORIZON_SEC
    watch_comp = target_alive and horizon_ok
    if not watch_comp:
        why = "цели нет" if not target_alive else f"окно дальше {COMP_HORIZON_SEC/86400:.0f}д"
        log(f"конкурент: наблюдение выключено ({why}) — nonce={comp_nonce} "
            f"ETH={comp_eth/1e18:.6f} USDC=${cu:,.2f} (в лог, без TG)")
    if watch_comp and comp_eth >= ETH_ARM_WEI > p_e:
        tg(f"⛽ [midnight] КОНКУРЕНТ ЗАПРАВЛЯЕТСЯ: ETH {p_e/1e18:.5f} → "
           f"{comp_eth/1e18:.5f} у {COMPETITOR[:10]}… ({when}). Самый ранний признак "
           f"оснащения — газ нужен и под repay-из-кармана, и под деплой контракта.")
        log(f"АЛЕРТ: ETH {p_e/1e18:.5f} → {comp_eth/1e18:.5f}")

    if watch_comp and comp_nonce != p_n:
        deployed = []
        for n in range(p_n, comp_nonce):
            addr = _rlp_create(COMPETITOR, n)
            code = rpc("eth_getCode", [addr, "latest"])
            if code and code != "0x":
                deployed.append(addr)
        if deployed:
            tg(f"🚨🚨 [midnight] КОНКУРЕНТ ЗАДЕПЛОИЛ КОНТРАКТ: {', '.join(deployed)} "
               f"({when}). На Base флешлоун даёт $100k БЕЗ капитала — с контрактом он "
               f"боеспособен независимо от баланса. На WC его почерк ровно такой "
               f"(EOA→свой контракт 0xe741bc7c…).")
            log(f"АЛЕРТ: деплой контракта {deployed}")
            arm_race(f"конкурент задеплоил контракт {deployed[0]}", dt)
        else:
            tg(f"👣 [midnight] КОНКУРЕНТ АКТИВЕН НА BASE: nonce {p_n} → {comp_nonce} "
               f"({when}). Контракта среди этих tx нет, но адрес ожил.")
            log(f"АЛЕРТ: nonce {p_n} → {comp_nonce}")

    for lvl, label in USDC_TIERS:
        if watch_comp and cu >= lvl > p_u:
            extra = ("— с этого укуса наш пол $3,000 становится НЕДОСТИЖИМ"
                     if lvl >= 28_000 else "")
            tg(f"🥊 [midnight] КОНКУРЕНТ ФОНДИРУЕТСЯ {label}: USDC ${p_u:,.0f} → "
               f"${cu:,.0f} ({when}) {extra}")
            log(f"АЛЕРТ: USDC ${p_u:,.0f} → ${cu:,.0f} ступень ${lvl:,}")
            if lvl >= 28_000:
                arm_race(f"конкурент фондирован ${cu:,.0f} — наш пол ${FLOOR_USD:,.0f} "
                         f"при таком укусе недостижим", dt)

    # --- 3. чужие ликвидации на рынках окна -------------------------------------------
    frm = prev.get("head", head - 500) + 1
    # ПЕРВОЕ ПОЯВЛЕНИЕ НЕЗНАКОМОГО АДРЕСА — сигнал класса «сиблинг» (31.07). Прежняя разведка
    # следила за ОДНИМ захардкоженным адресом (0x6cf59693) и трижды дала «не угроза», а 31.07
    # пришёл незнакомый 0x2bfc428f — ровно тот класс, который слежка за адресом не ловит.
    # Спамить это не может по построению: защёлка в стейте, один алерт на адрес НАВСЕГДА.
    # Размер здесь НЕ фильтруем сознательно: новый игрок калибруется на пыли (0x2bfc428f
    # засветился за 4 дня до окна), и именно ранняя калибровка — то, что мы хотим видеть.
    # Повторные взятия уже знакомого адреса идут по общему правилу: выше нашего пола огня —
    # «деньги прошли мимо» в TG, ниже — в лог.
    # ПЕРВЫЙ ЗАПУСК = БАЗЛАЙН БЕЗ АЛЕРТОВ. Пустая защёлка означала бы «все известные адреса
    # незнакомы» и дала бы залп на ровном месте — ровно та ошибка, что 30.07 выдала пять
    # ложных ⏱ на первом окне вотчера ставок. Первый проход только запоминает.
    baseline = "seen_callers" not in prev
    seen_callers = set(prev.get("seen_callers", []))
    try:
        for f in foreign_liquidations(max(frm, head - 50_000), head):
            c = f["caller"].lower()
            if c not in seen_callers:
                seen_callers.add(c)
                if baseline:
                    log(f"базлайн незнакомых: запомнил {c} (первый запуск, без алерта)")
                    continue
                tg(f"🆕 [midnight] НОВЫЙ ЛИКВИДАТОР на рынках окна: {f['caller']} "
                   f"(первое появление, блок {f['block']}, ~${f['repaid'] / 1e6:,.2f}). "
                   f"Класс «сиблинг»: 31.07 ждали 0x6cf59693, пришёл 0x2bfc428f. "
                   f"Профиль: python3 -m analysis.midnight_rivals")
                log(f"АЛЕРТ: новый ликвидатор {c} блок {f['block']} units={f['repaid']}")
            # РАЗМЕРНЫЙ КВАЛИФИКАТОР (28.07): без него ветка разбудила нас ТРИЖДЫ на взятиях
            # по $2.26 / $1.22 / $0.03 — вся активность Midnight сейчас пылевая (25 событий за
            # 200k блоков, все < $5). Порог в units; займ рынков окна — USDC (6 знаков), т.е.
            # units ≈ микродоллары. Для рынка с 18-значным займом порог фактически не
            # отсекает — это СОЗНАТЕЛЬНО: ошибка в сторону лишнего алерта, а не тишины.
            #
            # 31.07: ОБХОД ПОРОГА ДЛЯ postMaturity СНЯТ. Он ставился как разведка перед окном
            # 31.07 («кто взял пост-maturity даже на $0.01 — знает календарь и придёт»), и эту
            # работу уже сделал: окно прошло, конкурент ИЗМЕРЕН вживую (0x2bfc428f бил по нашей
            # цели за 32с до нас, словил реверт, через 10с после нас забрал вторую на $1.35).
            # Гипотеза закрыта фактом, а обход остался и в тот же день разбудил на взятии $5.07
            # через 18 минут после окна — по такому сигналу человек не делает НИЧЕГО.
            # Теперь порог общий и равен нашему полу огня: чужое взятие ниже пола мы бы не
            # взяли при любом раскладе ⇒ лог. Выше пола — это упущенные деньги ⇒ TG.
            if f["repaid"] < FOREIGN_MIN_UNITS:
                # post= сохраняем ИМЕННО здесь: пост-maturity взятие — самый ценный след для
                # форензики (кто знает календарь), и он теперь виден только в логе.
                log(f"чужой ликвидатор {f['caller']} ниже пола огня: {f['repaid']} units "
                    f"(< {FOREIGN_MIN_UNITS}) post={f['post']} рынок {f['market'][:14]}… "
                    f"блок {f['block']} — в лог, без алерта")
                continue
            kind = "пост-maturity " if f["post"] else ""
            tg(f"🏁 [midnight] ДЕНЬГИ ПРОШЛИ МИМО: {f['caller']} взял {kind}позицию "
               f"~${f['repaid'] / 1e6:,.0f} (если займ USDC) на {f['market'][:14]}… "
               f"(блок {f['block']}, {when}). Это ВЫШЕ нашего пола ${FOREIGN_MIN_UNITS / 1e6:,.0f} "
               f"— мы могли её взять и не взяли.")
            log(f"АЛЕРТ: чужой ликвидатор {f['caller']} блок {f['block']} "
                f"units={f['repaid']} post={f['post']}")
    except Exception as e:  # noqa: BLE001 — скан логов НИКОГДА не валит радар
        log(f"скан чужих ликвидаций не удался (не критично): {e}")
    # Защёлка знакомых адресов пишется ОТДЕЛЬНО и ПОСЛЕ скана: основной стейт сохраняется
    # выше (строка ~315), до этой секции, и без второго дампа список не пережил бы проход —
    # тогда каждый запуск считал бы всех незнакомыми и алерт стал бы вечным.
    if seen_callers != set(prev.get("seen_callers", [])) or baseline:
        cur["seen_callers"] = sorted(seen_callers)
        json.dump(cur, open(STATE, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
