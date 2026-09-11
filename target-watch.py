#!/usr/bin/env python3
"""Радар окна Morpho Midnight 25.09.2026 15:00Z (Base): кит и топ-3, МАРКЕР УВОДА,
оснащение конкурента, чужие укусы, предвзвод бота.

ПЕРЕНАЦЕЛЕН 11.09.2026 (прежняя версия — target-watch.py.bak-20260911). До этого дня
радар две недели опрашивал ТРИ ТРУПА (киты 27.08 и роллер 28.08 — у всех `debt=$0`,
окна в минусе на 330-368 часов), следил за конкурентом, актуальным на 27.07, а
`WINDOW_MARKETS` держал 15 id прошедших окон. Он был жив, тикал каждые 15 минут и не мог
предупредить НИ О ЧЁМ: вывод субъекта не заглушил сторожа, а убил его СПЕЦИФИЧНОСТЬ
([[retired-subject-needs-watchdog-sweep]], [[dead-watchdog-worse-than-none]] — «всё тихо»
от такого сторожа не значит ничего).

Все числа и адреса ниже — из `midnight-liquidator/docs/PREFLIGHT-2509.md` (замер 11.09 на
закреплённом блоке 51161260). Радар ТОЛЬКО ЧИТАЕТ: ни одной tx, ни одной правки env,
ни одного рестарта бота. Автоперевод профиля (`arm_race`, `mn-profile.sh`) СНЯТ — см. ниже.

ЧТО СМОТРИТ (≤ десятка eth_call/getLogs за прогон, крон */15):
  1. ЦЕЛИ — кит $1,005,266.09 и топ-3 книги на рынке 25.09: долг `debt()` и залог по
     слотам. Ветки: долг→0 (РАЗВИЛКА причин, см. ниже), падение долга ≥$5k с пересчётом
     достижимости огня на остатке, движение залога.
  2. МАРКЕР УВОДА — `SetIsAuthorized` на MIDNIGHT с authorizer = наша цель. Механизм,
     которым 26.08 увезли китов 1 и 3: заёмщик уполномочивает хаб tenor 0xdbb74bf8, хаб
     уполномочивает мигратор 0x6bb533cf, исполнитель одним залпом кладёт позицию на
     Morpho Blue. ЛЮБАЯ новая авторизация вне штатного списка Midnight = алерт.
     **ФОРЫ МАРКЕР НЕ ДАЁТ** (оба известных ролла прошли за ~10ч до срока, а авторизации
     стояли с момента входа): он говорит «подключён ли заёмщик к рельсу увода», а НЕ
     «когда уведут». Это единственный сигнал ДО нуля, а не после.
  3. КОНКУРЕНТ 0x5d183c7e — тот, кто РЕАЛЬНО взял окно 27.08 ($398,259.83 из кармана).
     Сегодня: голая EOA (код 0x), ETH 0.000076, USDC $0.00, 13 tx за 14 суток. Оснащение
     под $1M видно заранее: приток USDC, приток ETH, появление кода по адресу (в т.ч.
     делегация EIP-7702) или задеплоенный CREATE-контракт.
  4. ПРЕДВЗВОД — с T−2ч и до конца окна: знает ли ЖИВОЙ бот адрес кита (реестр заёмщиков
     в его state-файле) и охотится ли он (`hot=` в его логе после MN_PREARM_SEC=900).
     Что каждая проверка доказывает, а что нет — сказано в тексте самого алерта.

ЭСКАЛАЦИЯ (правило 11.09). Инбокс агента мёртв с 01.09 (OAuth крон-тракта протух,
чинит владелец), поэтому ДО `maturity + 2ч` все поводы идут ЧЕЛОВЕКУ (`hil=True`), а не
в инбокс, который никто не читает. Чтобы это не превратилось в спам:
  * поводы прогона СОБИРАЮТСЯ и уходят ОДНИМ сообщением (побудка считается сессией, а не
    строкой — [[alerts-only-where-human-acts]]);
  * ЗАЩЁЛКА ПО СОСТОЯНИЮ: ключ дедупа = sha1 отсортированного СОСТАВА поводов, срок 30
    суток. Тот же набор человеку повторно не идёт; новый повод меняет ключ и проходит
    бесплатно ([[escalation-needs-window-and-latch]]: 10.09 один кит уехал владельцу
    трижды, потому что «максимум за историю» не гаснет, а dedup был короче каденции);
  * «всё тихо» — ТОЛЬКО в лог, в TG никогда.
После `maturity + 2ч` поводы снова едут в инбокс агента (hil=False): окно кончилось,
человеку решать нечего.

СНЯТО 11.09 И ПОЧЕМУ:
  * `arm_race()` / `mn-profile.sh` / `current_floor()` — автоперевод в race-профиль писал
    `MN_MIN_PROFIT` в боевой env и РЕСТАРТОВАЛ executor. Его премисса (пол $3,000, профили
    patient/race) умерла 01.09: живой бот работает на дробном поле `MN_HYBRID_FLOOR_BPS=5`
    при `MN_MIN_PROFIT=100`, и «переключить профиль» означало бы сломать боевую политику
    огня. Пороги огня — решение владельца, сторож их не трогает.
  * `floor_reachable()` на модели `net(t)=1.22t−260` и полной позиции $100,214.49 — это
    рампа РОЛЛЕРА 31.07. Заменена на `ramp_ceiling_usd()` по замеренной глубине 25.09.
  * ступени USDC $1,000 и $28,000 — первая будила человека на $1k, вторая считала
    достижимость мёртвого пола $3,000.

Только чтение. Ничего не пишет, кроме своего state (путь переопределяем MN_WATCH_STATE —
дверь, которую стенд 27.08 оставил открытой и снёс боевые защёлки).

cron (каждые 15 минут):
  */15 * * * * flock -n /tmp/mn-target-watch.lock /home/claude-agent/.fleet-watch/target-watch.py \
    >> /home/claude-agent/.fleet-watch/target-watch.log 2>&1
"""
from __future__ import annotations

import hashlib
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
# РЫНОК ОКНА 25.09 15:00Z. Единственный: `maturity 1790348400`, займ USDC (6 знаков),
# РОВНО ОДИН collateralParam — голый cbBTC (8 знаков), bitmap 0x1 у всей книги.
# `enterGate`/`liquidatorGate` = 0x0 (гейтов нет), `maxLif` 1043841336116910229 ⇒ бонус
# 4.3841%. Сверено с `data/midnight_markets.json` и с цепью (PREFLIGHT §1).
MARKET_2509 = "0x549cd072daf99328554f3a6d2d4d6f4a07f1c59369e891e6391946f9cf75f221"
MATURITY_2509 = 1790348400                  # 2026-09-25 15:00:00 UTC

