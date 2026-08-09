#!/bin/bash
# КАНАРЕЙКА ВЫХОДОВ ликвидатора Base/Morpho на кроне (задача #72, раз в 6 часов).
#
# ЗАЧЕМ. Прямой маршрут выхода — не константа, а срез ЧУЖОЙ ликвидности. 09.08 два раза
# подряд это стоило денег: cbXRP держали на UniV3, где выход оказался в 269 раз хуже
# Aerodrome (план молча ревертил), а кап cbDOGE стоял ровно на краю обрыва пула (impact
# 43.5% при бонусе 12.7%). Оба раза узнавали постфактум. Замер стоит ~22 read-only квоты
# на ПУБЛИЧНОМ узле — без ключей, без tx, боевой бот и его квоты не задеты.
#
# ГДЕ ЗАПУСКАЕТСЯ: на АГЕНТСКОЙ машине, не на боевой (урок 30.07: вотчеры — на Вену;
# одноядерная боевая VPS душится ad-hoc нагрузкой, эффект наблюдателя задокументирован).
#
# АДРЕСАТ ТРЕВОГИ — агент (hil=0): протухший роут чинит агент (кап вниз, смена венью),
# человеку тут решать нечего. Порог считает сам вотчер по LIF конкретного рынка.
#
# ПОЧЕМУ ПУТЬ ИЩЕТСЯ, А НЕ ЗАШИТ. Код живёт в рабочем дереве, которое переезжает между
# ветками/воркдеревьями. Жёсткий путь означал бы тихую смерть сторожа при первом же
# переезде — а мёртвый сторож хуже отсутствующего. Поэтому кандидаты перебираются, и
# ОТСУТСТВИЕ кода — это громкая тревога, а не молчаливый exit 0.
#
#   ROUTE_CANARY_REPO=/путь   принудительный чекаут
#   RC_LOG=/путь              иной лог (для проверок; боевой не трогать)
set -u

LOG=${RC_LOG:-/home/claude-agent/.fleet-watch/route-canary.log}
NOTIFY=/home/claude-agent/.fleet-watch/notify.sh
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

REPO=""
for CAND in ${ROUTE_CANARY_REPO:-} \
            /home/claude-agent/work/liquidator \
            /home/claude-agent/work/liquidator/.claude/worktrees/latency-race; do
  [ -n "$CAND" ] || continue
  if [ -f "$CAND/analysis/route_canary_watch.py" ]; then REPO=$CAND; break; fi
done

if [ -z "$REPO" ]; then
  echo "$TS route-canary: ОШИБКА — не найден чекаут с analysis/route_canary_watch.py" >> "$LOG"
  "$NOTIFY" route-canary 0 \
    "канарейка выходов НЕ ЗАПУСКАЕТСЯ: не найден чекаут ликвидатора с analysis/route_canary_watch.py (кандидаты: work/liquidator и его worktree). Крон тикает вхолостую — проверить ~/.fleet-watch/route-canary.sh" \
    21600 route-canary:no-repo >> "$LOG" 2>&1
  exit 1
fi

cd "$REPO" || { echo "$TS route-canary: ОШИБКА cd $REPO" >> "$LOG"; exit 1; }
echo "$TS route-canary: старт, чекаут $REPO" >> "$LOG"
exec /usr/bin/python3 -m analysis.route_canary_watch >> "$LOG" 2>&1
