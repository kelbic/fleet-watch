#!/usr/bin/env python3
"""Будильник агента: разбирает инбокс тревог, поднимая headless-сессию Claude.

Вторая половина решения 03.08 («алерты будят тебя, а не эскалируются на меня»). notify.py
кладёт ВСЁ в ~/.fleet-watch/agent-inbox.jsonl (HIL — до отправки в TG, чтобы падение
посреди отправки не теряло тревогу; при доставке следом ложится handled-копия, и пара
схлопывается здесь). Этот скрипт по крону смотрит, есть ли неразобранное, и если есть —
поднимает сессию, которая чинит.

ГАРДЫ (иначе будильник сам станет источником вреда):
  • частота: не чаще WAKE_GAP_SEC (умолчание 1ч). Тревоги копятся и разбираются пачкой —
    цель не «мгновенно», а «не потерять».
  • попытки: счётчик на записи. После MAX_ATTEMPTS неудач элемент закрывается и уходит
    ЧЕЛОВЕКУ — это пункт 3 критерия HIL: сломана сама автоматика, и её молчание иначе
    неотличимо от «всё спокойно» (урок 01.08).
  • счётчик растёт ДО запуска сессии: если она падает жёстко (OOM, таймаут), элемент не
    зациклится навечно.
  • выключатель: FLEET_WAKE=0.

Ставится в crontab с flock:
  */20 * * * * flock -n /tmp/fleet-agent-wake.lock python3 ~/.fleet-watch/agent-wake.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser("~/.fleet-watch"))
from notify import notify, INBOX, _log  # noqa: E402

WATCH_DIR = os.path.expanduser("~/.fleet-watch")
HANDLED = os.path.join(WATCH_DIR, "agent-inbox.handled.jsonl")
STAMP = os.path.join(WATCH_DIR, ".agent-wake-stamp")
SESSION_LOG = os.path.join(WATCH_DIR, "agent-wake-sessions.log")
CLAUDE = os.path.expanduser("~/.local/bin/claude")

WAKE_GAP_SEC = float(os.environ.get("FLEET_WAKE_GAP", "3600"))
MAX_ATTEMPTS = int(os.environ.get("FLEET_WAKE_ATTEMPTS", "3"))
MAX_ITEMS = int(os.environ.get("FLEET_WAKE_MAX_ITEMS", "25"))
SESSION_TIMEOUT = int(os.environ.get("FLEET_WAKE_TIMEOUT", "1800"))

# Модель сессии разбора ПРИБИТА здесь, а не берётся из интерактивного дефолта
# ~/.claude/settings.json. 10.08 дефолт стоял на модели с выбранной квотой, и 29 часов подряд
# подъём падал за 5 секунд одной строкой «You've reached your … limit»: сторожа исправно писали,
# разбора не было ни разу, три тревоги закрылись как «escalated-to-human», не будучи прочитанными.
# Цепочка, а не одно имя: квота — величина ПЕР-МОДЕЛЬНАЯ, запасная нужна ровно на этот случай.
WAKE_MODELS = [m.strip() for m in
               os.environ.get("FLEET_WAKE_MODELS", "opus,sonnet").split(",") if m.strip()] or ["opus"]

# Подписи «сессия НЕ ЗАПУСТИЛАСЬ» — это не провал разбора, а его ОТСУТСТВИЕ, и попытку тратить
# на него нельзя (иначе очередь молча стекает человеку без единого прохода по цепи). Класс отказа
# идёт в ключ дедупа: смена причины (квота→доступ) обязана позвонить заново — отпечаток с сигнала.
# Класс отказа опознаём ПО ФОРМЕ сообщения, а не по списку написаний. 17-19.08 список из
# четырёх подстрок («reached your», «usage limit», …) прошёл мимо живого текста
# «You've hit your weekly limit · resets Aug 19, 5pm (UTC)»: вендор сменил и глагол, и
# прилагательное. Цена промаха не «не опознали причину», а ОТМЕНА ЗАПАСНОЙ МОДЕЛИ: ветка
# `if not blocked: break` вышла из цепочки после первой же модели, sonnet не пробовался НИ
# РАЗУ за шесть подъёмов, попытки сгорели, и владельцу дважды ушло ночное «агент не
# поднимается» без причины — при живой квоте на второй модели.
# Суита при этом была зелёной: её фикстура QUOTA списана с того же написания, что и список
# подстрок, — тест и продукт делили одно допущение и вместе не видели боя. Поэтому фикстуры
# теперь берутся из agent-wake-sessions.log (эталон), а совпадение ищется по форме:
# глагол исчерпания рядом с мерой, либо оговорка про сброс — у работающей сессии их не бывает.
_START_FAILURES = (
    ("quota", (
        # якорь на «you/your» обязателен: без него короткий отчёт самой сессии
        # («rate limit config … reset the counter») читался как квота, а ложная квота
        # ВОЗВРАЩАЕТ попытку — то есть настоящий отказ крутился бы, не эскалируясь.
        re.compile(r"\byou\b[^.\n]{0,30}\b(hit|reached|exceeded|out of|ran out of)\b"
                   r"[^.\n]{0,40}\b(limit|quota|credits?)\b"),
        re.compile(r"\byour\b[^.\n]{0,30}\b(limit|quota)\b[^.\n]{0,60}\bresets?\b"),
        re.compile(r"usage-credits|credit balance"),      # исторические написания
    )),
    ("auth", (
        re.compile(r"invalid api key|please run /login|not logged in|"
                   r"unauthorized|authentication_error"),
    )),
)
START_FAIL_MAXLEN = 500


def _start_failure(rc: int, out: str) -> str | None:
    """Класс отказа, если сессия не запускалась вовсе, иначе None.

    Требуем И ненулевой код, И короткий вывод: длинный вывод при rc≠0 — это сессия, которая
    работала и упала (её попытка сгорает честно), а не отказ старта; своё же слово «limit»
    внутри полноценного разбора не должно читаться как отказ. Умолчание консервативное:
    неопознанный отказ считаем работой — вечный цикл хуже одной лишней потраченной попытки.
    """
    if rc == 0 or len(out) > START_FAIL_MAXLEN:
        return None
    low = out.lower()
    for cls, pats in _START_FAILURES:
        if any(p.search(low) for p in pats):
            return cls
    return None


def _run_session(prompt: str, model: str) -> tuple[int, str]:
    cmd = [CLAUDE, "-p"] + (["--model", model] if model else []) + [prompt]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=SESSION_TIMEOUT, cwd=os.path.expanduser("~"))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"таймаут {SESSION_TIMEOUT}s"
    except Exception as e:  # noqa: BLE001
        return 1, f"запуск упал: {e}"

PROMPT_HEAD = """Ты разбираешь накопившиеся тревоги флота ликвидаторов (проснулся по крону,
человек НЕ ждёт у экрана). Тревоги ниже пришли из сторожей и НЕ эскалировались владельцу —
предполагается, что ты разберёшься сам.