# ЦЕЛИ. Кит + топ-3 = 96.66% книги ($1,421,644.61 / 38 позиций). У всех bitmap 0x1 (голый
# cbBTC — ваулт-класса, который закрыл нам кита-2 27.08, в этой книге НЕТ), путь v1 открыт.
# Хвост (34 позиции на $78 тыс., из них 26 мельче $1,000) под радар не берём: он не меняет
# ни одного решения, а каждая цель — это два лишних eth_call на прогон.
#
# coll_move_pct=0.0 у всех: залог — ГОЛЫЙ cbBTC, начисления на нём нет вовсе (в отличие от
# обёртки кита-2 27.08, ради которой ставился общий допуск 2%), значит любое движение слота
# = действие заёмщика. Под процентным порогом — абсолютный пол на крошку (coll_move_dust):
# 10_000 units = 0.0001 cbBTC ≈ $7.73 по оракулу 11.09. Урок 07.08: при move_pct=0 крошка
# чужого транша (0.0000026 WETH) дала тревогу «+0.00%»; реальный ввод/вывод здесь — сотые
# доли cbBTC и выше, то есть на 2+ порядка над полом.
TARGETS = [
    # 11.09: долг 1005266086395 БИТ-В-БИТ равен снимку 13.08 — позиция неподвижна 29 суток.
    # Залог 23.00000000 cbBTC = $1,777,223.66, LTV 56.56%, HF 1.520 ⇒ до maturity
    # ликвидация невозможна, понадобилось бы падение cbBTC на ≈34.2%. Голая EOA (код 0x),
    # nonce 17, адрес ЖИВОЙ (10.09 18:28Z переводил 0.1 cbBTC из кошелька, залог не тронут).
    {"name": "кит 25.09 $1.005M cbBTC", "market": MARKET_2509,
     "borrower": "0xc6877a65349b0fa45cc61a267ee682c7abf2b369",
     "maturity": MATURITY_2509, "loan_dec": 6, "coll_dec": 8, "coll": "cbBTC",
     "coll_move_pct": 0.0, "coll_move_dust": 10 ** 4},
    {"name": "#2 25.09 $167k cbBTC", "market": MARKET_2509,
     "borrower": "0x0eba5721065f961bced568527bfa03ea9ed61a00",
     "maturity": MATURITY_2509, "loan_dec": 6, "coll_dec": 8, "coll": "cbBTC",
     "coll_move_pct": 0.0, "coll_move_dust": 10 ** 4},
    {"name": "#3 25.09 $120k cbBTC", "market": MARKET_2509,
     "borrower": "0xa5b6cebb8343253c6f4d73333da6d110a3fa9406",
     "maturity": MATURITY_2509, "loan_dec": 6, "coll_dec": 8, "coll": "cbBTC",
     "coll_move_pct": 0.0, "coll_move_dust": 10 ** 4},
    # МАРКЕР TENOR У ЭТОЙ ПОЗИЦИИ УЖЕ СТОИТ (хаб 0xdbb74bf8 авторизован 23.07 15:08Z) —
    # см. AUTH_SEED ниже: он засеян, поэтому известное состояние НЕ звонит при внедрении
    # ([[alerts-only-where-human-acts]] 07.08, ловушка 1: пустая защёлка повторяет звонок,
    # который уже состоялся). Уедет она, по прецеденту, между T−48ч и T−2ч.
    {"name": "#4 25.09 $51k cbBTC (МАРКЕР TENOR стоит)", "market": MARKET_2509,
     "borrower": "0xcab6b18d178502d6e18609a5f7228011cbf34f56",
     "maturity": MATURITY_2509, "loan_dec": 6, "coll_dec": 8, "coll": "cbBTC",
     "coll_move_pct": 0.0, "coll_move_dust": 10 ** 4},
]
# Совместимость с остальным файлом (секции конкурента и чужих ликвидаций): «главная» цель.
MARKET = TARGETS[0]["market"]
BORROWER = TARGETS[0]["borrower"]
MATURITY = MATURITY_2509                    # 2026-09-25 15:00:00 UTC
SEL_DEBT = "0x93af51c2"                     # debt(bytes32,address)
SEL_COLL = "0xecdcc72d"                     # collateral(bytes32,address,uint256)
SEL_COLL_BITMAP = "0xb502e1f9"              # collateralBitmap(bytes32,address)
SEL_BALANCE_OF = "0x70a08231"               # balanceOf(address)
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
LOAN_DEC, COLL_DEC = 6, 8                   # USDC / cbBTC

# КОНКУРЕНТ. 27.07-28.08 радар следил за 0x6cf59693 — оператора, который РЕАЛЬНО взял окно
# 27.08, он не знал. Это он: 0x5d183c7e погасил $398,259.83 из кармана в 00:23:37Z при
# maturity 00:00Z (то есть на +23.6 мин — ПОЗЖЕ нашего t*=+272с).
COMPETITOR = "0x5d183c7ec27e2bc7d07d7c2d6e206ebd667c1c5b"

# Liquidate(address,bytes32,address,uint256,uint256,address,bool,address,address,
#           uint256,uint256,uint256) — indexed: id, collateral, borrower
TOPIC_LIQUIDATE = "0x" + keccak256(
    b"Liquidate(address,bytes32,address,uint256,uint256,address,bool,address,address,"
    b"uint256,uint256,uint256)").hex()

# --- МАРКЕР УВОДА (ветка 2, новая 11.09) ----------------------------------------------
# SetIsAuthorized(address authorizer, address authorized, bool newIsAuthorized, address sender)
# РАСКЛАДКА СВЕРЕНА НА ЦЕПИ, А НЕ ВЫВЕДЕНА ИЗ ПОДПИСИ (позитивный контроль 11.09: блок
# 49197445, tx 0xe280b6ae…, маркер кита-1 27.07):
#   topics[1] = authorizer (indexed) = 0xfb94d340… (кит-1)
#   topics[2] = authorized (indexed) = 0xdbb74bf8… (ХАБ-TENOR)
#   topics[3] = sender     (indexed) = 0xfb94d340…
#   data      = ОДНО слово = bool newIsAuthorized = 0x…01
# Три индексированных поля, а не два: снятие авторизации (data=0) отличимо от выдачи, и
# ревокация НЕ является маркером увода — алертим только на newIsAuthorized=true.
TOPIC_SET_AUTH = "0x" + keccak256(b"SetIsAuthorized(address,address,bool,address)").hex()
# ШТАТНАЯ ИНФРАСТРУКТУРА MIDNIGHT — авторизация этих адресов маркером НЕ является.
# Источник: analysis/midnight_day0 (MIDNIGHT-реестр: BUNDLES + EXTRA-ратификаторы), а не
# список «кого мы видели»: незнание обязано закрывать гард в сторону лишнего алерта, но
# звонить на собственный фронтенд Midnight — это шум по построению.
MIDNIGHT_NATIVE_AUTH = {
    "0x800b5f12a61b8198a5a6efd794cac6699b294d63",   # setterRatifier
    "0x091183d729be9f808c212b475e387a12e67850a7",   # BUNDLES — официальный бандлер
    "0xd6e70365c8e8dda9a4ca662c07bbe663b017755e",   # ecrecoverRatifier
    "0x292bea9f1443d54e0e509120c919106765c6a493",   # ecrecoverAuthorizer
}
# Известные адреса рельса увода — их имена печатаем в алерте, чтобы агент не поднимал
# атрибуцию с нуля. Список НЕ является фильтром: алертим на ЛЮБОЙ адрес вне
# MIDNIGHT_NATIVE_AUTH (оператор tenor уже менял хаб — кит-3 31.08 авторизовал 0x7d4a92f8).
# ЗДЕСЬ ТОЛЬКО АДРЕСА, ВЫПИСАННЫЕ В ПРЕФЛАЙТЕ ЦЕЛИКОМ. Ещё два участника рельса известны
# лишь префиксом (исполнитель ролла 26.08 `0xf913b3bd…`, новый хаб кита-3 от 31.08
# `0x7d4a92f8…`) — дописывать им хвосты НЕЛЬЗЯ: в этом проекте уже дважды фабриковались
# хвосты id, и после этого приёмка id делается только сверкой с источником. Они и не
# нужны: фильтр стоит на MIDNIGHT_NATIVE_AUTH, поэтому оба всё равно дадут алерт как
# «сторонняя авторизация», просто без готового имени.
TENOR_KNOWN = {
    "0xdbb74bf80bd05a1959c8f21e2b821dcfed87104c": "ХАБ-TENOR (тот самый, 101 событие)",
    "0x6bb533cf102d164106a5f83739fd1c0078f3e2da": "мигратор tenor",
}
# ЗАСЕВ ЗАЩЁЛКИ ПРИ ВНЕДРЕНИИ. Состояние на 11.09 уже измерено префлайтом и уже доложено —
# пустая защёлка повторила бы этот доклад алертом на первом же прогоне, причём по позиции
# #4 он был бы ГРОМКИМ («сторонняя авторизация!») и ложным по смыслу («новая!»).
# Источник — PREFLIGHT §2, таблица «маркер tenor по остальной книге» (blockscout, 11.09).
# НАЗВАННЫЙ ПРЕДЕЛ: засев взят ИЗ ДОКУМЕНТА, а не перемерен живым сканом — широкий
# eth_getLogs по всей истории рынка отбивается всеми четырьмя RPC (проверено 11.09:
# base.org 413, publicnode 403, drpc 400, tenderly «Block range too large»), а 288 чанков
# на адрес — штурм публичного RPC. Поэтому дыра закрыта с другой стороны: AUTH_SEED_BLOCK
# стоит на блоке ЗАМЕРА префлайта, и первый же инкрементальный скан перекрывает зазор
# между замером и внедрением.
AUTH_SEED = {
    "0xc6877a65349b0fa45cc61a267ee682c7abf2b369": [
        "0x800b5f12a61b8198a5a6efd794cac6699b294d63",     # setterRatifier, 15.07 18:13Z
        "0x091183d729be9f808c212b475e387a12e67850a7"],    # Bundler, 07.08 17:00Z
    "0x0eba5721065f961bced568527bfa03ea9ed61a00": [
        "0x800b5f12a61b8198a5a6efd794cac6699b294d63"],    # setterRatifier, 28.08
    "0xa5b6cebb8343253c6f4d73333da6d110a3fa9406": [
        "0x091183d729be9f808c212b475e387a12e67850a7"],    # Bundler, 02.09
    "0xcab6b18d178502d6e18609a5f7228011cbf34f56": [
        "0xdbb74bf80bd05a1959c8f21e2b821dcfed87104c"],    # ХАБ-TENOR, 23.07 15:08Z ⚠️
}
AUTH_SEED_BLOCK = 51161260                  # блок замера префлайта (ts 1789111867)
# Скан логов идёт чанками: публичные Base-RPC режут getLogs на ~10k блоков.
AUTH_CHUNK = int(os.environ.get("MN_WATCH_AUTH_CHUNK", "9000"))
# Больше этого за прогон не сканируем: 15-минутный крон отстаёт максимум на ~450 блоков,
# и только долгий простой радара может дать разрыв. Тогда лучше догонять несколько
# прогонов, чем штурмовать публичный RPC одним залпом.
AUTH_MAX_CHUNKS = int(os.environ.get("MN_WATCH_AUTH_MAX_CHUNKS", "6"))

