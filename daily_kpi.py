#!/usr/bin/env python3
"""KPI владельца за 24ч — «позиций между ПОБЕДИТЕЛЕМ и нами» — из журнала prize_watch.
Идёт отдельной строкой ВСЛЕД за боевой сводкой (та бежит на боевой машине и этот журнал
не видит). Ноль гонок с нашим участием — тоже ответ, и он печатается, а не молчит."""
import re, sys, time, datetime as dt
sys.path.insert(0, "/home/claude-agent/.fleet-watch")
LOG = "/home/claude-agent/.fleet-watch/prize-watch.log"
since = time.time() - 86400
gaps, ahead, blocks, ours_in = [], [], 0, 0
cur_ts = None
for ln in open(LOG, errors="replace"):
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)Z", ln)
    if m:
        cur_ts = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    if cur_ts is None or cur_ts < since:
        continue
    if "блок " in ln and "ликвидаций" in ln:
        blocks += 1
    if "мы БЫЛИ в призовом блоке" in ln:
        ours_in += 1
    g = re.search(r"между победителем и нами (\d+) позиций", ln)
    if g: gaps.append(int(g.group(1)))
    a = re.search(r"РАНЬШЕ победителя на (\d+)", ln)
    if a: ahead.append(int(a.group(1)))
if blocks == 0:
    line = "KPI победитель→мы: призовых блоков за 24ч НЕТ"
elif not gaps and not ahead:
    line = "KPI победитель→мы: призовых блоков %d, нас в них 0 — отставание НЕ ИЗМЕРИМО (не были в блоке)" % blocks
else:
    parts = []
    if gaps:
        gs = sorted(gaps)
        parts.append("между победителем и нами: p50 %d, лучший %d (n=%d)" % (gs[len(gs)//2], gs[0], len(gs)))
    if ahead:
        parts.append("стояли РАНЬШЕ победителя: %d раз (проигрыш целью/типом, не местом)" % len(ahead))
    line = "KPI победитель→мы: " + "; ".join(parts) + " | призовых блоков %d, нас в них %d" % (blocks, ours_in)
print(line)
if "--send" in sys.argv:
    from notify import notify
    print("отправка:", notify(line, source="daily-kpi", hil=True, key="daily-kpi:%s" % dt.date.today(), dedup_sec=86400))
