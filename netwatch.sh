#!/bin/bash
# ХОСТОВОЙ сетевой детектор флота ликвидаторов.
#
# ЗАЧЕМ. 24.07 все ЧЕТЫРЕ бота (World 480, Katana 747474, HyperEVM 999, Base 8453 —
# разные чейны, разные провайдеры) замерли в ОДНИ И ТЕ ЖЕ секунды: 13:03:21 и ~13:11:05,
# провалы 406с/406с/129с/404с. Это отказ egress ХОСТА, а не деградация какого-то чейна.
# Ни один прибор этого не увидел: cron-watchdog смотрит, что процесс жив (зависший жив и
# держит flock), deadman — что лог тикает (у каждого бота свой, и четыре одновременных 💀
# читались бы как четыре независимые поломки, а не как ОДНА общая причина).
#
# ЧТО ДЕЛАЕТ. Пробит по одному эндпоинту на чейн + КОНТРОЛЬНЫЙ не-RPC хост. Алертит
# ТОЛЬКО когда лежит ВСЁ (иначе это обычная деградация одного провайдера — шум).
# Контроль разделяет два диагноза: RPC=0 но контроль жив -> легли провайдеры/фильтрация;
# всё по нулям -> хост потерял egress. Плюс отдельная проба DNS (резолв не подчиняется
# сокетным таймаутам Python — классический источник «висит дольше таймаута»).
#
# ТИХИЙ РЕЖИМ. Алерт не чаще часа (штамп), и ровно ОДИН на инцидент + один на
# восстановление. История каждой пробы пишется в netwatch.log — по ней можно будет
# скоррелировать будущий инцидент вместо гадания.
set -u
DIR=/home/claude-agent/.fleet-watch
LOG=$DIR/netwatch.log
STAMP=$DIR/.alerted            # взведён = инцидент уже отправлен (ждём восстановления)
THROTTLE=3600

# по одному эндпоинту на чейн, из тех же списков, что читают боты
PROBES=(
  "wc|https://worldchain-mainnet.g.alchemy.com/public"
  "katana|https://rpc.katana.network"
  "hyperlend|https://rpc.hyperlend.finance"
  "base|https://mainnet.base.org"
)
CONTROL="https://1.1.1.1"      # не-RPC контроль: отделяет «легли провайдеры» от «лёг хост»
BODY='{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'

ok=0; total=0; detail=""
for p in "${PROBES[@]}"; do
  name=${p%%|*}; url=${p#*|}
  total=$((total+1))
  t0=$(date +%s%N)
  if curl -sS -m 8 -X POST -H 'Content-Type: application/json' \
       -H 'User-Agent: Mozilla/5.0' --data "$BODY" "$url" 2>/dev/null | grep -q '"result"'; then
    ms=$(( ($(date +%s%N)-t0)/1000000 )); ok=$((ok+1)); detail="$detail $name=${ms}ms"
  else
    ms=$(( ($(date +%s%N)-t0)/1000000 )); detail="$detail $name=FAIL(${ms}ms)"
  fi
done

# контроль (не-RPC) и DNS — только для диагноза, в счёт ok не идут
ctl=FAIL; curl -sS -m 8 -o /dev/null "$CONTROL" 2>/dev/null && ctl=ok
dns=FAIL; getent hosts mainnet.base.org >/dev/null 2>&1 && dns=ok

ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
echo "$ts rpc_ok=$ok/$total ctl=$ctl dns=$dns$detail" >> "$LOG"
# лог не должен расти вечно (детектор тикает часто) — держим последние ~20k строк
if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 20000 ]; then
  tail -n 10000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

send() {  # $1 = текст. АДРЕСАТ — АГЕНТ (03.08): сетевой провал VPS человек не чинит.
  # При мёртвом egress запись в инбокс всё равно проходит (это локальный файл), и агент
  # разберёт её, когда сеть вернётся — тревога не теряется, а просто ждёт.
  /home/claude-agent/.fleet-watch/notify.sh netwatch 0 "$1" 900 netwatch > /dev/null
}

if [ "$ok" -eq 0 ]; then
  # ВСЁ легло. Диагноз по контролю: egress хоста или только RPC-провайдеры.
  if [ -f "$STAMP" ] && [ $(( $(date +%s) - $(stat -c %Y "$STAMP") )) -lt "$THROTTLE" ]; then
    exit 0
  fi
  if [ "$ctl" = "ok" ]; then
    diag="контроль (1.1.1.1) ЖИВ, DNS=$dns -> сеть хоста жива, легли/фильтруются RPC-провайдеры"
  else
    diag="контроль (1.1.1.1) ТОЖЕ мёртв, DNS=$dns -> ХОСТ потерял egress"
  fi
  send "🌐 [флот] ВСЕ RPC недоступны (0/$total).$detail
$diag
Все 4 бота сейчас слепы одновременно — это ОДНА причина, не четыре поломки. Проходы ограничены потолком и перезапустятся сами, когда сеть вернётся."
  touch "$STAMP"
elif [ -f "$STAMP" ]; then
  # восстановление после отправленного инцидента — ровно один ответный пинг
  send "✅ [флот] сеть восстановлена: RPC $ok/$total живы.$detail"
  rm -f "$STAMP"
fi