# Рынки, на которых чужой укус означает «игрок работает наш календарь». Окно 25.09 — одно,
# и рынок у него один. После окна список обновить на следующее — иначе снова мёртвая вода
# (ровно то, что случилось с этим файлом между 28.08 и 11.09).
WINDOW_MARKETS = [MARKET_2509]

# Известные ликвидаторы Midnight (включая наш контракт). Любой ДРУГОЙ caller на рынке окна
# = новый игрок, в т.ч. «сиблинг» того же оператора со свежего адреса.
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
# ПУТЬ СТЕЙТА — ПЕРЕОПРЕДЕЛЯЕМ ИЗ ОКРУЖЕНИЯ (27.08). Стенд, замутивший КАНАЛ, всё равно
# писал БОЕВОЙ стейт: json.dump(cur, open(STATE,"w")) — это не транспорт, это другая дверь,
# и MN_WATCH_MUTE её не закрывал. Цена: прогон стенда 27.08 ~01:1xZ снёс защёлки gone_*, и
# следующий крон-прогон (01:15:02Z) прочитал пустое prev и дал ЗАЛП из трёх ложных
# «ДОЛГ ЦЕЛИ → $0: долг $0 → $0» по всем целям сразу. Ровно тот класс, что
# [[tests-never-touch-production-channels]], но по НЕ-КАНАЛЬНОЙ двери: сторож стоял на
# отправке, а состояние осталось голым. Дверь закрыта здесь, у самого пути.
STATE = os.environ.get("MN_WATCH_STATE") or os.path.expanduser("~/.fleet-watch/target-watch.json")
# «видимый/достижимый горизонт»: ближе этого срока до окна конкурент важен. Было 7 суток —
# при перенацеливании 11.09 это означало бы, что ветка конкурента МОЛЧИТ до 18.09, то есть
# ровно в ту неделю, когда оснащение под $1M и стоит ловить заранее. 30 суток покрывают
# окно целиком с запасом.
COMP_HORIZON_SEC = float(os.environ.get("MN_COMP_HORIZON_DAYS", "30")) * 86400

REPAY_ALERT_USD = float(os.environ.get("MN_WATCH_REPAY_USD", "5000"))
COLL_EPS = 1e-6
# порог «залог реально двинулся», % от слота (общий; у целей 25.09 переопределён в 0.0)
COLL_MOVE_PCT = float(os.environ.get("MN_WATCH_COLL_MOVE_PCT", "2"))
# Порог «чужой ликвидатор» в units репея = НАШ СОБСТВЕННЫЙ ПОЛ ОГНЯ (канон
# [[alerts-only-where-human-acts]]: ниже пола мы бы не стреляли никогда ⇒ чужое взятие не
# упущенные деньги, а перепись). 11.09: живой пол бота — `MN_MIN_PROFIT=100` (замер
# /proc/1898665/environ), а не $300 образца 27.07 ⇒ 100_000_000 units USDC.
FOREIGN_MIN_UNITS = int(os.environ.get("MN_WATCH_FOREIGN_UNITS", "100000000"))
ETH_ARM_WEI = int(float(os.environ.get("MN_WATCH_ETH_ARM", "0.02")) * 10 ** 18)
# Ступени USDC. Было три ($1k / $10k / $28k); осталась ОДНА. $1k будил человека на сумму,
# которой $1M-позицию не берут; $28k считал достижимость пола $3,000, которого с 01.09
# не существует. $10k = порог из задачи перенацеливания: столько на кошельке у EOA без
# контракта означает подготовку к repay-из-кармана.
USDC_TIERS = [(10_000, "(≥$10k — хватит на repay-из-кармана по средней позиции)")]

# --- ДОСТИЖИМОСТЬ ОГНЯ НА ОСТАТКЕ (замена floor_reachable) ----------------------------
# Прежняя функция считала по рампе РОЛЛЕРА 31.07 (`full = 100214.49`, `net(t)=1.22t−260`,
# пол $3,000) — три числа, из которых к окну 25.09 не относится ни одно.
#
# Модель 11.09, вся из замеров префлайта:
#   потолок(R) ≈ R × (бонус maxLif 4.3841% − стоимость выхода на размере R) − газ,
#   пол(R)     = max(MN_MIN_PROFIT $100, R × MN_HYBRID_FLOOR_BPS 5/10000)   [полный клип]
# Стоимость выхода — ЗАМЕР (QuoterV2, n=5, интервал ≥30с, блок 51161260), по маршруту,
# который выбирает САМ БОТ (`BaseRouter.quote`, полный перебор); маршрут мигрирует с
# размером, поэтому это таблица, а не константа.
#
# НАЗВАННЫЙ ПРЕДЕЛ, чтобы число не ушло в решение как точное: на полном размере модель
# даёт $30,093 там, где квотер намерил $29,481.44 (+2.1% — бонус maxLif начисляется на
# сейзнутое, а не на репей). Это ОЦЕНКА, а не цена выхода, и к 25.09 глубина изменится:
# контрольные точки T−48ч и T−2ч обязаны перемерить (PREFLIGHT §3).
MAXLIF_BONUS = 0.043841
GAS_PER_SHOT_USD = 0.34                     # 1.3M газа × (basefee 0.005 + 0.1 gwei)
MIN_PROFIT_USD = float(os.environ.get("MN_WATCH_MIN_PROFIT", "100"))
FLOOR_BPS = float(os.environ.get("MN_WATCH_FLOOR_BPS", "5"))
# (репей USD, доля проскальзывания) — строка «что выберет БОТ» из PREFLIGHT §3
SLIP_TABLE = [(125658.26, 0.002478), (251316.52, 0.004495),
              (502633.04, 0.008592), (1005266.09, 0.013905)]


def _w(x: str) -> str:
    return x.lower().replace("0x", "").rjust(64, "0")


def _w0(x: str) -> str:
    """То же, но для ТОПИКА лога: с префиксом 0x. Отдельная функция, а не флаг, потому
    что перепутать их — молчаливо пустой getLogs, а не ошибка."""
    return "0x" + _w(x)


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


def exit_slip(remaining_usd: float) -> float:
    """Доля проскальзывания на выходе для клипа такого размера (замер 11.09, n=5).

    Линейная интерполяция по ЛОГАРИФМУ размера между замеренными точками; за краями
    таблицы — крайнее значение (не экстраполируем: маршрут мигрирует с размером, и
    число с соседнего размера не переносится — прямой урок 28.08→11.09).
    """
    if remaining_usd <= SLIP_TABLE[0][0]:
        return SLIP_TABLE[0][1]
    if remaining_usd >= SLIP_TABLE[-1][0]:
        return SLIP_TABLE[-1][1]
    import math
    for (s0, v0), (s1, v1) in zip(SLIP_TABLE, SLIP_TABLE[1:]):
        if s0 <= remaining_usd <= s1:
            w = (math.log(remaining_usd) - math.log(s0)) / (math.log(s1) - math.log(s0))
            return v0 + w * (v1 - v0)
    return SLIP_TABLE[-1][1]


def ramp_ceiling_usd(remaining_usd: float) -> float:
    """Чистый потолок на ПОЛНОЙ рампе для остатка позиции. Оценка, не цена выхода."""
    if remaining_usd <= 0:
        return 0.0
    return remaining_usd * (MAXLIF_BONUS - exit_slip(remaining_usd)) - GAS_PER_SHOT_USD


def floor_usd(remaining_usd: float) -> float:
    """Пол бота на ПОЛНОМ клипе остатка: max(MN_MIN_PROFIT, клип × MN_HYBRID_FLOOR_BPS)."""
    return max(MIN_PROFIT_USD, remaining_usd * FLOOR_BPS / 10_000)


