#!/bin/bash
# fleetctl — единственный безопасный способ найти и перезапустить бота флота Вены.
#
# ЗАЧЕМ. У всех ботов флота БУКВАЛЬНО одинаковая командная строка (`python3 -u -m bot.executor
# loop`), различает их только рабочий каталог. Поэтому любой отбор по шаблону cmdline —
# `pkill -f "bot.executor"`, `pgrep -f loop | head -1` — это лотерея: 05.08 при перезапуске
# hyperlend такой шаблон погасил katana (крон поднял её через ~2 минуты; в день окна это стоило
# бы окна). Плюс шаблон ловит и сам вызывающий шелл, если строка шаблона написана в той же
# команде — три таких самоубийства за одну сессию.
#
# ПРАВИЛО: процесс бота опознаётся ТОЛЬКО по readlink /proc/PID/cwd. Никаких -f шаблонов.
#
# Использование:
#   fleetctl.sh pid <bot>            — PID процесса бота (пусто + код 1, если не найден)
#   fleetctl.sh status               — таблица по всем: pid, аптайм, возраст лога
#   fleetctl.sh restart <bot>        — TERM по PID, ожидание подъёма крон+flock, проверка
#   fleetctl.sh assert-alive <bot>   — код 0, если жив И лог свежий (для скриптов)
# bot ∈ hyperlend | katana | wc | midnight
set -uo pipefail

declare -A CWD=(
  [hyperlend]=/home/claude-agent/hyperlend-liquidator
  [katana]=/home/claude-agent/katana-liquidator
  [wc]=/home/claude-agent/wc-liquidator
  [midnight]=/home/claude-agent/midnight-liquidator
)
declare -A LOG=(
  [hyperlend]=/home/claude-agent/.hyperlend-bot/executor.log
  [katana]=/home/claude-agent/.katana-bot/executor.log
  [wc]=/home/claude-agent/.wc-bot/executor.log
  [midnight]=/home/claude-agent/.midnight-bot/executor.log
)
RESTART_WAIT=${FLEETCTL_RESTART_WAIT:-150}   # крон тикает раз в минуту; 150с = два окна с запасом
LOG_FRESH=${FLEETCTL_LOG_FRESH:-120}         # лог старше этого = бот не пишет

die() { echo "fleetctl: $*" >&2; exit 2; }

known() { [ -n "${CWD[$1]+x}" ] || die "неизвестный бот '$1' (есть: ${!CWD[*]})"; }

# PID по рабочему каталогу — единственный надёжный признак. Возвращает ВСЕ совпадения:
# их должно быть ровно одно, два означают гонку flock и это само по себе сигнал.
pids_of() {
  local want="${CWD[$1]}" p c out=()
  for p in $(pgrep -x python3 2>/dev/null; pgrep -x python 2>/dev/null); do
    c=$(readlink "/proc/$p/cwd" 2>/dev/null) || continue
    [ "$c" = "$want" ] && out+=("$p")
  done
  printf '%s\n' "${out[@]}" | sed '/^$/d'
}

log_age() {
  local f="${LOG[$1]}"
  [ -f "$f" ] || { echo -1; return; }
  echo $(( $(date +%s) - $(stat -c %Y "$f") ))
}

cmd_pid() { known "$1"; local p; p=$(pids_of "$1" | head -1); [ -n "$p" ] || exit 1; echo "$p"; }

cmd_status() {
  printf '%-10s %-8s %-14s %s\n' БОТ PID АПТАЙМ ЛОГ
  local b p age up
  for b in "${!CWD[@]}"; do
    p=$(pids_of "$b" | head -1)
    age=$(log_age "$b")
    up=$([ -n "$p" ] && ps -o etime= -p "$p" 2>/dev/null | tr -d ' ' || echo '—')
    printf '%-10s %-8s %-14s %sс назад\n' "$b" "${p:-НЕТ}" "$up" "$age"
  done | sort
}

cmd_assert_alive() {
  known "$1"
  local p age
  p=$(pids_of "$1" | head -1); age=$(log_age "$1")
  [ -n "$p" ] || { echo "$1: процесса нет" >&2; return 1; }
  [ "$age" -ge 0 ] && [ "$age" -le "$LOG_FRESH" ] || {
    echo "$1: процесс есть (pid $p), но лог молчит ${age}с — живость НЕ доказана" >&2; return 1; }
  echo "$1: жив (pid $p, лог ${age}с назад)"
}

cmd_restart() {
  known "$1"
  local b="$1" old new waited age
  old=$(pids_of "$b" | head -1)
  [ -n "$old" ] || die "$b: процесс не найден — перезапускать нечего (крон поднимет сам)"
  # ЛОГ ПЕРЕД. Приёмка перезапуска — смена PID И ожившая запись, а не отсутствие ошибки.
  echo "$b: останавливаю pid $old (по cwd ${CWD[$b]})"
  kill "$old" || die "$b: kill $old не прошёл"
  waited=0
  while [ "$waited" -lt "$RESTART_WAIT" ]; do
    sleep 5; waited=$(( waited + 5 ))
    new=$(pids_of "$b" | head -1)
    [ -n "$new" ] && [ "$new" != "$old" ] && break
  done
  new=$(pids_of "$b" | head -1)
  [ -n "$new" ] && [ "$new" != "$old" ] || die "$b: за ${waited}с новый процесс не поднялся (крон/flock?)"
  # ждём ПЕРВУЮ свежую строку — процесс есть ≠ цикл жив (урок 01.08)
  waited=0
  while [ "$waited" -lt 90 ]; do
    age=$(log_age "$b"); [ "$age" -ge 0 ] && [ "$age" -le 30 ] && break
    sleep 5; waited=$(( waited + 5 ))
  done
  age=$(log_age "$b")
  echo "$b: поднялся pid $new; лог ${age}с назад"
  [ "$age" -ge 0 ] && [ "$age" -le 60 ] || { echo "$b: ВНИМАНИЕ — лог молчит ${age}с" >&2; return 1; }
}

case "${1:-}" in
  pid)          shift; cmd_pid "${1:?нужен бот}" ;;
  status)       cmd_status ;;
  restart)      shift; cmd_restart "${1:?нужен бот}" ;;
  assert-alive) shift; cmd_assert_alive "${1:?нужен бот}" ;;
  *) die "использование: $0 {pid|status|restart|assert-alive} [bot]" ;;
esac
