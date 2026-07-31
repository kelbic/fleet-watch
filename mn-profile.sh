#!/bin/bash
# Переключатель боевых профилей midnight для окна 31.07. ОДНА КОМАНДА, не рецепт.
#
#   mn-profile.sh status    — что стоит сейчас
#   mn-profile.sh patient   — терпение: пол $3,000, детект штатный (по умолчанию до 🥊)
#   mn-profile.sh race      — гонка: пол $25, детект 30с (по алерту оснащения конкурента)
#
# ЧЕМ ОТЛИЧАЮТСЯ. Выплата растёт ~$1.22/сек (рампа 0→maxLif за 60 мин, потолок ~$4,117).
#   patient — ждём пол $3,000 (~+45 мин). Максимум денег, если конкурент не боеспособен.
#   race    — бьём почти сразу за breakeven (~+233с, ~$25-30). Это НЕ попытка выиграть
#             гонку скоростью: конкурент 0x6cf59693 уже обыграл нас в одном блоке на WC,
#             и на Base мы его не пересидим. Это ОГРАНИЧЕНИЕ УЩЕРБА — взять немного
#             вместо ноля, если он оснастился.
#
# ДВА РЫЧАГА, оба env (кода не трогаем — за 4 дня до окна правки боевого кода дороже):
#   MN_MIN_PROFIT — пол выстрела;
#   MN_HOT_REFRESH — каденс FULL-скана. До maturity цель НЕ в hot-наборе (здоровье 59%
#     < порога 90%), поэтому её видит только FULL. Штатные 600с дают разброс детекта
#     0..10 мин; в гонке это и есть главная потеря, а не бродкаст. 30с убирает разброс.
#
# ПАРАЛЛЕЛЬНЫЙ БРОДКАСТ СОЗНАТЕЛЬНО НЕ ДЕЛАЕМ: write-путь уже ротирует 3 эндпоинта, а
# выигрыш параллели на Base (один секвенсер) — десятки мс против МИНУТ разброса детекта.
# Это правка боевого кода ради второго знака после запятой. Если понадобится — после окна.
set -euo pipefail

ENV=/home/claude-agent/.midnight-bot/env
LOG=/home/claude-agent/.midnight-bot/executor.log

cur() { grep -oE '^export MN_MIN_PROFIT=[0-9]+' "$ENV" | cut -d= -f2; }
cur_refresh() { grep -oE '^export MN_HOT_REFRESH=[0-9]+' "$ENV" | cut -d= -f2 || echo "600(деф)"; }

status() {
  echo "профиль: пол=\$$(cur)  FULL-скан=$(cur_refresh)с"
  local p; p=$(pgrep -f '[b]ot\.executor schedule' | head -1 || true)
  if [ -n "$p" ]; then
    echo "процесс: pid $p, живёт $(ps -p "$p" -o etime= | tr -d ' ')"
    echo "в процессе: $(tr '\0' '\n' < /proc/"$p"/environ | grep -E 'MN_MIN_PROFIT|MN_HOT_REFRESH' | tr '\n' ' ')"
  else
    echo "процесс: НЕ ЗАПУЩЕН (cron поднимет в течение минуты)"
  fi
  tail -2 "$LOG"
}

apply() {  # $1=floor $2=refresh $3=имя
  cp "$ENV" "$ENV.bak-$(date -u +%Y%m%d-%H%M%S)"
  sed -i -E "s|^export MN_MIN_PROFIT=[0-9]+.*|export MN_MIN_PROFIT=$1   # профиль $3 ($(date -u +%d.%m\ %H:%M)Z)|" "$ENV"
  sed -i -E "/^export MN_HOT_REFRESH=/d" "$ENV"
  echo "export MN_HOT_REFRESH=$2   # профиль $3" >> "$ENV"
  echo "env: пол=\$$1 FULL-скан=$2с (профиль $3)"
  local p; p=$(pgrep -f '[b]ot\.executor schedule' | head -1 || true)
  [ -n "$p" ] && { kill "$p"; echo "процесс $p снят — cron поднимет ≤60с"; }
  echo -n "жду возврата"
  for _ in $(seq 1 24); do
    sleep 5; echo -n "."
    p=$(pgrep -f '[b]ot\.executor schedule' | head -1 || true)
    if [ -n "$p" ]; then
      echo " ПОДНЯЛСЯ pid $p"
      tr '\0' '\n' < /proc/"$p"/environ | grep -E 'MN_MIN_PROFIT|MN_HOT_REFRESH'
      return 0
    fi
  done
  echo " ⚠️ НЕ ПОДНЯЛСЯ за 2 мин — смотреть $LOG и flock /tmp/midnight-executor.lock"
  return 1
}

case "${1:-status}" in
  status)  status ;;
  patient) apply 3000 600 patient ;;
  race)    apply 25 30 race ;;
  *) echo "usage: $0 {status|patient|race}"; exit 2 ;;
esac
