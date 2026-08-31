#!/usr/bin/env python3
"""Гард маркера вывода из эксплуатации в threads-watch.sh (31.08, крон-триаж threads-botdown).

ПРОВЕРЯЕМ ОБЕ СТОРОНЫ, а не только глушилку: «сторож замолчал» ценно ровно настолько,
насколько доказано, что он НЕ замолчал везде остальном (мёртвый сторож хуже отсутствующего).
Боевые артефакты не трогаем — всё через TW_*-переопределения (канон tests-never-touch-production).
"""
import os, subprocess, tempfile, sys

SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "threads-watch.sh")


def run(d, marker=None, host="root@127.0.0.1"):
    env = dict(os.environ)
    env.update(TW_FAILS=f"{d}/fails", TW_LOG=f"{d}/log", TW_NOTIFY=f"{d}/notify.sh",
               TW_RETIRED=marker or f"{d}/absent-marker", TW_HOST=host)
    p = subprocess.run(["bash", SH], env=env, capture_output=True, text=True, timeout=90)
    return p.returncode


def main():
    fails = []
    with tempfile.TemporaryDirectory() as d:
        # сторож-заглушка вместо notify.sh: тревога не уходит в канал, но факт вызова виден
        with open(f"{d}/notify.sh", "w") as f:
            f.write('#!/bin/sh\necho "$@" >> "$(dirname "$0")/notified"\n')
        os.chmod(f"{d}/notify.sh", 0o755)
        open(f"{d}/log", "w").write("05:00 threads=174 max=2048\n")
        open(f"{d}/fails", "w").write("4\n")   # серия слепых прогонов ДО вывода из эксплуатации

        # 1. МАРКЕР ЕСТЬ: тихий выход, тревоги нет, серия сброшена, в лог — ОДНА строка
        m = f"{d}/RETIRED.md"
        open(m, "w").write("# выведен\n")
        rc = run(d, marker=m)
        if rc != 0:
            fails.append(f"маркер есть: rc={rc}, ожидался 0")
        if os.path.exists(f"{d}/notified"):
            fails.append("маркер есть: сторож ВСЁ РАВНО позвонил")
        if open(f"{d}/fails").read().strip() != "0":
            fails.append("маркер есть: серия слепых прогонов не сброшена")
        log = open(f"{d}/log").read()
        if "retired=1" not in log:
            fails.append("маркер есть: сторож молчит В ЛОГ — неотличим от 'cron не зовёт'")
        if "threads=174" not in log:
            fails.append("маркер есть: прежнее распределение threads= затёрто")

        # 2. ПОВТОРНЫЙ ПРОГОН: строка не дублируется (иначе 288/сутки вымоют распределение)
        run(d, marker=m)
        if open(f"{d}/log").read().count("retired=1") != 1:
            fails.append("повтор: строка retired=1 продублирована")

        # 3. МАРКЕРА НЕТ: сторож ЖИВ — на трёх слепых прогонах подряд обязан позвонить
        open(f"{d}/fails", "w").write("0\n")
        for _ in range(3):
            run(d)                      # host недостижим -> ветка blind()
        if not os.path.exists(f"{d}/notified"):
            fails.append("маркера нет: сторож НЕ позвонил на 3 слепых прогонах — я его убил")

    for f in fails:
        print("FAIL:", f)
    print("OK: гард маркера" if not fails else f"ПРОВАЛЕНО: {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
