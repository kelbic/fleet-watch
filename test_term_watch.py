#!/usr/bin/env python3
"""Стенд сторожей Term/Midnight. Транспорт закрыт НА САМОМ ТРАНСПОРТЕ
([[tests-never-touch-production-channels]]): FLEET_ALERT_MUTE + временные INBOX/DEDUP,
плюс имя файла test_* — notify.muted() ловит его само.

Проверяется НЕ «скрипт не падает», а что тревога ФАКТИЧЕСКИ поднимается на том состоянии,
ради которого сторож поставлен, и НЕ поднимается на здоровом ([[red-bench-is-not-a-diagnosis]]).
"""
import json, os, subprocess, sys, tempfile

WATCH = os.path.expanduser("~/.fleet-watch")
ENVBASE = dict(os.environ, FLEET_ALERT_MUTE="1")


def run(script, state_seed=None, patch=None):
    d = tempfile.mkdtemp()
    inbox = os.path.join(d, "inbox.jsonl")
    env = dict(ENVBASE, FLEET_INBOX=inbox, FLEET_DEDUP_STATE=os.path.join(d, "dedup.json"))
    if patch:
        env.update(patch)
    st = os.path.join(WATCH, {"term-watch.py": "term-watch.state",
                              "midnight-chains-watch.py": "midnight-chains.state"}[script])
    bak = st + ".testbak"
    had = os.path.exists(st)
    if had:
        os.rename(st, bak)
    try:
        if state_seed is not None:
            json.dump(state_seed, open(st, "w"))
        p = subprocess.run([sys.executable, os.path.join(WATCH, script)],
                           capture_output=True, text=True, env=env, timeout=1800)
        # notify при заглушённом транспорте уводит запись в ФАЙЛ-ДУБЛЁР INBOX+".muted"
        # (см. notify.py:212). Стенд обязан читать именно его, иначе «тревог нет» —
        # артефакт стенда, а не свойство сторожа ([[red-bench-is-not-a-diagnosis]]).
        alarms = []
        for f_ in (inbox, inbox + ".muted"):
            if os.path.exists(f_):
                alarms += [json.loads(l)["key"] for l in open(f_)]
        return p.stdout + p.stderr, alarms
    finally:
        if os.path.exists(st):
            os.unlink(st)
        if had:
            os.rename(bak, st)


def ok(name, cond, extra=""):
    print(("ПРОШЁЛ  " if cond else "ПРОВАЛ  ") + name + (" | " + extra if extra else ""))
    return cond


def main():
    fails = 0

    # 1. term-watch на ЗДОРОВОМ состоянии молчит и печатает тождество
    out, al = run("term-watch.py")
    fails += not ok("term: здоровое состояние — тревог нет", al == [], f"тревоги={al}")
    fails += not ok("term: тождество агрегата сошлось", "тождество=сошлось" in out, out[-160:])

    # 2. term-watch: падение долга ≥$100k поднимает тревогу (сеем прошлый максимум)
    # ПАДЕНИЕ судится против БАЗЫ реестра, а не против прошлого прогона: поэтому сеять
    # state бесполезно — поднимаем саму базу ручкой стенда и меряем на живой цепи.
    out, al = run("term-watch.py", patch={"TERM_WATCH_BASE_ADD": "200000"})
    fails += not ok("term: тревога на падении долга", "term-watch:debt-drop" in al, f"{al}")

    # 3. term-watch: мёртвый узел = ТРЕВОГА, а не тишина
    out, al = run("term-watch.py", patch={"TERM_WATCH_FORCE_RPC": "1"})
    #   (подменяем список узлов через окружение — см. ниже правку сторожа)
    fails += not ok("term: мёртвый прибор поднимает тревогу",
                    "term-watch:instrument-dead" in al, f"{al} | {out[-140:]}")

    # 4. midnight-chains: сломанный контроль прибора = тревога «прибор мёртв» и НИ СЛОВА
    #    про «книга пуста» (иначе ноль от сломанного прибора читался бы как тишина)
    out, al = run("midnight-chains-watch.py", patch={"MC_CTRL_MIN": "999999999999999"})
    fails += not ok("midnight: провал контроля -> instrument-dead",
                    "midnight-chains:instrument-dead" in al, f"{al}")
    fails += not ok("midnight: при мёртвом приборе книга НЕ судится",
                    not any(a.endswith("-alive") for a in al), f"{al}")

    print("\nИТОГ:", "ВСЁ ЗЕЛЕНО" if not fails else f"ПРОВАЛОВ {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
