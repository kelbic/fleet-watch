#!/usr/bin/env bash
# НОЧНОЙ АДВЕРСАРИЙ (kelbic 26.08: «ревьюер без контекста и взгляда сессии»).
# Форма Б протокола docs/BLIND-REVIEW.md репо liquidator: свежая headless-сессия берёт
# вердикты суток из STATE (строки с маркером «ВЕРДИКТ:»), ПЕРЕМЕРЯЕТ первичные источники
# ДО чтения обоснования и кладёт исход в agent-inbox (опровержение будит ведущую сессию
# штатным agent-wake). Дисциплина квоты: ≤2 вердикта за ночь, цепочка моделей opus→sonnet.
set -u
REPO="$HOME/work/liquidator"
WATCH="$HOME/.fleet-watch"
CLAUDE="$HOME/.local/bin/claude"
LOG="$WATCH/blind-audit.log"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

# вердикты, ДОБАВЛЕННЫЕ в STATE за ~26 часов (по git-истории, не по grep всего файла)
VERDICTS=$(git -C "$REPO" log --since="26 hours ago" -p -- docs/STATE.md 2>/dev/null \
  | grep -E '^\+.*ВЕРДИКТ:' | sed 's/^+//' | head -12)
if [ -z "$VERDICTS" ]; then
  log "тихо: новых строк «ВЕРДИКТ:» за сутки нет"
  exit 0
fi

PROMPT="Ты — независимый адверсарный ревьюер бота-ликвидатора (Morpho/Base). Проснулся по крону, человек не ждёт. Ниже — заявленные за сутки вердикты ведущей сессии (СТРОКИ-ЗАЯВЛЕНИЯ, обоснований тебе намеренно не дали). Выбери 1-2 с наибольшим радиусом поражения (деньги/nonce/стратегия) и для КАЖДОГО: (1) ПЕРЕМЕРЬ первичные источники САМ — journalctl -u liquidator-bot на root@185.173.146.134 (read-only!), публичная нода https://mainnet.base.org, код в $REPO/chain/; ЗАПРЕЩЕНО до завершения перемера читать docs/STATE.md и docs/Plan.md (это и есть взгляд, от которого ты независим); (2) вынеси вердикт: ПОДТВЕРЖДЁН / ОПРОВЕРГНУТ (чем) / НЕ ПРОВЕРЯЕМ (чего не хватает); (3) запиши исход: python3 -c 'import sys; sys.path.insert(0,\"$WATCH\"); from notify import notify; notify(\"<твой текст>\", source=\"blind-audit\", hil=False, key=\"blind-audit:<кратко>\", dedup_sec=86400)' — при ОПРОВЕРЖЕНИИ начни текст с «⛔ ОПРОВЕРГНУТО:». Ничего не менять: ни кода, ни env, ни рестартов — ты только судья. ВЕРДИКТЫ СУТОК:
$VERDICTS"

for MODEL in opus sonnet; do
  OUT=$(timeout 1500 "$CLAUDE" -p --model "$MODEL" "$PROMPT" 2>&1)
  RC=$?
  if [ $RC -eq 0 ] && ! printf '%s' "$OUT" | grep -qiE "limit|quota exceeded"; then
    log "ок model=$MODEL: $(printf '%s' "$OUT" | tail -1 | cut -c1-200)"
    exit 0
  fi
  log "модель $MODEL не прошла rc=$RC: $(printf '%s' "$OUT" | head -1 | cut -c1-150)"
done
log "ОБЕ модели не прошли — аудит этой ночи пропущен (утренняя сводка сессии увидит лог)"
exit 1
