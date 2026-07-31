#!/bin/bash
# НАДЗОР ЗА СТОРОЖАМИ. Проверяет, что периодические задачи флота ФАКТИЧЕСКИ запускаются.
#
# ЗАЧЕМ (урок 28.07, привезён с чужого бота: «мёртвый сторож хуже отсутствующего»).
# Здоровый deadman.sh НЕ ПИШЕТ НИЧЕГО — он молча выходит. Поэтому «сторож отработал, всё
# хорошо» и «сторож не запускался ни разу» выглядят на диске ОДИНАКОВО. Изнутри флота
# отличить их невозможно: у нас 4 боевых deadman'а, и если cron перестанет их вызывать,
# мы узнаем об этом только когда бот встанет и никто не крикнет. Ложная уверенность.
#
# ЧТО ДЕЛАЕТ. Берёт ВНЕШНЮЮ истину — journalctl службы cron, где каждый запуск логируется
# СИСТЕМОЙ без всякого участия самой задачи. Считает возраст последнего запуска каждой
# зарегистрированной задачи и сравнивает с её каденцией. Ни одна задача не обязана
# сотрудничать: скрипты флота НЕ ПРАВИЛИСЬ (важно за 3 дня до окна midnight 31.07).
#
# ПОРОГ = каденция × 2 + 120с. Двойка — правило «не писал дольше 2× каденса»; +120с —
# та же поправка на джиттер cron, что в ~/.midnight-bot/deadman.sh (иначе задача с
# каденцией 60с флапает на каждой секунде опоздания).
#
# ═══ FAIL-SAFE ПО КОНСТРУКЦИИ (главное; сюда смотреть при любой правке) ═══
# «Всё хорошо» ТРЕБУЕТ ПОЛОЖИТЕЛЬНОГО ДОКАЗАТЕЛЬСТВА. Флаг VERIFIED поднимается ровно один
# раз — в самом конце, после того как ВСЕ задачи реестра проверены. Любой обрыв на любом
# шаге оставляет VERIFIED=0, и trap на EXIT превращает это в ТРЕВОГУ. Молча пройти нельзя.
#
# ПОЧЕМУ ИМЕННО ТАК (реальный баг первой версии, пойман тестом 28.07 — не гипотеза).
# throttled() считала $(( now - $(stat -c %Y $STAMP) )). Когда stat недоступен, подстановка
# пуста, выражение вырождается в «now - » = ФАТАЛЬНАЯ арифметическая ошибка bash. Bash при
# ней РАЗМАТЫВАЕТ ВЕСЬ ОБЪЕМЛЮЩИЙ `if`: были пропущены tg, touch, запись state И `exit 1`.
# Выполнение провалилось в нормальный путь, там ошибка повторилась и приземлилась на ветку
# восстановления — скрипт отправил «✅ все задачи тикают» БУДУЧИ ПОЛНОСТЬЮ СЛЕПЫМ. Это ровно
# тот отказ, против которого весь скрипт. Поэтому: (1) арифметика только над проверенными
# числами, (2) «здорово» — не отсутствие тревоги, а доказанный факт.
#
# ЧЕГО ЭТОТ СКРИПТ НЕ УМЕЕТ (честная граница, не чините её видимостью покрытия):
#  * Журнал держит ~2.3 суток (persistent, но кап по размеру). Задачи с каденцией реже
#    суток проверить им НЕЛЬЗЯ: epbs_monitor (3 сут), chainwatch (неделя), base_replay,
#    shadow_weekly, usde_forecast (неделя). Они СОЗНАТЕЛЬНО вне реестра. Артефакт-mtime
#    их не спасает: chainwatch/state.json протух на 5.4 сут, потому что задача может
#    отработать и ничего не записать — такой «пульс» врал бы. Правильное продолжение —
#    дописать в их cron-строки `; echo "$(date +%s) имя" >> ~/.fleet-watch/heartbeats`
#    (правка crontab, отложена до после окна 31.07).
#  * Кто сторожит сторожа. Скрипт зарегистрирован в собственном реестре и ловит свою
#    ЧАСТИЧНУЮ деградацию (запускался, но реже). Полный «не запускался вообще» изнутри
#    хоста не ловится в принципе — нужен наблюдатель ВНЕ хоста. Пока это закрыто видимым
#    следом: cronwatch.log + cronwatch.state с меткой времени.
#
# ЗАПУСК:  cronwatch.sh           — проверка (cron)
#          cronwatch.sh selftest  — самопроверка, TG физически недостижим (форсит CW_MUTE=1)
set -u

