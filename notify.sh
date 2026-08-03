#!/bin/bash
# Обёртка notify.py для bash-сторожей (деадманы, netwatch, cronwatch).
#
#   notify.sh <source> <hil:0|1> <текст> [dedup_sec] [key]
#
# hil=0 (обычный случай) — в инбокс агента: он проснётся и починит.
# hil=1 — человеку, и только по критерию из шапки notify.py (нужна его подпись/ключ/
# доступ, остановлен боевой огонь, сломана сама автоматика, необратимое действие).
# Мёртвый или зависший бот к человеку НЕ идёт: перезапуск и снятие спина агент делает сам.
src="$1"; hil="$2"; text="$3"; dedup="${4:-0}"; key="${5:-}"
exec /usr/bin/python3 - "$src" "$hil" "$text" "$dedup" "$key" <<'PY'
import os, sys
sys.path.insert(0, os.path.expanduser("~/.fleet-watch"))
from notify import notify
src, hil, text, dedup, key = sys.argv[1:6]
print(notify(text, source=src, hil=(hil == "1"), key=key, dedup_sec=float(dedup or 0)))
PY
