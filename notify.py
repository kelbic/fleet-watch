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

КОНТРОЛЬ ТРУБЫ ЗАКРЫВАЕТ СЕБЯ САМ (21.08). Сторож threads-watch проверил свою трубу
живым вызовом notify.sh с обычным ключом: текст честно говорил «тест, игнорировать», но
запись легла в БОЕВУЮ очередь и через 19 минут подняла сессию разбора — то есть контроль
оплачен настоящим подъёмом. Тот же класс уже был 20.08 (две тестовые тревоги разбудили
сессию как настоящие). Поэтому у контроля теперь свой адресат: selftest=True или ключ на
':selftest' кладёт запись СРАЗУ В АРХИВ разобранного (agent-inbox.handled.jsonl,
handled=True, outcome=selftest) — след остаётся, труба проверена до самого файла, живая
очередь и TG не трогаются. Признак ЯВНЫЙ и только от вызывающего: происхождение прогона
(__main__, env, путь файла) отправителя не доказывает — это перебрано и закрыто 13-14.08.
Забыть пометку безопасно: получишь настоящую тревогу, а не молчание.

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
# Архив РАЗОБРАННОГО (тот же файл, что читает agent-wake). Выводится из INBOX, а не
# прибивается путём: иначе тест с FLEET_INBOX=<временный> писал бы в боевой архив.
HANDLED_ARCHIVE = (INBOX[:-len(".jsonl")] if INBOX.endswith(".jsonl") else INBOX) + ".handled.jsonl"
SELFTEST_SUFFIX = ":selftest"
# ДЕДУП-СОСТОЯНИЕ — ПЕРЕОПРЕДЕЛЯЕМО ИЗ ОКРУЖЕНИЯ (11.09). Замутить транспорт НЕДОСТАТОЧНО:
# _dedup_hit() пишет ключ в ЭТОТ файл ещё до всякой отправки, и стенд, гоняющий защёлку,
# засевал бы БОЕВЫЕ ключи — то есть глушил бы настоящую тревогу того же состава на весь
# TTL. Ровно та же НЕ-КАНАЛЬНАЯ дверь, что 27.08 у target-watch (сторож стоял на отправке,
# а состояние осталось голым и снесло защёлки gone_*), только с обратным знаком: не стенд
# теряет защёлку, а бой её получает. Дверь закрыта у самого пути
# ([[tests-never-touch-production-channels]]: границу держит транспорт, а не дисциплина).
DEDUP_STATE = os.environ.get("FLEET_DEDUP_STATE") or os.path.join(WATCH_DIR, "notify-dedup.json")
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
           dedup_sec: float = 0.0, selftest: bool = False) -> str:
    """Единственная точка отправки тревог. Возвращает адресата: tg|inbox|dedup|muted|selftest.

    hil=False (умолчание) — в инбокс агента: он проснётся, разберёт и починит.
    hil=True — человеку в TG; критерий в шапке модуля, четыре случая.
    selftest=True (или ключ, кончающийся на ':selftest') — КОНТРОЛЬ ТРУБЫ: запись едет
    сразу в АРХИВ разобранного, минуя живую очередь и TG. Причина — в шапке модуля.
    """
    if selftest or key.endswith(SELFTEST_SUFFIX):
        rec = {"ts": int(time.time()),
               "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "source": source, "hil": hil, "key": key,
               "text": text if text.startswith(f"[{source}]") else f"[{source}] {text}",
               "attempts": 0, "handled": True, "outcome": "selftest"}
        # мьют держит границу и здесь: тестовый прогон не пачкает боевой архив
        arch = HANDLED_ARCHIVE + (".muted" if muted() else "")
        try:
            with open(arch, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            _log(f"notify[{source}]: контроль не записан в архив: {e}")
        _log(f"notify[{source}] контроль трубы → архив (никого не будит): {text[:120]}")
        return "selftest"

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
    if ok:
        # Запись легла в инбокс ДО отправки (падение посреди _tg не теряет тревогу), но
        # доставленное человеку агент разбирать не должен — иначе каждый HIL бумерангом
        # будит сессию на собственную эскалацию (07.08: рапорт base-liquidator ушёл в TG
        # и следом поднял агента). Помечаем доставку добавочной handled-копией: инбокс
        # append-only для всех, кроме agent-wake (он схлопывает пары merged-перезаписью);
        # переписывать файл отсюда нельзя — параллельный notify() дописывает в него же.
        try:
            with open(path, "a") as f:
                f.write(json.dumps({**rec, "handled": True, "outcome": "tg"},
                                   ensure_ascii=False) + "\n")
        except OSError as e:
            _log(f"notify[{source}]: tg-надгробие не записано: {e}")
    return "tg" if ok else "inbox"


def resolve(key: str, text: str, match: str = "", ts: int = 0) -> bool:
    """Записать ВЫВОД разбора в поле `resolution` своей тревоги в инбоксе.

    Существует потому, что вывод разбора терялся ДВАЖДЫ (20.08 и 22.08) одинаково: сессию
    убивал таймаут 1800s, agent-wake архивирует запись только на чистом выходе, и в архиве
    оставалось «outcome: agent» без единого слова о том, что выяснено. Разбор без записанного
    вывода = разбора не было. Поэтому: писать СРАЗУ, черновиком, ДО долгих проверок, и
    уточнять по ходу — переживший таймаут черновик полезнее не дожившего до записи финала.

    Переписывает инбокс атомарно (tmp + os.replace) ОТ СВЕЖЕГО ЧТЕНИЯ, а не от снапшота.
    Названный предел: notify(), дописавший строку в зазор между чтением и заменой, будет
    потерян — зазор миллисекундный против минут разбора, но он есть. Дописывать патч-строкой
    нельзя: _rewrite_merged() в agent-wake отображает патч на КАЖДУЮ копию ident'а, и на
    выходе по таймауту дубль размножил бы тревогу вместо того, чтобы донести вывод.
    Структурное лечение (sidecar-файл резолюций, append-only) — отдельной правкой, с тестом.

    ПУСТОЙ КЛЮЧ — НЕ АДРЕС (27.08). Сторожа midnight/window-watch зовут notify() без key,
    и в инбоксе одновременно лежало ЧЕТЫРЕ записи с key="": resolve("", ...) записал бы
    один вывод во все четыре разом и стёр бы вывод предыдущей сессии по window-watch-2708.
    Поэтому есть уточнители, оба необязательные (старое поведение по умолчанию):
      match — подстрока, обязанная встретиться в text записи;
      ts    — точная метка времени записи.
    Уточнители СУЖАЮТ, а не расширяют: запись обязана удовлетворить всем заданным.
    Если после сужения подходит БОЛЬШЕ ОДНОЙ записи — вывод НЕ пишется и возвращается
    False: молчание лучше вывода, разлитого по чужим тревогам (там, где адрес неоднозначен,
    «записал» неотличимо от «затёр»).

    Возвращает True, если ровно одна неразобранная запись найдена и обновлена.
    """
    path = INBOX + (".muted" if muted() else "")
    try:
        with open(path) as f:
            recs = [json.loads(ln) for ln in f if ln.strip()]
    except (OSError, ValueError) as e:
        _log(f"resolve[{key}]: инбокс не прочитан: {e}")
        return False
    sel = [r for r in recs
           if r.get("key") == key and not r.get("handled")
           and (not match or match in (r.get("text") or ""))
           and (not ts or r.get("ts") == ts)]
    if not sel:
        _log(f"resolve[{key}|{match}|{ts}]: неразобранной записи нет — вывод НЕ записан")
        return False
    if len(sel) > 1 and (match or ts or not key):
        # Неоднозначный адрес: писать во все — значит затирать чужие выводы.
        _log(f"resolve[{key}|{match}|{ts}]: подходит {len(sel)} записей — вывод НЕ записан")
        return False
    for r in sel:
        r["resolution"] = text
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except OSError as e:
        _log(f"resolve[{key}]: инбокс не записан: {e}")
        return False
    _log(f"resolve[{key}]: вывод записан ({len(text)} симв.)")
    return True
