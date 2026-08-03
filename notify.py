"""Маршрутизация тревог флота: по умолчанию будим АГЕНТА, человека — только если без
него нельзя.

Решение владельца 03.08: «алерты могут будить тебя, а не эскалироваться на меня; алерт
нужен только если нужен HIL». Это ужесточение старого правила («алерт только там, где
нужно действие человека»): раньше выбор был «слать в TG или в лог», и всё, что требовало
РАЗБОРА, ехало к человеку за неимением другого адресата. Теперь адресат есть — инбокс
агента, который читает `agent-wake.sh`.

КРИТЕРИЙ HIL (hil=True). Ровно четыре случая, всё остальное — инбокс:
  1. Нужно то, чего у агента НЕТ: подпись владельца на капитал/газ, приватный ключ,
     внешний доступ, оплата, вход в чужой аккаунт.
  2. Боевой огонь ОСТАНОВЛЕН (kill-switch, сработавший гард): возобновление —
     решение о капитале, а не техническая починка.
  3. Сломана сама автоматика доставки: агента не удаётся разбудить, инбокс не
     разбирается N раз подряд. Иначе тишина инбокса неотличима от «всё спокойно»
     (урок 01.08: мёртвый сторож хуже отсутствующего).
  4. Необратимое или внешнее действие, которое агент не вправе совершить сам.

Не HIL, даже если выглядит страшно: конкурент задеплоил контракт, залог кита двинулся,
чужой ликвидатор забрал деньги, бот завис, профиль не переключился. Всё это агент
разбирает и чинит; человеку там нечего решать, пока дело не упрётся в пункты 1-4.

Сторож НА ТРАНСПОРТЕ: FLEET_ALERT_MUTE=1 (или MN_WATCH_MUTE=1 — совместимость с
target-watch) закрывает канал к человеку И уводит инбокс в файл-дублёр, чтобы тест не
пачкал боевую очередь. Правило то же, что 19.07: границу держит транспорт, а не
дисциплина вызывающего.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

WATCH_DIR = os.path.expanduser("~/.fleet-watch")
INBOX = os.environ.get("FLEET_INBOX", os.path.join(WATCH_DIR, "agent-inbox.jsonl"))
DEDUP_STATE = os.path.join(WATCH_DIR, "notify-dedup.json")
LOG_FILE = os.path.join(WATCH_DIR, "notify.log")
TG_ENV = os.path.expanduser("~/.claude/channels/telegram/.env")
CHAT_ID = os.environ.get("MN_CHAT_ID", "265715923")


def muted() -> bool:
    """True — транспорт к человеку обязан молчать.

    Сторож стоит на транспорте, а не на дисциплине вызывающего (урок 19.07: заглушка,
    которую каждый тест обязан не забыть, границу не держит — первый забывший достучался
    до человека). notify — НОВЫЙ, третий отправитель в тот же чат, поэтому автоопределение
    теста тут обязано быть своё: mute-переменные вызывающих (MN_MUTE_TG у executor,
    MN_WATCH_MUTE у target-watch) на него бы не распространились.

    По умолчанию БЕЗОПАСНО: любой признак тестового прогона → канал закрыт.
    """
    import sys
    for var in ("FLEET_ALERT_MUTE", "MN_WATCH_MUTE", "MN_MUTE_TG"):
        v = os.environ.get(var)
        if v == "1":
            return True
        if v == "0":                       # явный opt-in теста текста алерта
            return False
    # два признака, а не один: раннер `python3 -m bot.test_executor` грузит сьют как
    # __main__ и сканом sys.modules не ловится (ровно эта слепота выпустила фикстуры).
    main_file = os.path.basename(getattr(sys.modules.get("__main__"), "__file__", "") or "")
    if main_file.startswith("test_"):
        return True
    # `unittest` В ЭТОТ СПИСОК НЕ ВХОДИТ намеренно: его тянут посторонние библиотеки, и
    # ложное молчание тут дороже ложной отправки — непрошедший HIL неотличим от тишины
    # «всё спокойно» (урок 01.08: мёртвый сторож хуже отсутствующего).
    if "pytest" in sys.modules:
        return True
    return any(n.startswith("test_") or ".test_" in n for n in list(sys.modules))


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _tg(text: str) -> bool:
    try:
        token = None
        with open(TG_ENV) as f:
            for ln in f:
                if ln.startswith("TELEGRAM_BOT_TOKEN="):
                    token = ln.split("=", 1)[1].strip()
        if not token:
            _log("notify: нет TELEGRAM_BOT_TOKEN — HIL-алерт НЕ доставлен")
            return False
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        r = json.loads(urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                   data=data), timeout=20).read())
        return bool(r.get("ok"))
    except Exception as e:  # noqa: BLE001
        _log(f"notify: отправка HIL упала: {e}")
        return False


def _dedup_hit(key: str, ttl: float) -> bool:
    """True = такой ключ уже клали недавно. Состояние чистится от протухшего, иначе файл
    растёт вечно на часто повторяющихся ключах."""
    if not key or ttl <= 0:
        return False
    now = time.time()
    try:
        st = json.load(open(DEDUP_STATE))
    except Exception:  # noqa: BLE001
        st = {}
    st = {k: v for k, v in st.items() if now - v < max(ttl, 86400)}
    hit = key in st and now - st[key] < ttl
    if not hit:
        st[key] = now
        tmp = DEDUP_STATE + ".tmp"
        try:
            json.dump(st, open(tmp, "w"))
            os.replace(tmp, DEDUP_STATE)
        except OSError:
            pass
    return hit


def notify(text: str, *, source: str, hil: bool = False, key: str = "",
           dedup_sec: float = 0.0) -> str:
    """Единственная точка отправки тревог. Возвращает адресата: tg|inbox|dedup|muted.

    hil=False (умолчание) — в инбокс агента: он проснётся, разберёт и починит.
    hil=True — человеку в TG; критерий в шапке модуля, четыре случая.
    """
    if _dedup_hit(key, dedup_sec):
        _log(f"notify[{source}] dedup ({key}): {text[:80]}")
        return "dedup"

    # Весь флот пишет в ОДИН чат, поэтому каждая строка называет автора: строка без
    # подписи неатрибутируема (жалоба владельца 19.07). Идемпотентно — не двоит метку.
    pre = f"[{source}]"
    if not text.startswith(pre) and f"[{source}]" not in text.split("\n")[0]:
        text = f"{pre} {text}"

    rec = {"ts": int(time.time()),
           "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "source": source, "hil": hil, "key": key, "text": text,
           "attempts": 0, "handled": False}
    path = INBOX + (".muted" if muted() else "")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        _log(f"notify[{source}]: инбокс не записан: {e}")

    if not hil:
        _log(f"notify[{source}] → инбокс агента: {text[:120]}")
        return "inbox"
    if muted():
        _log(f"[tg muted] HIL[{source}] {text}")
        return "muted"
    ok = _tg(text)
    _log(f"notify[{source}] → HIL человеку: отправлен={ok}: {text[:120]}")
    return "tg" if ok else "inbox"
