#!/usr/bin/env python3
"""Будильник агента: разбирает инбокс тревог, поднимая headless-сессию Claude.

Вторая половина решения 03.08 («алерты будят тебя, а не эскалируются на меня»). notify.py
кладёт всё не-HIL в ~/.fleet-watch/agent-inbox.jsonl; этот скрипт по крону смотрит, есть
ли неразобранное, и если есть — поднимает сессию, которая чинит.

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
    pending = [r for r in recs if not r.get("handled")]
    if not pending:
        return 0

    # 1) выдохшиеся попытки -> человеку (пункт 3 критерия HIL: сломана автоматика)
    dead = [r for r in pending if r.get("attempts", 0) >= MAX_ATTEMPTS]
    if dead:
        notify(f"🛠 [fleet] АВТОРАЗБОР НЕ РАБОТАЕТ: {len(dead)} тревог не разобраны за "
               f"{MAX_ATTEMPTS} попыток — сторожа пишут, агент не поднимается. "
               f"Первая: {dead[0].get('source')}: {dead[0].get('text', '')[:160]}",
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
    open(STAMP, "w").write(str(int(time.time())))

    prompt = PROMPT_HEAD + "\n".join(
        f"  [{r['iso']}] {r['source']}: {r['text']}" for r in batch)
    if len(pending) > len(batch):
        prompt += f"\n\n(ещё {len(pending) - len(batch)} тревог в очереди — разберёшь следующим подъёмом)"

    _log(f"agent-wake: поднимаю сессию на {len(batch)} тревог")
    t0 = time.time()
    try:
        p = subprocess.run([CLAUDE, "-p", prompt], capture_output=True, text=True,
                           timeout=SESSION_TIMEOUT, cwd=os.path.expanduser("~"))
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, f"таймаут {SESSION_TIMEOUT}s"
    except Exception as e:  # noqa: BLE001
        rc, out = 1, f"запуск упал: {e}"

    with open(SESSION_LOG, "a") as f:
        f.write(f"\n===== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                f"тревог={len(batch)} rc={rc} {time.time() - t0:.0f}s =====\n{out}\n")
    _log(f"agent-wake: сессия rc={rc} за {time.time() - t0:.0f}s, "
         f"вывод {len(out)} симв. -> {SESSION_LOG}")

    if rc == 0:
        for r in batch:
            r["handled"] = True
            r["outcome"] = "agent"
        _append(HANDLED, batch)
        # merged, не снапшот: пока сессия шла (до 30 мин), инбокс мог пополниться
        _rewrite_merged(batch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
