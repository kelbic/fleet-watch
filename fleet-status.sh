#!/bin/bash
# Срез всего флота одной командой: живость, свежесть лога, экономика, сторожа.
# Read-only — ничего не запускает и не перезапускает.
set -u
now=$(date +%s)
printf "ФЛОТ ЛИКВИДАТОРОВ  %s UTC\n" "$(date -u '+%Y-%m-%d %H:%M:%S')"
printf "%-11s %-7s %-7s %-9s %-8s %s\n" БОТ ЖИВ ЛОГ ПРОХОДОВ ВЫСТРЕЛ ПОСЛЕДНЯЯ_СТРОКА
printf '%.0s-' {1..100}; echo

for b in wc katana hyperlend midnight; do
  lock=/tmp/$b-executor.lock
  holders=$(fuser $lock 2>/dev/null | tr -s ' ')
  alive=$([ -n "$holders" ] && echo ДА || echo НЕТ)

  L=/home/claude-agent/.$b-bot/executor.log
  if [ -f "$L" ]; then age=$(( now - $(stat -c %Y "$L") )); last=$(tail -n1 "$L" | cut -c1-46)
  else age=-1; last="(нет лога)"; fi

  # состояние: у wc оно в отдельном каталоге
  S=/home/claude-agent/.$b-bot/exec_state.json
  [ -f "$S" ] || S=/home/claude-agent/.$b-bot/state.json
  [ -f "$S" ] || S=/home/claude-agent/.wc-liquidator/state.json
  if [ "$b" = wc ]; then S=/home/claude-agent/.wc-liquidator/state.json; fi
  if [ -f "$S" ]; then
    read -r passes fires <<<"$(python3 -c "
import json;d=json.load(open('$S'));print(d.get('passes',0), d.get('fires',0))" 2>/dev/null || echo "? ?")"
  else passes=?; fires=?; fi

  printf "%-11s %-7s %-7s %-9s %-8s %s\n" "$b" "$alive" "${age}s" "$passes" "$fires" "$last"
done

echo
# газ-гарды и killswitch
echo "ГАРДЫ:"
for b in wc katana hyperlend midnight; do
  S=/home/claude-agent/.$b-bot/exec_state.json
  [ -f "$S" ] || S=/home/claude-agent/.$b-bot/state.json
  [ "$b" = wc ] && S=/home/claude-agent/.wc-liquidator/state.json
  [ -f "$S" ] || continue
  python3 -c "
import json
d=json.load(open('$S'))
bal=d.get('balance_eth'); tr=d.get('tripped')
print(f\"  {'$b':<10} gas_usd=\${d.get('gas_usd',0):<6.2f} consec_reverts={d.get('consec_reverts',0)}\"
      + (f' balance={bal}' if bal is not None else '')
      + (f'  KILL-SWITCH: {tr}' if tr else ''))" 2>/dev/null
done

echo
echo "СТОРОЖА:"
NW=/home/claude-agent/.fleet-watch/netwatch.log
if [ -f "$NW" ]; then
  echo "  netwatch (сеть хоста): $(tail -n1 $NW)"
  fails=$(tail -n 720 "$NW" | grep -c "rpc_ok=0/")   # ~сутки при каденсе 2 мин
  echo "  netwatch: полных отказов за последние ~720 проб: $fails"
else echo "  netwatch: лога ещё нет"; fi
CW=/home/claude-agent/.chainwatch/state.json
[ -f "$CW" ] && python3 -c "
import json;d=json.load(open('$CW'))
print(f\"  chainwatch: рынков {len(d.get('markets',{}))}, чейнов {len(d.get('chains',[]))}, fail_streak={d.get('fail_streak')}\")"
echo "  deadman-кроны: $(crontab -l 2>/dev/null | grep -c deadman.sh)/4 установлены"