def reachability_txt(remaining_usd: float) -> str:
    """Одна строка для алерта: остаётся ли остаток целью огня и с каким запасом."""
    ceil_, fl = ramp_ceiling_usd(remaining_usd), floor_usd(remaining_usd)
    # Точка безубытка по $100-плечу пола: ниже неё полный клип не перекрывает пол ни на
    # какой секунде рампы. Считаем, а не вписываем константой.
    breakeven = MIN_PROFIT_USD / max(MAXLIF_BONUS - SLIP_TABLE[0][1], 1e-9)
    if ceil_ > fl:
        return (f"остаток ЕЩЁ ЦЕЛЬ: потолок на полной рампе ≈${ceil_:,.0f} против пола "
                f"${fl:,.0f} (оценка по замеренной глубине 11.09, не цена выхода; "
                f"порог ≈${breakeven:,.0f})")
    return (f"остаток БОЛЬШЕ НЕ ЦЕЛЬ: потолок ≈${ceil_:,.0f} НЕ перекрывает пол "
            f"${fl:,.0f} — бот молча не выстрелит (порог ≈${breakeven:,.0f})")


# --- СБОРЩИК ПОВОДОВ И ЗАЩЁЛКА ПО СОСТАВУ ---------------------------------------------
# Раньше каждая ветка звала notify() сама: 12 точек отправки, каждая со своим (или без
# своего) дедупом. При hil=True это означало бы отдельную побудку ЧЕЛОВЕКА на каждый повод
# в одном прогоне. Теперь поводы копятся и уходят ОДНИМ сообщением с ОДНОЙ защёлкой.
_REASONS: list[tuple[str, str]] = []
# Срок защёлки — 30 суток: заведомо больше и каденции радара (15 мин), и остатка до окна.
LATCH_SEC = float(os.environ.get("MN_WATCH_LATCH_SEC", str(30 * 86400)))
# До этого момента поводы едут ЧЕЛОВЕКУ. Инбокс агента мёртв с 01.09 (OAuth), а окно —
# единственная боевая цель флота; после +2ч от maturity решать человеку нечего.
ESC_UNTIL = MATURITY_2509 + 2 * 3600


def alert(text: str, *, tag: str) -> None:
    """Записать ПОВОД. Отправки здесь нет — она одна, в flush_reasons().

    `tag` — устойчивый отпечаток СОСТОЯНИЯ, породившего повод, а не текста: по составу
    тегов считается ключ защёлки, поэтому в теге не должно быть величин, меняющихся от
    прогона к прогону (голова цепи, время до окна, доля процента). Иначе защёлка
    перестанет защёлкивать, и мы вернёмся к 10.09, когда один факт уехал владельцу трижды.
    """
    _REASONS.append((tag, text))
    log(f"ПОВОД [{tag}] {text}")


def flush_reasons() -> str:
    """Отправить накопленные поводы ОДНИМ сообщением. «Тихо» — только в лог."""
    if not _REASONS:
        log("поводов нет — тихо, TG молчит")
        return "silent"
    tags = sorted({t for t, _ in _REASONS})
    key = "mn2509:" + hashlib.sha1("\n".join(tags).encode()).hexdigest()[:12]
    hil = time.time() < ESC_UNTIL
    body = "\n\n".join(f"{i}. {txt}" for i, (_, txt) in enumerate(_REASONS, 1))
    head = (f"[midnight] РАДАР ОКНА 25.09 — поводов {len(_REASONS)}"
            f"{' (ЧЕЛОВЕКУ: инбокс агента мёртв с 01.09)' if hil else ''}\n\n")
    res = notify(head + body, source="midnight", hil=hil, key=key, dedup_sec=LATCH_SEC)
    if res == "dedup":
        log(f"ЗАЩЁЛКА: состав поводов {tags} уже уходил, повторно не шлю (ключ {key})")
    elif hil and res != "tg":
        # ДОСТАВКА ≠ НАМЕРЕНИЕ ([[tx-hash-is-not-an-effect]]). Хуже того, notify() ставит
        # отметку дедупа ДО отправки: недоставленный HIL глушит СВОЙ СОСТАВ на 30 суток.
        # Лечить семантику notify() этой правкой не берусь — но молчать об этом нельзя,
        # иначе «сторож промолчал» и «сторож не смог» станут неразличимы.
        log(f"⚠️ ДОСТАВКА НЕ ПОДТВЕРЖДЕНА: notify вернул '{res}' при hil=True "
            f"(ключ {key} уже помечен — этот состав поводов не повторится 30 суток)")
    else:
        log(f"отправлено ({res}), поводов {len(_REASONS)}, ключ {key}")
    return res


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


# --- метаданные рынков окна (кэш боевого бота — единственный авторитет по токенам/оракулам)
# ЗАЧЕМ (07.08): сумма чужого взятия делилась на 1e6 ДЛЯ ВСЕХ рынков — это LOAN_DEC главной
# цели, а не рынка события. После перенацеливания радара 06.08 в списке 5 рынков с займом
# WETH (18 знаков): там порог 300_000_000 units не отсекал НИЧЕГО (проходила пыль от
# 3e-10 WETH), а сумма в алерте завышалась в ~10^12. Мина под окно: пять рынков, на которых
# радар кричал бы на каждую пыль и называл её сотнями миллионов долларов.
MARKETS_CACHE = "/home/claude-agent/midnight-liquidator/data/midnight_markets.json"
ORACLE_PRICE_SCALE = 10 ** 36          # IOracle.price(): масштаб Midnight/Blue
SEL_PRICE = "0xa035b1fe"               # price()
# Доллары считаем ТОЛЬКО по долларовым стейблам. EURC сюда сознательно не входит: это евро
# (≈$1.08) — называть его долларами та же ошибка масштаба, просто на 8%, а не на 10^12.
USD_STABLES = {"USDC", "USDT", "DAI", "USDbC"}
# Пол огня в не-долларовом займе: перевести $300 не по чему — котировки самого loan-токена у
# радара нет, а тянуть квотер в вотчер значит завести второй боевой контур ради телеметрии.
# Берём статический эквивалент и честно помечаем: это ТЕЛЕМЕТРИЧЕСКИЙ порог, не решение об
# огне. При сильном движении ETH пересмотреть (MN_WATCH_FOREIGN_WETH).
FOREIGN_MIN_WETH = float(os.environ.get("MN_WATCH_FOREIGN_WETH", "0.15"))  # ≈$290 при ETH $1910
_MKT_META: dict | None = None


def market_meta() -> dict:
    """{id: {loan_sym, loan_dec, usd, floor_units, colls:{token:(oracle,dec,sym)}}} из кэша.

    Тот же файл, что кормит executor (канон: полные id и токены — только из кэша, не руками).
    Нет файла или нет рынка ⇒ {} по этому id, и вызывающий деградирует к прежнему поведению
    (units + пометка «знаки неизвестны»): молчать из-за отсутствия метаданных нельзя.
    """
    global _MKT_META
    if _MKT_META is not None:
        return _MKT_META
    _MKT_META = {}
    try:
        blob = json.load(open(MARKETS_CACHE))
    except Exception as e:  # noqa: BLE001
        log(f"кэш рынков недоступен ({e}) — суммы чужих взятий останутся в units")
        return _MKT_META
    toks = {a.lower(): t for a, t in (blob.get("tokens") or {}).items()}
    for m in blob.get("markets") or []:
        lt = (m.get("loanToken") or "").lower()
        ti = toks.get(lt, {})
        sym = ti.get("symbol") or lt[:10]
        dec = int(ti.get("decimals", 18))
        usd = sym in USD_STABLES
        if usd:
            floor = FOREIGN_MIN_UNITS if dec == 6 else int(300 * 10 ** dec)
        elif sym == "WETH":
            floor = int(FOREIGN_MIN_WETH * 10 ** dec)
        else:                      # прочие не-доллары (EURC): порог в единицах токена
            floor = int(300 * 10 ** dec)
        colls = {}
        for cp in m.get("collateralParams") or []:
            ct = (cp.get("token") or "").lower()
            cti = toks.get(ct, {})
            colls[ct] = (cp.get("oracle"), int(cti.get("decimals", 18)),
                         cti.get("symbol") or ct[:10])
        _MKT_META[(m.get("id") or "").lower()] = {
            "loan_sym": sym, "loan_dec": dec, "usd": usd,
            "floor_units": floor, "colls": colls}
    return _MKT_META