# Пути переопределяемы из окружения — ЧТОБЫ selftest НЕ ТРОГАЛ БОЕВЫЕ АРТЕФАКТЫ.
# (Поймано 28.07: тест оставлял в боевом cronwatch.state "verified:false, blind" — файл
#  состояния, который врёт из-за прогона теста, это та же ложная уверенность.)
DIR=${CW_DIR:-/home/claude-agent/.fleet-watch}
LOG=${CW_LOG:-$DIR/cronwatch.log}
STATE=${CW_STATE:-$DIR/cronwatch.state}
STAMP=${CW_STAMP:-$DIR/.cronwatch_alerted}
THROTTLE=3600
WINDOW="-12h"          # ПЕРЕСЧИТЫВАЕТСЯ ниже из реестра — правьте там, не здесь
GRACE=120
MIN_CMD_LINES=50       # меньше этого за окно = журнал пуст/обрезан = мы слепы

# имя|подстрока cron-команды|каденция, с
REGISTRY=(
  "deadman-wc|wc-bot/deadman.sh|600"
  "deadman-katana|katana-bot/deadman.sh|600"
  "deadman-hyperlend|hyperlend-bot/deadman.sh|600"
  "deadman-midnight|midnight-bot/deadman.sh|600"
  "netwatch|fleet-watch/netwatch.sh|120"
  "target-watch|fleet-watch/target-watch.py|900"
  "mglo-watch|chainwatch/mglo_watch.py|3600"
  "sow-watch|chainwatch/sow_watch.py|21600"
  "disloc-watch|chainwatch/disloc_watch.py|120"
  "usde-watch|liquidator/state/usde_cron.sh|3600"
  "usde-hbcheck|liquidator/state/usde_hbcheck.sh|600"
  "shadow-watch-katana|katana-probe/shadow_watch.py|900"
  "exec-wc|wc-executor.lock|60"
  "exec-katana|katana-executor.lock|60"
  "exec-hyperlend|hyperlend-executor.lock|60"
  "exec-midnight|midnight-executor.lock|60"
  "cronwatch|fleet-watch/cronwatch.sh|900"
)

# Окно выборки ОБЯЗАНО перекрывать самый длинный порог реестра (каденция×2+GRACE). Иначе
# долгоживущая задача не может доказать, что тикала: 30.07 в реестр добавили суточный
# sow-watch (86400с ⇒ порог 48ч), а окно осталось 12-часовым — «НЕ ЗАПУСКАЛСЯ» прилетало бы
# каждый день по построению, независимо от здоровья задачи. Ложная тревога сторожа стоит
# дороже молчания: на неё перестают смотреть.
_max_cad=0
for _e in "${REGISTRY[@]}"; do
  _c=${_e##*|}
  case "$_c" in (''|*[!0-9]*) _c=0 ;; esac
  [ "$_c" -gt "$_max_cad" ] && _max_cad=$_c
done
_need_sec=$(( _max_cad * 2 + GRACE ))
_need_h=$(( (_need_sec + 3599) / 3600 ))
[ "$_need_h" -lt 12 ] && _need_h=12
WINDOW="-${_need_h}h"

VERIFIED=0     # поднимается ТОЛЬКО после полной проверки всего реестра
ALARMED=0      # тревога уже отправлена в этом прогоне

now=$(date -u +%s 2>/dev/null); now=${now:-0}

log() { echo "$(date -u '+%F %T' 2>/dev/null) $*" >> "$LOG"; }

tg() {  # $1=текст. МЬЮТ НА ТРАНСПОРТЕ: при CW_MUTE=1 curl физически не вызывается.
  if [ "${CW_MUTE:-0}" = "1" ]; then
    echo "[tg muted] $1" >> "$LOG"; return 0
  fi
  local token chat
  token=$(grep '^TELEGRAM_BOT_TOKEN=' /home/claude-agent/.claude/channels/telegram/.env 2>/dev/null | cut -d= -f2-)
  chat=$(grep -E '^(export )?MN_CHAT_ID=' /home/claude-agent/.midnight-bot/env 2>/dev/null | head -1 | cut -d= -f2- | awk '{print $1}')
  [ -n "${chat:-}" ] || chat=265715923
  [ -n "${token:-}" ] || { echo "[tg НЕТ ТОКЕНА] $1" >> "$LOG"; return 1; }
  # Ответ API НЕ глушим: молча не дошедшая тревога — тот же мёртвый сторож, только
  # на уровень ниже. Провал доставки обязан оставить след в логе.
  local resp
  resp=$(curl -sm 10 "https://api.telegram.org/bot$token/sendMessage" \
    --data-urlencode "chat_id=$chat" --data-urlencode "text=$1" 2>&1)
  case "$resp" in
    *'"ok":true'*) return 0 ;;
    *) echo "[tg НЕ ДОСТАВЛЕНО] ${resp:0:200}" >> "$LOG"; return 1 ;;
  esac
}

