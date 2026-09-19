#!/bin/bash
# ПОЧЕМУ ЭТОТ ФАЙЛ ЕСТЬ
# 19.09.2026 хост на ~5 минут перестал исполнять крон ЦЕЛИКОМ (своп 1952/2047 МБ,
# free 219 МБ, load 17.5). Разбор 12:52Z уткнулся в названный предел: ЧТО именно съело
# память в окне 12:25..12:29 ретроспективно НЕ ВОССТАНОВИМО — телеметрии RSS по времени
# на машине нет. Без неё следующий затор будет так же необъясним, а боевое окно midnight
# 25.09 идёт с HOT-циклом 5с: такой же затор ударит уже по боту, а не по сторожу.
#
# Прибор, а не сторож: НИЧЕГО не решает и НИКОГО не будит — только пишет строку в минуту.
# Тревоги по затору звонит cronwatch (ключ cronwatch:stall, коммит f72bd9e).
#
# ЦЕНА (ЗАМЕРЕНО 19.09 через /usr/bin/time -v, не на глаз): 0.03с по стенке, 0.02с CPU,
# пик RSS 4.4 МБ на прогон. Чистый shell, python не поднимается.
# Прибор на машине со 119 МБ свободного ОЗУ обязан быть дешевле наблюдаемого эффекта,
# иначе он сам становится причиной (память single-core-observer-effect).
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=${HM_LOG:-$DIR/hostmem.log}
MAXBYTES=${HM_MAXBYTES:-5242880}   # 5 МБ; при ЗАМЕРЕННОЙ длине строки 190б это ~19 суток,
                                   # после усечения остаётся половина. Хвост важнее истории.
TOPN=${HM_TOPN:-3}

read -r _ memtotal memfree memavail swaptotal swapfree < <(
  awk '/^MemTotal:/{mt=$2} /^MemFree:/{mf=$2} /^MemAvailable:/{ma=$2}
       /^SwapTotal:/{st=$2} /^SwapFree:/{sf=$2}
       END{printf "x %d %d %d %d %d\n", mt/1024, mf/1024, ma/1024, st/1024, sf/1024}' /proc/meminfo
)
read -r l1 l5 l15 procs _ < <(awk '{print $1, $2, $3, $4}' /proc/loadavg)

# Топ-N держателей RSS. ps без сортировки ядром дешевле, сортируем сами.
top=$(ps -eo rss=,pid=,comm= --sort=-rss 2>/dev/null | head -n "$TOPN" |
      awk '{printf "%s:%s=%dM ", $2, $3, $1/1024}')

# Счётчик ухода в своп с момента загрузки: растущий pswpin/pswpout = машина молотит диск,
# а не просто держит холодные страницы в свопе (высокий swap used сам по себе безвреден).
read -r pswpin pswpout < <(awk '/^pswpin/{i=$2} /^pswpout/{o=$2} END{print i, o}' /proc/vmstat)

printf '%s free=%dM avail=%dM swapfree=%d/%dM load=%s/%s/%s procs=%s pswpin=%s pswpout=%s top=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$memfree" "$memavail" "$swapfree" "$swaptotal" \
  "$l1" "$l5" "$l15" "$procs" "$pswpin" "$pswpout" "${top% }" >> "$LOG"

# Ротация усечением С ГОЛОВЫ, ЧЕРЕЗ ТОТ ЖЕ INODE (cat >, не mv): mv подменил бы inode и
# читатели с открытым дескриптором продолжили бы читать отвязанный файл. Режем по СТРОКАМ
# (tail -n), а не по байтам: -c оставил бы первую строку обрезанной с головы.
if [ -f "$LOG" ]; then
  sz=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
  if [ "$sz" -gt "$MAXBYTES" ]; then
    keep=$(( MAXBYTES / 2 / 200 ))   # ~200 байт на строку
    if tail -n "$keep" "$LOG" > "$LOG.trim" 2>/dev/null; then
      cat "$LOG.trim" > "$LOG" && rm -f "$LOG.trim"
    fi
  fi
fi