def fmt_loan(units: int, meta: dict) -> str:
    """Сумма в валюте займа РЫНКА СОБЫТИЯ. Доллар печатаем только для долларового стейбла."""
    if not meta:
        return f"{units} units (знаки неизвестны)"
    amt = units / 10 ** meta["loan_dec"]
    if meta["usd"]:
        return f"${amt:,.2f}"
    return f"{amt:,.4f} {meta['loan_sym']}"


def missed_prize_units(f: dict, meta: dict) -> int | None:
    """Упущенный приз В ЕДИНИЦАХ ЗАЙМА = стоимость сейзнутого по оракулу − репей.

    ЭТО ОЦЕНКА ПО ОРАКУЛУ, А НЕ ЦЕНА ВЫХОДА: реальная выручка конкурента ниже на слиппедж и
    газ (урок «цена оракула ≠ цена выхода» — там оценка потока разошлась с фактом в 23 раза).
    Нет оракула/цены ⇒ None: молча завышенное число хуже отсутствующего.

    Отдаёт ЧИСЛО, а не текст (27.08): по этой величине теперь СУДИТ гейт алерта, а судить
    по отформатированной строке нельзя. Форматирование — в missed_prize().
    """
    if not meta or not f.get("seized") or not f.get("collateral"):
        return None
    orc = (meta["colls"].get(f["collateral"]) or (None, None, None))[0]
    if not orc:
        return None
    try:
        price = int(rpc("eth_call", [{"to": orc, "data": SEL_PRICE}, "latest"]), 16)
    except Exception:  # noqa: BLE001 — цена оракула не обязана быть доступной
        return None
    if price <= 0:
        return None
    value = f["seized"] * price // ORACLE_PRICE_SCALE
    return value - f["repaid"]


def missed_prize(f: dict, meta: dict) -> str | None:
    """Тот же приз, отформатированный для текста алерта."""
    units = missed_prize_units(f, meta)
    return None if units is None else fmt_loan(units, meta)


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
                    # topics[2] = токен залога (indexed): нужен, чтобы найти его оракул
                    # и посчитать упущенный приз стоимостью сейзнутого
                    "collateral": ("0x" + lg["topics"][2][-40:]).lower(),
                    "seized": int(data[64:64 * 2], 16),
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
                # ПРИЧИНУ НАЗЫВАЮТ ЛОГИ ПРОТОКОЛА, А НЕ ВЕЛИЧИНА СОСТОЯНИЯ (канон
                # autopsy-verdict-from-protocol-logs; тот же класс, что «фантом vs гонка»
                # 06.08). Прежний текст утверждал «погашен сам» — 26.08 14:07:55Z это
                # оказалось ЛОЖЬЮ ровно в том месте, которое решает планирование: кит-1
                # не погасился, а АТОМАРНО ПЕРЕЕХАЛ на Morpho Base (блок 50481964,
                # tx 0xf44d71e2…617d, мигратор 0x6bb533cf, маркер tenor) — те же
                # 442.73 WETH и те же $399,189 живы на НАШЕМ поле, и «планирование
                # снять» было бы отказом от цели, а не её похоронами. Поэтому сторож
                # говорит ФАКТ и ЧЕМ он измерен, а разбор причины оставляет агенту:
                # у него есть цепь, у сторожа — одно число.
                alert(f"🚨 ДОЛГ ЦЕЛИ → $0: {t['name']} — долг "
                   f"${was:,.0f} → $0 за {left:.1f}ч до maturity. ПРИЧИНА НЕ УСТАНОВЛЕНА "
                   f"(сторож видит только величину): погашение, ликвидация и РЕФИНАНС "
                   f"НА ДРУГОЕ ВЕНЬЮ здесь неотличимы. Разобрать логами блока ДО того, "
                   f"как снимать планирование: ролл меняет market id, поэтому ноль на "
                   f"СТАРОМ рынке ожидаем и у ЖИВОЙ позиции. Прецеденты: 30.07 "
                   f"$100,214 ушли за 24ч55м до срока (настоящее погашение); "
                   f"26.08 кит-1 $399k уехал на Morpho Base целиком (ролл). "
                   f"Остаток книги окна считать заново.",
                   tag=f"debt0:{t['borrower'][:10]}")
                log(f"АЛЕРТ: {t['name']} долг обнулился (было ${was:,.0f})")
            else:
                log(f"{t['name']}: долг ноль — уже сообщал, TG молчит")
        elif p_d is not None and abs(p_d - d_usd) >= REPAY_ALERT_USD:
            # Движение долга крупной цели = смена размера опциона. У position.debt в
            # Midnight аккруала НЕТ вовсе (зеро-купон в units: растёт только добором
            # через Take, падает только Repay/Liquidate — сверено с Midnight.sol 03.08),
            # так что ЛЮБОЕ движение долга = действие заёмщика. Порог $5k — чистая
            # существенность: мелкий добор не меняет планирование на окно.
            # НЕЦЕЛЕВАЯ ПОЗИЦИЯ: РОСТ — В ЛОГ (13.08). Кит-2 выведен из целей огня 06.08
            # (гейт шэров на обёртке — сейз невозможен ни для кого), но продолжал будить
            # агента на КАЖДОМ Tenor-доливе: 07.08, 08.08, 13.08 — три сессии разбора, и все
            # три кончились одним и тем же «плечо долито пропорционально, боя не касается».
            # Разбирать нечего ПО ПОСТРОЕНИЮ: стрелять по этой позиции мы не можем, а рост
            # долга/залога у того, по кому мы не стреляем, — предвестник, не исход
            # ([[alerts-only-where-human-acts]]: предвестник = лог). Гейт по НАПРАВЛЕНИЮ, а не
            # по величине/пропорции: «доля не изменилась» был бы новый допуск на дрейфующей
            # величине — ровно тот класс, что кусал 02.08 и трижды 09.08. Движение ВНИЗ
            # (погашение, вывод залога, исчезновение слота) остаётся побудкой: книга окна
            # меняется, а долг→0 разбирает своя ветка выше независимо от флага.
            if t.get("not_target") and d_usd > p_d:
                log(f"{t['name']}: долг {p_d:,.0f} → {d_usd:,.0f} ({d_usd - p_d:+,.0f}) — "
                    f"рост нецелевой позиции, в лог без побудки")
            else:
                alert(f"{'⚠️' if p_d > d_usd else '📈'} {t['name']}: долг "
                      f"${p_d:,.0f} → ${d_usd:,.0f} ({d_usd - p_d:+,.0f}), до окна "
                      f"{left:.1f}ч. " + (reachability_txt(d_usd) + "."
                                          if p_d > d_usd else
                                          "Рост долга = добор через Take, приз растёт."),
                      tag=f"debt:{t['borrower'][:10]}:{int(d_usd // 1000)}k")
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
            # coll_move_dust (сырые units): абсолютный пол ПОД процентным. При
            # coll_move_pct=0 («любое движение = действие заёмщика», кит-1) КРОШКА
            # чужого транша тоже «движение»: 07.08 Tenor-долив $1,001 (сам по себе ниже
            # по-шагового порога долга $5k, штатно молчит) докинул 0.0000026 WETH — и
            # тревога «+0.00%» разбудила агента на суб-пороговое событие. Пол глушит
            # крошки, не трогая реальные вводы/выводы (они на 2+ порядка выше).
            dust = t.get("coll_move_dust", 0)
            moved = [(k, was[k], now_[k]) for k in set(was) & set(now_)
                     if was[k] and abs(now_[k] - was[k]) / was[k] * 100 > move_pct
                     and abs(now_[k] - was[k]) >= dust]
            # ДВА ЗВОНКА ОБ ОДНОМ СОБЫТИИ. При d==0 позиция закрыта ЦЕЛИКОМ, и слоты
            # исчезают той же tx — 26.08 14:15:02Z это дало вторую тревогу («состав
            # слотов изменился») через секунду после первой, обе про один ролл. Ветка
            # d==0 выше уже сказала всё, что сторож знает; здесь остаётся только шум.
            # Гейт стоит на d, а не на «c_fp == нет»: залог, ВЫВЕДЕННЫЙ ПРИ ЖИВОМ ДОЛГЕ,
            # — сигнал подготовки к погашению и обязан будить по-прежнему.
            if d == 0:
                log(f"{t['name']}: залог {p_c} → {c_fp} при нулевом долге — тот же "
                    f"закрывающий переход, что и ветка выше; второй звонок не шлю")
            elif structural or moved:
                what = ("состав слотов изменился" if structural else
                        "; ".join(f"слот {k}: {100 * (b - a) / a:+.2f}%" for k, a, b in moved))
                # Тот же гейт направления, что и у долга выше: у нецелевой позиции ДОЛИВ
                # залога разбирать нечего, а ВЫВОД и структурное изменение — будят.
                shrunk = any(b < a for _, a, b in moved)
                if t.get("not_target") and not structural and not shrunk:
                    log(f"{t['name']}: залог {p_c} → {c_fp} ({what}) — долив нецелевой "
                        f"позиции, в лог без побудки")
                else:
                    alert(f"🔧 {t['name']}: ЗАЛОГ ДВИНУЛСЯ — {what} "
                          f"({p_c} → {c_fp}), до окна {left:.1f}ч.",
                          tag=f"coll:{t['borrower'][:10]}:"
                              f"{hashlib.sha1(c_fp.encode()).hexdigest()[:8]}")
                    log(f"АЛЕРТ: {t['name']} залог {p_c} → {c_fp} ({what})")
            else:
                d_pct = max((abs(now_[k] - was[k]) / was[k] * 100
                             for k in set(was) & set(now_) if was[k]), default=0.0)
                log(f"{t['name']}: залог +{d_pct:.4f}% — ниже порога "
                    f"({move_pct}% / пыль {dust} units), в лог")
    return st