# КРАШ-БЕЗОПАСНО: ни одной арифметики над непроверенной подстановкой (это и был баг).
throttled() {
  [ -f "$STAMP" ] || return 1
  local m; m=$(stat -c %Y "$STAMP" 2>/dev/null)
  case "$m" in (''|*[!0-9]*) return 1 ;; esac        # не число — считаем, что не троттлим
  case "$now" in (''|*[!0-9]*) return 1 ;; esac
  [ $(( now - m )) -lt "$THROTTLE" ]
}

alarm() {  # $1=текст. Троттлится; помечает прогон как отревоженный.
  ALARMED=1
  throttled && return 0
  tg "$1"
  : > "$STAMP" 2>/dev/null || true
}

# ЛОВУШКА: любой выход без доказанного VERIFIED — тревога, а не тишина.
finish() {
  local rc=$?
  if [ "$VERIFIED" -ne 1 ] && [ "$ALARMED" -ne 1 ]; then
    log "АВАРИЙНЫЙ ВЫХОД rc=$rc — проверка НЕ завершена"
    printf '{"ts":%s,"verified":false,"reason":"aborted","rc":%s}\n' "$now" "$rc" > "$STATE" 2>/dev/null
    alarm "🔴 [cronwatch] оборвался, не завершив проверку (rc=$rc). Надзор за сторожами НЕ подтверждён — смотреть $LOG."
  fi
}
trap finish EXIT

# ── САМОПРОВЕРКА ────────────────────────────────────────────────────────────────
if [ "${1:-}" = "selftest" ]; then
  export CW_MUTE=1                      # мьют на транспорте, форсированно
  fails=0
  prod_state=$(cat "$DIR/cronwatch.state" 2>/dev/null)   # снимок ДО тестов, сверяется после
  t() { # $1=имя $2=ожидаемый код $3...=команда
    local name=$1 want=$2; shift 2
    "$@" >/dev/null 2>&1; local got=$?
    if [ "$got" = "$want" ]; then echo "  ok   $name (код $got)"
    else echo "  FAIL $name: ожидался код $want, получен $got"; fails=$((fails+1)); fi
  }
  echo "selftest cronwatch (CW_MUTE=1 — живой TG физически недостижим)"
  empty=$(mktemp -d)
  sand=$(mktemp -d)                     # песочница: боевые log/state/stamp НЕ трогаем
  SLOG=$sand/log; SSTATE=$sand/state; SSTAMP=$sand/stamp
  probe() { env PATH="$empty" CW_MUTE=1 CW_DIR="$sand" CW_LOG="$SLOG" \
                CW_STATE="$SSTATE" CW_STAMP="$SSTAMP" "$0" _blindprobe; }
  # 1) слепота обязана дать код 1, а НЕ 0 — регрессия бага 28.07
  rm -f "$SSTAMP"
  t "слепой путь (нет journalctl), штамп снят"  1 probe
  rm -f "$SSTAMP"
  t "слепой путь, штамп снят повторно"          1 probe
  : > "$SSTAMP"
  t "слепой путь ПРИ взведённом штампе"         1 probe
  # 2) throttled не должна падать при недоступном stat
  t "throttled краш-безопасна без stat"         0 env PATH="$empty" CW_MUTE=1 /bin/bash -c \
      "STAMP=$SSTAMP; now=$now; THROTTLE=3600; $(declare -f throttled); throttled; true"
  # 3) боевые артефакты обязаны остаться нетронутыми
  t "боевой state не тронут тестом"             0 test "$(cat "$DIR/cronwatch.state" 2>/dev/null)" = "$prod_state"
  rm -rf "$sand"; rmdir "$empty" 2>/dev/null
  echo "провалов: $fails"
  VERIFIED=1; ALARMED=1
  [ "$fails" -eq 0 ] || exit 1
  exit 0