Что сделать по каждой: установить факт по цепи/логам (не по тексту тревоги — он может
врать, прецеденты были), починить, если починка в твоей власти, и записать вывод.

ГРАНИЦЫ, которые не переходить без явного «го» владельца:
  • никаких live-транзакций и никаких правок боевого конфига/env боевых ботов;
  • не трогать удалённый Morpho-ликвидатор на 185.173.146.134 (Charlotte);
  • никакого force-push и деструктивных операций (обычный push/merge разрешены);
  • не поднимать пороги огня и не распоряжаться капиталом.
Диагностика, чтение цепи, правка сторожей/аналитики, тесты, коммиты — можно.

Если по тревоге НУЖЕН человек (подпись на капитал, ключ, доступ, остановленный боевой
огонь) — вызови:
  python3 -c "import sys; sys.path.insert(0,'$HOME/.fleet-watch'); from notify import notify; \\
              notify('текст', source='agent-triage', hil=True)"
Не пиши владельцу иначе — он просил не будить его тем, что можешь решить сам.

ТРЕВОГИ:
"""


def _read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def _rewrite(path: str, recs: list[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _append(path: str, recs: list[dict]) -> None:
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ident(r: dict) -> tuple:
    return (r.get("ts"), r.get("source"), r.get("key"), r.get("text"))


def _rewrite_merged(drop: list[dict], patch: list[dict] | None = None) -> None:
    """Переписать инбокс от СВЕЖЕГО чтения, а не от снапшота main(): drop — закрытые
    записи (убрать), patch — записи с обновлёнными полями (attempts). Сессия разбора идёт
    минуты-десятки минут, и notify() за это время дописывает в инбокс новые тревоги —
    перезапись стейл-снапшотом их молча теряла (07.08: тревога midnight легла в инбокс
    через 5 минут после подъёма сессии и была бы стёрта на её выходе). Потерянная тревога =
    класс «мёртвый сторож»: молчание неотличимо от спокойствия."""
    dropset = {_ident(r) for r in drop}
    patches = {_ident(r): r for r in (patch or [])}
    fresh = _read(INBOX)
    out = [patches.get(_ident(r), r) for r in fresh if _ident(r) not in dropset]
    _rewrite(INBOX, out)


def main() -> int:
    if os.environ.get("FLEET_WAKE") == "0":
        return 0
    recs = _read(INBOX)
    # HIL-записи, доставленные в TG, notify() помечает добавочной handled-копией
    # (append-only, см. комментарий там). Схлопываем пары: доставленное человеку агент
    # не разбирает — в архив с outcome=tg-delivered, из инбокса вон (обе копии).
    tombstones = [r for r in recs if r.get("handled")]
    handled_ids = {_ident(r) for r in tombstones}
    delivered = [r for r in recs if not r.get("handled") and _ident(r) in handled_ids]
    if delivered or tombstones:
        for r in delivered:
            r["handled"] = True
            r["outcome"] = "tg-delivered"
        _append(HANDLED, delivered)
        # выносим и осиротевшие надгробия (пара уже ушла) — иначе копятся в инбоксе вечно
        _rewrite_merged(delivered + tombstones)
        recs = _read(INBOX)
    pending = [r for r in recs if not r.get("handled")]
    if not pending:
        return 0

    # 1) выдохшиеся попытки -> человеку (пункт 3 критерия HIL: сломана автоматика)
    dead = [r for r in pending if r.get("attempts", 0) >= MAX_ATTEMPTS]
    if dead:
        why = (dead[0].get("last_error") or "").strip()
        notify(f"🛠 [fleet] АВТОРАЗБОР НЕ РАБОТАЕТ: {len(dead)} тревог не разобраны за "
               f"{MAX_ATTEMPTS} попыток — сторожа пишут, агент не поднимается. "
               f"Первая: {dead[0].get('source')}: {dead[0].get('text', '')[:160]}"
               + (f"\nПоследняя ошибка сессии: {why[:200]}" if why else ""),
               source="agent-wake", hil=True, key="wake-broken", dedup_sec=6 * 3600)
        for r in dead:
            r["handled"] = True
            r["outcome"] = "escalated-to-human"
        _append(HANDLED, dead)
        pending = [r for r in pending if r.get("attempts", 0) < MAX_ATTEMPTS]
        # только НЕразобранные: иначе инбокс растёт вечно закрытыми записями, а архив
        # закрытых — отдельный файл (agent-inbox.handled.jsonl). Merged: notify() в HIL-ветке
        # выше — сетевой вызов, за него в инбокс могли лечь новые записи.
        _rewrite_merged(dead)
        if not pending:
            return 0

    # 2) частотный гард
    last = os.path.getmtime(STAMP) if os.path.exists(STAMP) else 0
    if time.time() - last < WAKE_GAP_SEC:
        _log(f"agent-wake: {len(pending)} тревог ждут, но с прошлого подъёма "
             f"{time.time() - last:.0f}s < {WAKE_GAP_SEC:.0f}s — не бужу")
        return 0
    if not os.path.exists(CLAUDE):
        _log(f"agent-wake: нет {CLAUDE} — разбудить некого")
        return 1

    batch = pending[:MAX_ITEMS]
    # счётчик ДО запуска: жёсткое падение сессии не должно зациклить элемент
    for r in batch:
        r["attempts"] = r.get("attempts", 0) + 1
    _rewrite_merged([], patch=batch)
    with open(STAMP, "w") as f:
        f.write(str(int(time.time())))

    prompt = PROMPT_HEAD + "\n".join(
        f"  [{r['iso']}] {r['source']}: {r['text']}" for r in batch)
    if len(pending) > len(batch):
        prompt += f"\n\n(ещё {len(pending) - len(batch)} тревог в очереди — разберёшь следующим подъёмом)"

    _log(f"agent-wake: поднимаю сессию на {len(batch)} тревог")
    rc, out, blocked, model = 1, "", None, ""
    for model in WAKE_MODELS:
        t0 = time.time()
        rc, out = _run_session(prompt, model)
        blocked = _start_failure(rc, out)
        with open(SESSION_LOG, "a") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                    f"тревог={len(batch)} model={model} rc={rc} {time.time() - t0:.0f}s"
                    f"{' НЕ-СТАРТ:' + blocked if blocked else ''} =====\n{out}\n")
        _log(f"agent-wake: сессия model={model} rc={rc} за {time.time() - t0:.0f}s, "
             f"вывод {len(out)} симв. -> {SESSION_LOG}")
        if not blocked:
            break
        _log(f"agent-wake: модель {model} не поднялась ({blocked}), беру следующую")

    # причину кладём НА ЗАПИСЬ: тревога об исчерпании попыток обязана нести «почему», а не
    # отсылать к третьему файлу (10-11.08 она дважды позвонила человеку словами «агент не
    # поднимается», пока «You've reached your … limit» лежало в agent-wake-sessions.log)
    if rc != 0:
        for r in batch:
            r["last_error"] = out.strip()[-200:]
        _rewrite_merged([], patch=batch)

    if blocked:
        # НИ ОДНА модель не поднялась ⇒ разбора не было. Попытку возвращаем: иначе незнание
        # («мы даже не смотрели») засчитывается как работа и за MAX_ATTEMPTS циклов закрывает
        # живые тревоги. Человека зовём на САМ отказ инфраструктуры — это пункт 3 критерия HIL,
        # и тревоги при этом остаются в очереди, а не закрываются непрочитанными.
        for r in batch:
            r["attempts"] = max(0, r.get("attempts", 1) - 1)
        _rewrite_merged([], patch=batch)
        notify(f"🛠 [fleet] РАЗБОР НЕ ЗАПУСКАЕТСЯ ({blocked}): не поднялась ни одна модель "
               f"({'/'.join(WAKE_MODELS)}), {len(pending)} тревог ждут в очереди — попытки НЕ "
               f"тратятся, разбор пойдёт сам, как только причина уйдёт.\nПричина: "
               f"{out.strip()[:200]}",
               source="agent-wake", hil=True, key=f"wake-blocked:{blocked}", dedup_sec=6 * 3600)
        return 0

    if rc == 0:
        # В АРХИВ едет СВЕЖАЯ запись, а не снапшот main(). Разбор идёт минуты-десятки минут
        # и дописывает в свою тревогу вывод (поле resolution) — стейл-копия молча его
        # теряла, и в архиве оставалось «outcome: agent» без единого слова о том, ЧТО
        # выяснено. Это та же ошибка, что уже исправлена для перезаписи инбокса
        # (см. _rewrite_merged), просто на соседней строке она дожила незамеченной:
        # архив читают глазами, а не кодом, поэтому потеря не давала симптома.
        fresh = {_ident(r): r for r in _read(INBOX)}
        batch = [fresh.get(_ident(r), r) for r in batch]
        for r in batch:
            r["handled"] = True
            r["outcome"] = "agent"
        _append(HANDLED, batch)
        # merged, не снапшот: пока сессия шла (до 30 мин), инбокс мог пополниться
        _rewrite_merged(batch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