def _scan_foreign(prev: dict, cur: dict, when: str, head: int) -> None:
    """Скан чужих ликвидаций на рынках окна: перепись адресов + исход «деньги мимо».

    ВЫНЕСЕНО ИЗ main() 27.08 — не ради красоты, а чтобы стенд исполнял НАСТОЯЩИЙ гейт, а
    не заглушку над ним ([[seam-stubbed-above-the-defect]]). Оба дефекта того дня (гейт по
    репею вместо приза; `continue` базлайна, съевший ликвидацию кита-2 на $398k) жили ровно
    здесь и были непокрыты, потому что ветка сидела внутри 200-строчного main().

    Стейт пишет САМА (защёлка seen_callers) — как и раньше, вторым дампом после скана.
    """
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
            # метаданные РЫНКА СОБЫТИЯ: знаки займа, его валюта и пол в её единицах
            meta = market_meta().get(f["market"].lower(), {})
            if c not in seen_callers:
                seen_callers.add(c)
                if baseline:
                    # 27.08: ЗДЕСЬ БЫЛ `continue` — И ОН СЪЕДАЛ ИСХОД ВМЕСТЕ С ПЕРЕПИСЬЮ.
                    # Защёлка базлайна отвечает на вопрос «новый ли это АДРЕС» (перепись), а
                    # «прошли ли мимо ДЕНЬГИ» — вопрос независимый, и одно событие отвечает
                    # на оба. `continue` отдавал вопрос о деньгах вопросу о переписи, и
                    # ровно на первом событии каждого нового адреса.
                    # Цена 27.08 00:30:03Z: события шли по возрастанию блока, первым пришла
                    # ЛИКВИДАЦИЯ КИТА-2 на $398,259.83 (блок 50500435) — она ушла в базлайн
                    # и в лог не попала ВОВСЕ, а крошка $10,063.89 тем же адресом блоком
                    # позже (50500435 уже добавлен в seen_callers) прошла до конца и дала
                    # алерт. Сторож промолчал на крупнейшем событии ночи и разбудил на
                    # мелком; причину обнуления кита-2 пришлось поднимать логами цепи с нуля.
                    # Базлайн гасит ТОЛЬКО свой алерт 🆕, дальше событие идёт общим путём.
                    log(f"базлайн незнакомых: запомнил {c} (первый запуск, без алерта 🆕; "
                        f"исход события считается общим правилом)")
                else:
                    alert(f"🆕 НОВЫЙ ЛИКВИДАТОР на рынке окна: {f['caller']} "
                       f"(первое появление, блок {f['block']}, {fmt_loan(f['repaid'], meta)}). "
                       f"Класс «сиблинг»: 31.07 ждали 0x6cf59693, пришёл 0x2bfc428f. "
                       f"Профиль: python3 -m analysis.midnight_rivals",
                          tag=f"foreign:new:{c}")
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
            # 07.08: порог берётся в единицах ЗАЙМА ЭТОГО РЫНКА. Прежний общий порог в
            # units был верен ровно для 6-значных займов; на пяти WETH-рынках радара он не
            # отсекал ничего (см. шапку market_meta).
            # 27.08: СУДИМ ПО ПРИЗУ, А НЕ ПО РЕПЕЮ. Пол огня — это пол на ЧИСТОМ профите
            # (config.py: min_profit_usd — «floor on NET (profit − gas/tip cost)»), а гейт
            # сравнивал с ним РАЗМЕР ВЛОЖЕНИЯ конкурента. Величины расходятся на порядки:
            # 27.08 00:30Z репей $10,063.89 при призе $23.94, 00:45Z репей $1,003.90 при
            # призе $3.11 — обе тревоги объявили «ВЫШЕ нашего пола $300» то, что ниже пола
            # в 13 и 96 раз. Мы не могли их взять: приз не покрыл бы даже газ.
            # Приз неизвестен (нет оракула) ⇒ судим по репею, как раньше: ошибка в сторону
            # лишнего алерта, а не тишины (незнание не выдаём за «денег не было»).
            floor = meta.get("floor_units", FOREIGN_MIN_UNITS)
            prize_units = missed_prize_units(f, meta)
            judged, judged_by = ((prize_units, "приз") if prize_units is not None
                                 else (f["repaid"], "репей (приз не посчитан)"))
            if judged < floor:
                # post= сохраняем ИМЕННО здесь: пост-maturity взятие — самый ценный след для
                # форензики (кто знает календарь), и он теперь виден только в логе.
                log(f"чужой ликвидатор {f['caller']} ниже пола огня по {judged_by}: "
                    f"{fmt_loan(judged, meta)} (< {fmt_loan(floor, meta)}) "
                    f"при репее {fmt_loan(f['repaid'], meta)} "
                    f"post={f['post']} рынок {f['market'][:14]}… "
                    f"блок {f['block']} — в лог, без алерта")
                continue
            kind = "пост-maturity " if f["post"] else ""
            # УПУЩЕННЫЙ ПРИЗ — то, ради чего этот алерт вообще существует: репей говорит,
            # СКОЛЬКО конкурент вложил, а не сколько заработал. Приз = стоимость сейзнутого
            # по оракулу − репей; помечен как оценка, потому что цена выхода ниже оракульной.
            prize = None if prize_units is None else fmt_loan(prize_units, meta)
            prize_txt = (f" Приз ≈{prize} (оценка по оракулу, не цена выхода)."
                         if prize else " Приз посчитать не удалось (нет цены оракула).")
            alert(f"🏁 ДЕНЬГИ ПРОШЛИ МИМО: {f['caller']} взял {kind}позицию, "
               f"репей {fmt_loan(f['repaid'], meta)} на {f['market'][:14]}… "
               f"(блок {f['block']}, {when}).{prize_txt} "
               f"Судил {judged_by}: {fmt_loan(judged, meta)} ВЫШЕ нашего пола "
               f"{fmt_loan(floor, meta)} — мы могли её взять и не взяли.",
                  tag=f"foreign:prize:{c}:{f['block']}")
            log(f"АЛЕРТ: чужой ликвидатор {f['caller']} блок {f['block']} "
                f"репей={f['repaid']} seized={f.get('seized')} приз={prize} "
                f"post={f['post']} рынок={f['market'][:14]}")
    except Exception as e:  # noqa: BLE001 — скан логов НИКОГДА не валит радар
        log(f"скан чужих ликвидаций не удался (не критично): {e}")
    # Защёлка знакомых адресов пишется ОТДЕЛЬНО и ПОСЛЕ скана: основной стейт сохраняется
    # выше (строка ~315), до этой секции, и без второго дампа список не пережил бы проход —
    # тогда каждый запуск считал бы всех незнакомыми и алерт стал бы вечным.
    if seen_callers != set(prev.get("seen_callers", [])) or baseline:
        cur["seen_callers"] = sorted(seen_callers)
        json.dump(cur, open(STATE, "w"), indent=1)


