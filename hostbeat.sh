#!/bin/bash
# Суточный «хост жив» в операционный TG — МОСТИК до внешнего пинг-сервиса.
#
# ЗАЧЕМ, если алерты только там, где нужен человек: это dead-man наоборот. Все прочие
# сторожа живут НА ЭТОЙ ЖЕ машине и при её смерти умирают вместе с ней — сценарий
# «мёртв весь хост» не покрыт ничем. Здесь сигналом является МОЛЧАНИЕ: сообщение не
# пришло сутки ⇒ смотреть. Слабее внешнего healthchecks.io (зависит от внимательности
# владельца), но ставится без единой внешней настройки и закрывает дыру уже сегодня.
# Когда появится ping-URL — этот крон снять, заменить на внешний.
set -u
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$HOME/.claude/channels/telegram/.env" 2>/dev/null | cut -d= -f2-)
CHAT="${MN_CHAT_ID:-265715923}"
[ -n "$TOKEN" ] || exit 0
BOTS=0
for p in $(pgrep -x python3 2>/dev/null); do
  case "$(readlink /proc/$p/cwd 2>/dev/null)" in *-liquidator) BOTS=$((BOTS+1));; esac
done
UP=$(uptime -p 2>/dev/null | sed 's/^up //')
LOAD=$(cut -d' ' -f1-3 /proc/loadavg)
DISK=$(df -h /home | awk 'NR==2{print $5}')
curl -sm 15 "https://api.telegram.org/bot$TOKEN/sendMessage" \
  --data-urlencode "chat_id=$CHAT" \
  --data-urlencode "text=💓 хост жив: ботов в бою $BOTS/4, аптайм $UP, load $LOAD, диск $DISK.
Это суточный маячок: ТИШИНА сутки = машина умерла, смотреть." > /dev/null