fi

# внутренний режим для selftest: тот же путь, но гарантированно без сети
[ "${1:-}" = "_blindprobe" ] && WINDOW="-12h"

# ── ИСТОЧНИК ИСТИНЫ ─────────────────────────────────────────────────────────────
SNAP=$(journalctl -u cron --since "$WINDOW" --no-pager -o short-unix 2>/dev/null | grep 'CMD (' 2>/dev/null)
lines=$(printf '%s\n' "$SNAP" | grep -c 'CMD (' 2>/dev/null)
case "$lines" in (''|*[!0-9]*) lines=0 ;; esac

# ПАДАТЬ ГРОМКО: нет журнала — нет проверки. Это тревога, а не тишина.
if [ "$lines" -lt "$MIN_CMD_LINES" ]; then
  log "СЛЕП: journalctl дал $lines строк CMD за $WINDOW (порог $MIN_CMD_LINES)"
  printf '{"ts":%s,"verified":false,"reason":"blind","lines":%s}\n' "$now" "$lines" > "$STATE" 2>/dev/null
  alarm "🔴 [cronwatch] СЛЕП: journalctl дал $lines строк CMD за $WINDOW. Надзор за сторожами НЕ РАБОТАЕТ — проверить journald/права."
  exit 1
fi

# ── ПРОВЕРКА РЕЕСТРА ────────────────────────────────────────────────────────────
# Насколько ГЛУБОК журнал на самом деле: расширить окно запросом мало — journald мог хранить
# меньше. Если журнал короче порога задачи, её смерть НЕДОКАЗУЕМА, и объявлять её мёртвой
# нельзя: это ровно та ложная тревога, из-за которой на сторожа перестают смотреть.
oldest=$(printf '%s\n' "$SNAP" | head -1 | cut -d. -f1)
case "$oldest" in (''|*[!0-9]*) oldest=$now ;; esac
span=$(( now - oldest ))

bad=""; report=""; unproven=""
for e in "${REGISTRY[@]}"; do
  name=${e%%|*}; rest=${e#*|}; pat=${rest%%|*}; cad=${rest##*|}
  last=$(printf '%s\n' "$SNAP" | grep -F "$pat" 2>/dev/null | tail -1 | cut -d. -f1)
  case "$last" in (''|*[!0-9]*) last="" ;; esac
  limit=$(( cad * 2 + GRACE ))
  if [ -z "$last" ]; then
    age=-1
    if [ "$span" -lt "$limit" ]; then
      unproven="$unproven $name(${span}с<${limit}с)"
    else
      bad="$bad\n  🔴 $name — НЕ ЗАПУСКАЛСЯ за ${WINDOW#-} (каденция ${cad}с)"
    fi
  else
    age=$(( now - last ))
    [ "$age" -gt "$limit" ] && \
      bad="$bad\n  ⚠️ $name — молчит ${age}с > порога ${limit}с (каденция ${cad}с)"
  fi
  report="$report$name=${age}s/${cad}s "
done

log "проверено ${#REGISTRY[@]} задач | окно ${WINDOW#-} глубина журнала ${span}с | $report"
[ -n "$unproven" ] && log "НЕДОКАЗУЕМО (журнал короче порога задачи):$unproven"

if [ -n "$bad" ]; then
  printf '{"ts":%s,"verified":true,"checked":%s,"bad":true}\n' "$now" "${#REGISTRY[@]}" > "$STATE" 2>/dev/null
  VERIFIED=1
  alarm "$(printf '💀 [cronwatch] СТОРОЖА НЕ ТИКАЮТ:%b\n\nПорог = каденция×2+120с. Источник — journalctl cron (внешний, задачи в нём не участвуют).' "$bad")"
  exit 2
fi

# всё проверено и всё в норме — единственная точка, где это можно утверждать
printf '{"ts":%s,"verified":true,"checked":%s,"bad":false}\n' "$now" "${#REGISTRY[@]}" > "$STATE" 2>/dev/null
VERIFIED=1
if [ -f "$STAMP" ]; then
  rm -f "$STAMP"
  tg "✅ [cronwatch] все ${#REGISTRY[@]} задач снова тикают в срок."
fi
exit 0