def scan_authorizations(prev: dict, cur: dict, head: int) -> None:
    """ВЕТКА 2 — МАРКЕР УВОДА. Новые `SetIsAuthorized` от НАШИХ целей на MIDNIGHT.

    Зачем она вообще есть: все остальные ветки узнают об уводе ПОСЛЕ того, как долг стал
    нулём, то есть когда приз уже уехал. Эта — единственная, которая видит возможность
    увода ДО события: увезти позицию может только тот, кого заёмщик уполномочил on-chain.
    У кита 25.09 на 11.09 сторонних авторизаций НЕТ ВООБЩЕ ⇒ увести его сегодня некому.

    ЧЕГО ВЕТКА НЕ ДЕЛАЕТ: не даёт форы и не предсказывает срок. Оба известных ролла
    (26.08, киты 1 и 3) прошли за ~10 часов до maturity, а их авторизации стояли с момента
    входа — «лаг 29.6 суток» это длина срока минус 10 часов, а не запас времени.

    Скан ИНКРЕМЕНТАЛЬНЫЙ от последнего просмотренного блока (`auth_block` в стейте), а не
    с нуля: полная история отбивается всеми публичными RPC (см. AUTH_SEED).
    """
    borrowers = [t["borrower"].lower() for t in TARGETS]
    seen = {k.lower(): set(v) for k, v in (prev.get("auth_seen") or {}).items()}
    if not seen:                                    # первый прогон — засев из префлайта
        seen = {k.lower(): set(v) for k, v in AUTH_SEED.items()}
        log(f"маркер увода: защёлка засеяна из PREFLIGHT §2 "
            f"({sum(len(v) for v in seen.values())} известных авторизаций у "
            f"{len(seen)} заёмщиков), скан с блока {AUTH_SEED_BLOCK}")
    frm = int(prev.get("auth_block", AUTH_SEED_BLOCK)) + 1
    if frm > head:
        cur["auth_seen"] = {k: sorted(v) for k, v in seen.items()}
        cur["auth_block"] = prev.get("auth_block", AUTH_SEED_BLOCK)
        return
    # Догоняем не больше AUTH_MAX_CHUNKS чанков за прогон: разрыв после долгого простоя
    # радара лучше закрыть за несколько прогонов, чем одним залпом по публичному RPC.
    to = min(head, frm + AUTH_CHUNK * AUTH_MAX_CHUNKS - 1)
    scanned = frm - 1
    try:
        b = frm
        while b <= to:
            e = min(b + AUTH_CHUNK - 1, to)
            logs = rpc("eth_getLogs", [{
                "address": MIDNIGHT, "fromBlock": hex(b), "toBlock": hex(e),
                "topics": [TOPIC_SET_AUTH, [_w0(a) for a in borrowers]]}])
            for lg in logs or []:
                who = ("0x" + lg["topics"][1][-40:]).lower()
                auth = ("0x" + lg["topics"][2][-40:]).lower()
                # data = ОДНО слово = bool newIsAuthorized (раскладка сверена на цепи,
                # блок 49197445 — см. шапку TOPIC_SET_AUTH). Снятие авторизации маркером
                # увода НЕ является: это движение в обратную сторону, ему место в логе.
                on = int(lg["data"] or "0x0", 16) != 0
                blk = int(lg["blockNumber"], 16)
                nm = next((t["name"] for t in TARGETS
                           if t["borrower"].lower() == who), who)
                if not on:
                    seen.get(who, set()).discard(auth)
                    log(f"маркер увода: {nm} СНЯЛ авторизацию {auth} (блок {blk}) — "
                        f"движение в обратную сторону, в лог")
                    continue
                if auth in MIDNIGHT_NATIVE_AUTH:
                    seen.setdefault(who, set()).add(auth)
                    log(f"маркер увода: {nm} авторизовал ШТАТНЫЙ адрес Midnight {auth} "
                        f"(блок {blk}) — не маркер, в лог")
                    continue
                if auth in seen.get(who, set()):
                    log(f"маркер увода: {nm} → {auth} уже известна (блок {blk}), в лог")
                    continue
                seen.setdefault(who, set()).add(auth)
                known = TENOR_KNOWN.get(auth)
                alert(f"🚨 МАРКЕР УВОДА: {nm} уполномочил СТОРОННИЙ адрес {auth}"
                      + (f" — это {known}" if known else
                         " — адрес рельса неизвестен, атрибутировать по цепи")
                      + f" (блок {blk}, tx {lg.get('transactionHash')}). Механизм увода "
                        f"26.08: заёмщик авторизует хаб → хаб уполномочивает мигратор → "
                        f"исполнитель одной tx делает Take+WithdrawCollateral и кладёт "
                        f"позицию на Morpho Blue. ФОРЫ ЭТО НЕ ДАЁТ: оба известных ролла "
                        f"прошли за ~10ч до maturity, а авторизации стояли с момента "
                        f"входа. Сигнал означает «увести стало КОМУ», а не «когда».",
                      tag=f"tenor:{who}:{auth}")
            scanned = e
            b = e + 1
    except Exception as ex:  # noqa: BLE001 — скан логов НИКОГДА не валит радар
        log(f"скан маркера увода не удался (не критично, догоним): {ex}")
    cur["auth_seen"] = {k: sorted(v) for k, v in seen.items()}
    cur["auth_block"] = scanned
    if scanned >= frm:
        log(f"маркер увода: просмотрены блоки {frm}…{scanned} "
            f"({scanned - frm + 1}), голова {head}")


EXEC_STATE = os.path.expanduser("~/.midnight-bot/exec_state.json")
EXEC_LOG = os.path.expanduser("~/.midnight-bot/executor.log")
PREARM_SEC = int(os.environ.get("MN_WATCH_PREARM", "900"))      # = MN_PREARM_SEC бота
PREARM_LEAD_SEC = int(os.environ.get("MN_WATCH_PREARM_LEAD", str(2 * 3600)))


def _last_hot() -> int | None:
    """Последнее `hot=N` из хвоста боевого лога. None — прочитать не удалось."""
    try:
        import re
        with open(EXEC_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16384))
            tail = f.read().decode("utf-8", "replace")
        m = re.findall(r"hot=(\d+)", tail)
        return int(m[-1]) if m else None
    except Exception as e:  # noqa: BLE001
        log(f"предвзвод: хвост боевого лога не прочитан ({e})")
        return None


def check_prearm(dt: int) -> None:
    """ВЕТКА 4 — ПРЕДВЗВОД. С T−2ч и до maturity+2ч: знает ли ЖИВОЙ бот нашу цель.

    Урок 28.08 ([[acceptance-reference-not-self-report]]): существование кита и ОХОТА бота
    на него — разные факты, и посмертный снимок покрытия уже один раз соврал. Поэтому
    здесь читается состояние ЖИВОГО процесса, а не наши намерения.

    ЧТО КАЖДАЯ ПРОВЕРКА ДОКАЗЫВАЕТ, названо прямо в тексте алерта:
      * реестр заёмщиков (`exec_state.borrowers[рынок]`) доказывает, что бот ЗНАЕТ адрес.
        Это необходимое условие охоты, но НЕ охота;
      * `hot=` в боевом логе доказывает, что горячий набор не пуст. Но в нём сидит и пыль
        (11.09: hot=21 при нуле целей), поэтому `hot>0` про КИТА не доказывает ничего —
        а вот `hot=0` ПОСЛЕ предвзвода (MN_PREARM_SEC=900) доказывает, что кита там нет.
        Асимметрия названа сознательно: положительный ответ здесь слабее отрицательного.
      * членство в самом hot-наборе поимённо бот наружу не печатает — это НАЗВАННЫЙ
        предел прибора, а не «всё хорошо».
    Битый/недочитанный `exec_state.json` (бот мог писать его в этот момент) — В ЛОГ, без
    алерта: [[red-bench-is-not-a-diagnosis]], сбой чтения не есть наблюдение о боте.
    """
    if not (-2 * 3600 <= dt <= PREARM_LEAD_SEC):
        return
    kit = TARGETS[0]["borrower"].lower()
    try:
        with open(EXEC_STATE) as f:
            st = json.load(f)
    except Exception as e:  # noqa: BLE001
        log(f"предвзвод: exec_state.json не прочитан ({e}) — в лог, без алерта")
        return
    bwr = {k.lower(): [a.lower() for a in (v or [])]
           for k, v in (st.get("borrowers") or {}).items()}
    mine = bwr.get(MARKET_2509.lower(), [])
    if kit not in mine:
        alert(f"🚨 ПРЕДВЗВОД: кита {kit} НЕТ в реестре заёмщиков живого бота по рынку "
              f"25.09 (в реестре {len(mine)} адресов, bwr_block={st.get('bwr_block')}). "
              f"Реестр — необходимое условие охоты: адреса, которого бот не знает, он не "
              f"переоценивает. Это НЕ доказывает, что по остальным он охотится, и НЕ "
              f"заменяет проверку hot-набора.", tag="prearm:registry")
    else:
        log(f"предвзвод: кит в реестре бота ({len(mine)} адресов по рынку 25.09, "
            f"bwr_block={st.get('bwr_block')}) — необходимое условие есть, "
            f"охоту это не доказывает")
    cr, gu = st.get("consec_reverts", 0), st.get("gas_usd", 0.0)
    if cr >= 3 or gu >= 20:
        # Критерий HIL №2: боевой огонь ОСТАНОВЛЕН гардом. Возобновление — решение о
        # капитале, а не техническая починка.
        alert(f"🛑 ПРЕДВЗВОД: гард бота взведён у самого окна — consec_reverts={cr} "
              f"(порог 3), gas_usd=${gu:.2f} (суточный порог $20). Пока он стоит, "
              f"выстрела не будет.", tag="prearm:killswitch")
    if dt <= PREARM_SEC:
        hot = _last_hot()
        if hot == 0:
            alert(f"🚨 ПРЕДВЗВОД: у бота hot=0 внутри окна предвзвода (осталось {dt}с, "
                  f"MN_PREARM_SEC={PREARM_SEC}). Кит обязан был войти в горячий набор — "
                  f"пустой набор доказывает, что не вошёл НИКТО. Обратное неверно: "
                  f"hot>0 про кита не доказывает ничего (в наборе сидит пыль).",
                  tag="prearm:hot0")
        elif hot is None:
            # «НЕ ПРОЧИТАН» ≠ «НЕ ПУСТ». Без этой ветки сбой чтения лога печатался как
            # `hot=None — набор не пуст`, то есть отказ прибора выдавался за наблюдение о
            # боте — ровно то, из-за чего мёртвый сторож хуже отсутствующего.
            log(f"предвзвод: hot НЕ ПРОЧИТАН (осталось {dt}с) — вердикта нет ни в одну "
                f"сторону, в лог")
        else:
            log(f"предвзвод: hot={hot} (осталось {dt}с) — набор не пуст; поимённого "
                f"состава бот не печатает, про КИТА это не доказывает ничего")


def main() -> int:
    comp_usdc = call(USDC, SEL_BALANCE_OF + _w(COMPETITOR))
    comp_eth = int(rpc("eth_getBalance", [COMPETITOR, "latest"]), 16)
    comp_nonce = int(rpc("eth_getTransactionCount", [COMPETITOR, "latest"]), 16)
    # КОД ПО АДРЕСУ КОНКУРЕНТА. 11.09 он голая EOA (`0x`), и именно поэтому 27.08 платил
    # $398k из кармана. Код на EOA появляется двумя путями: адрес стал контрактом или
    # хозяин подписал делегацию EIP-7702 (тогда код выглядит как `0xef0100` + адрес).
    # Оба означают одно: он больше не ограничен своим кошельком.
    comp_code = rpc("eth_getCode", [COMPETITOR, "latest"]) or "0x"
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
    tgt_state = watch_targets(prev)
    cur = {**tgt_state,
           "comp_usd": cu, "comp_eth": comp_eth, "comp_nonce": comp_nonce,
           "comp_code_len": len(comp_code),
           "head": head, "ts": now}
    # Маркер увода — ДО записи стейта: `auth_block`/`auth_seen` обязаны лечь в тот же дамп,
    # иначе каждый прогон пересканировал бы один и тот же диапазон.
    scan_authorizations(prev, cur, head)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(cur, open(STATE, "w"), indent=1)

    log(f"конкурент USDC ${cu:,.2f} ETH {comp_eth/1e18:.6f} nonce {comp_nonce} "
        f"код {len(comp_code)} симв.")

    when = f"до окна {dt/3600:.1f}ч" if dt > 0 else f"ПОСЛЕ окна +{-dt/60:.0f}мин"

    if not prev:
        # ПЕРВЫЙ ЗАПУСК = БАЗЛАЙН. Сравнивать не с чем, и все ветки «было → стало» на пустом
        # prev дают ложные переходы (27.08: залп из трёх «ДОЛГ ЦЕЛИ → $0: $0 → $0»).
        # НО НЕ ВСЕ поводы — сравнения: маркер увода засеян ИЗ ДОКУМЕНТА, а предвзвод
        # читает живого бота. Эти два вердикта осмысленны и на первом прогоне, и глушить
        # их базлайном значило бы построить тест, способный вернуть только ноль.
        check_prearm(dt)
        kept = [(t, x) for t, x in _REASONS
                if t.startswith("tenor:") or t.startswith("prearm:")]
        dropped = len(_REASONS) - len(kept)
        _REASONS[:] = kept
        log(f"базовая линия записана (сравнительных поводов отброшено {dropped}; "
            f"маркер увода и предвзвод судят без базлайна)")
        flush_reasons()
        return 0

    p_u = prev.get("comp_usd", cu)
    p_e = prev.get("comp_eth", comp_eth)
    p_n = prev.get("comp_nonce", comp_nonce)
    p_code = prev.get("comp_code_len", len(comp_code))

    # --- 3. оснащение конкурента ------------------------------------------------------
    # ГЕЙТ (решение kelbic 30.07): следим за конкурентом ТОЛЬКО когда есть цель на видимом
    # горизонте. Иначе алерты бессмысленны: nonce — прокси активности, а не угрозы.
    # Гейт снимается САМ, как только у цели снова появится долг и окно окажется близко.
    target_alive = any(not tgt_state.get(f"gone_{i}", False) for i in range(len(TARGETS)))
    horizon_ok = -2 * 3600 <= dt <= COMP_HORIZON_SEC
    watch_comp = target_alive and horizon_ok
    if not watch_comp:
        why = "цели нет" if not target_alive else f"окно дальше {COMP_HORIZON_SEC/86400:.0f}д"
        log(f"конкурент: наблюдение выключено ({why}) — nonce={comp_nonce} "
            f"ETH={comp_eth/1e18:.6f} USDC=${cu:,.2f} (в лог, без TG)")

    if watch_comp and len(comp_code) > 2 >= p_code:
        kind = ("делегация EIP-7702" if comp_code[:8].lower() == "0xef0100"
                else "контракт по адресу")
        alert(f"🚨🚨 КОНКУРЕНТ ОСНАСТИЛСЯ: по {COMPETITOR} появился код "
              f"({len(comp_code)} симв., похоже на {kind}), {when}. 27.08 он платил "
              f"$398,259.83 ИЗ КАРМАНА, потому что был голой EOA; с кодом ему доступен "
              f"флешлоун, то есть $1M без капитала.", tag="rival:code")

    if watch_comp and comp_eth >= ETH_ARM_WEI > p_e:
        alert(f"⛽ КОНКУРЕНТ ЗАПРАВЛЯЕТСЯ: ETH {p_e/1e18:.5f} → {comp_eth/1e18:.5f} "
              f"у {COMPETITOR[:10]}… ({when}). Газ нужен и под repay-из-кармана, и под "
              f"деплой контракта.", tag="rival:eth")

    if watch_comp and comp_nonce != p_n:
        # nonce САМ ПО СЕБЕ — предвестник, а не исход: 0.9 tx/сутки у этого адреса будили
        # бы человека через день на ровном месте ([[alerts-only-where-human-acts]]).
        # Будит только НАХОДКА: задеплоенный контракт по детерминированному CREATE-адресу
        # (keccak(rlp([addr,nonce]))[12:]) — он виден ДО первого использования.
        deployed = []
        for n in range(p_n, comp_nonce):
            addr = _rlp_create(COMPETITOR, n)
            code = rpc("eth_getCode", [addr, "latest"])
            if code and code != "0x":
                deployed.append(addr)
        if deployed:
            alert(f"🚨🚨 КОНКУРЕНТ ЗАДЕПЛОИЛ КОНТРАКТ: {', '.join(deployed)} ({when}). "
                  f"На Base флешлоун даёт $1M БЕЗ капитала — с контрактом он боеспособен "
                  f"независимо от баланса.", tag=f"rival:create:{deployed[0]}")
        else:
            log(f"конкурент активен: nonce {p_n} → {comp_nonce}, контракта среди этих tx "
                f"нет — предвестник, в лог без побудки")

    for lvl, label in USDC_TIERS:
        if watch_comp and cu >= lvl > p_u:
            alert(f"🥊 КОНКУРЕНТ ФОНДИРУЕТСЯ {label}: USDC ${p_u:,.0f} → ${cu:,.0f} "
                  f"({when}). 11.09 у него было $0.00, а 27.08 он погасил $398,259.83 из "
                  f"кармана — приток USDC есть подготовка к repay-из-кармана.",
                  tag=f"rival:usdc:{lvl}")

    check_prearm(dt)
    _scan_foreign(prev, cur, when, head)
    flush_reasons()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
