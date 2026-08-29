#!/usr/bin/env python3
"""ТРЕВОГА НА ПРИЗОВОЙ БЛОК (27.08, по требованию kelbic).

ЗАЧЕМ. В TG уходили только НАШИ выстрелы, «мы отказались — забрал конкурент» и аварийные стопы.
Приз, случившийся там, где мы просто молчали, не порождал НИ ОДНОЙ записи — а это ровно та
величина, которой мы теперь меряем П.0: сколько призовых блоков прошло и в скольких мы были.
Плюс закрывает вторую дыру: рынков Morpho на Base 3 078, у нас в покрытии 41 (из непокрытых 116
с живым долгом). Ожившый непокрытый рынок иначе остался бы незамеченным.

ЧТО ДЕЛАЕТ. Раз в запуск читает Liquidate-логи Morpho от последнего виденного блока, и на каждый
призовой блок шлёт строку: рынок, сколько ликвидаций, сколько погашено, НАШИХ проб в блоке,
покрыт ли рынок. Наша победа помечается отдельно и громко.

ПОЧЕМУ ЧЕРЕЗ SSH. С машины агента публичная нода Base отдаёт 403; читаем через боевую (только
чтение), её RPC-адрес НЕ печатаем. Сам бот при этом не трогаем — прибор живёт СБОКУ.

Состояние: last_block в ~/.fleet-watch/prize_watch.state — при первом запуске берём голову минус
POLL_LOOKBACK, чтобы не выплюнуть недельную историю разом.
"""
import json
import os
import subprocess
import sys
import time

HOST = "root@185.173.146.134"
STATE = os.path.expanduser("~/.fleet-watch/prize_watch.state")
LOOKBACK = int(os.environ.get("PRIZE_WATCH_LOOKBACK", "300"))      # блоков при первом запуске
MAX_SPAN = int(os.environ.get("PRIZE_WATCH_MAX_SPAN", "9000"))     # не читать больше за раз
OUR = "0x5f34e0b1e716ff5f5c77f9c0b176a889215d39ed"   # СВИП-доска с 28.08; старая 0x8f92cac0 считается как OUR2

REMOTE = r'''
import json,urllib.request,sys
RPC=None
for f in ("/root/liquidator/.env","/root/liquidator/.env.g2d"):
    try:
        for ln in open(f):
            if ln.startswith("RPC_URL="): RPC=ln.split("=",1)[1].strip()
    except Exception: pass
def call(m,p):
    r=urllib.request.urlopen(urllib.request.Request(RPC,json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),{"Content-Type":"application/json"}),timeout=60)
    return json.load(r).get("result")
M="0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb"
LIQ="0xa4946ede45d0c6f06a0f5ce92c9ad3b4751452d2fe0e25010783bcab57a67e41"
OUR="%s"
OUR2="0x8f92cac0b6586f834de33cf31819db0ebf6e52b9"
lo=int(sys.argv[1]); head=int(call("eth_blockNumber",[]),16)
hi=min(head, lo+%d)
out={"head":head,"lo":lo,"hi":hi,"blocks":{},"cov":None}
try:
    out["cov"]=[m["market_id"].lower() for m in json.load(open("/root/liquidator/covered_markets.json"))]
except Exception: pass
lg=call("eth_getLogs",[{"fromBlock":hex(lo),"toBlock":hex(hi),"address":M,"topics":[LIQ]}]) or []
AU="0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f"
for x in lg:
    b=int(x["blockNumber"],16); mid=x["topics"][1]
    d=out["blocks"].setdefault(str(b),{"mid":{},"ours":None,"caller":{}})
    d.setdefault("liq_idx",[]).append(int(x["transactionIndex"],16))
    d["mid"][mid]=d["mid"].get(mid,0)+1
    d["caller"]["0x"+x["topics"][2][-40:]]=1
    d.setdefault("raw",0)
    d["raw"]+=int(x["data"][2:66],16)
# ПРИЗ, А НЕ ПОГАШЕНИЕ. 28.08: тревога печатала repaidAssets ("погашено ~$3") и человек
# читал это как размер приза. Настоящий приз = бонус ликвидатора = repaid*(LIF-1), где
# LIF = 1/(1-0.3*(1-lltv)) — тождество Morpho, сошлось на четырёх событиях до 6 знаков.
def _sym(a):
    try:
        x=call("eth_call",[{"to":a,"data":"0x95d89b41"},"latest"]); n=int(x[66:130],16)
        return bytes.fromhex(x[130:130+n*2]).decode(errors="replace")
    except Exception: return a[:8]
for mid in set(m for d in out["blocks"].values() for m in d["mid"]):
    try:
        r=call("eth_call",[{"to":M,"data":"0x2c3c9157"+mid[2:]},"latest"])
        lltv=int(r[2+4*64:2+5*64],16)/10**18
        out.setdefault("lif",{})[mid]=1.0/(1.0-0.3*(1.0-lltv)) if lltv>0 else None
        # ПАРА РЫНКА (kelbic 28.08: «иначе непонятно что это»)
        out.setdefault("pair",{})[mid]="%%s/%%s" %% (_sym("0x"+r[2+64+24:2+2*64]), _sym("0x"+r[26:66]))
    except Exception: out.setdefault("lif",{})[mid]=None
import subprocess, datetime as _dt
def _journal_state(ts):
    """Что делал бот в ±60с от блока: было ли ОТКРЫТО окно (window open/armed) и сколько отправок."""
    try:
        lo=_dt.datetime.utcfromtimestamp(ts-60).strftime("%%Y-%%m-%%d %%H:%%M:%%S"); hi=_dt.datetime.utcfromtimestamp(ts+60).strftime("%%Y-%%m-%%d %%H:%%M:%%S")
        L=subprocess.run(["journalctl","-u","liquidator-bot","--since",lo,"--until",hi,"--no-pager","-o","cat"],capture_output=True,text=True,timeout=60).stdout
        opened=len([1 for l in L.splitlines() if "SPECFIRE window" in l and ("open" in l or "armed" in l or "arm " in l)])
        closed=len([1 for l in L.splitlines() if "window close" in l])
        sent=len([1 for l in L.splitlines() if "probe #" in l or "volley leg=" in l or "ACTIONABLE" in l])
        skip=len([1 for l in L.splitlines() if "ОТКЛОНЁН" in l or "вне досягаемости" in l or "не собралась" in l])
        return {"opened":opened,"closed":closed,"sent":sent,"skip":skip}
    except Exception as e:
        return {"err":type(e).__name__}
for b,d in out["blocks"].items():
    blk=call("eth_getBlockByNumber",[hex(int(b)),True]) or {}
    try: d["bot"]=_journal_state(int(blk.get("timestamp","0x0"),16))
    except Exception: d["bot"]={"err":"ts"}
    _our_idx=[int(t["transactionIndex"],16) for t in blk.get("transactions",[])
              if (t.get("to") or "").lower() in (OUR,OUR2)]
    d["ours"]=len(_our_idx)
    d["total"]=len(blk.get("transactions",[]))
    # ГЕОМЕТРИЯ ГОНКИ (мера kelbic 28.08): не «попали ли в класс», а СКОЛЬКО ЧУЖИХ встало
    # между транзитом и нашей первой ногой. Порядок внутри флешблока — по убыванию tip,
    # поэтому «сразу за транзитом» и есть призовое место, а чужие в зазоре — прямая мера промаха.
    try:
        _au=call("eth_getLogs",[{"fromBlock":hex(int(b)),"toBlock":hex(int(b)),"topics":[AU]}]) or []
        _tr=sorted({int(z["transactionIndex"],16) for z in _au})
    except Exception:
        _tr=[]
    d["transit_idx"]=_tr
    d["our_idx"]=sorted(_our_idx)[:3]
    d["win_idx"]=sorted(d.get("liq_idx") or [])
    def _behind(idx, trs):
        pre=[t for t in trs if t<=idx]
        return (max(pre) if pre else None)
    if _tr and _our_idx:
        _o=min(_our_idx); _t=_behind(_o,_tr)
        d["our_delta"]= (_o-_t) if _t is not None else None
        d["our_strangers"]= (_o-_t-1) if _t is not None else None
        d["our_transit"]=_t          # ИМЕННО этот транзит судил дельту — его и печатать
    if _tr and d.get("liq_idx"):
        _w=min(d["liq_idx"]); _t=_behind(_w,_tr)
        d["win_delta"]= (_w-_t) if _t is not None else None
        d["win_strangers"]= (_w-_t-1) if _t is not None else None
        d["win_transit"]=_t
    # KPI kelbic 28.08: ПОЗИЦИЙ МЕЖДУ ПОБЕДИТЕЛЕМ И НАМИ — отставание от того, кто ВЗЯЛ, а не от
    # транзита. Отрицательное = мы стояли РАНЬШЕ победителя (и всё равно не взяли — значит цель/тип).
    if d.get("liq_idx") and _our_idx:
        _w=min(d["liq_idx"]); _o=min(_our_idx)
        d["gap_to_winner"]=_o-_w
    # KPI, когда нас в призовом блоке НЕТ: опоздание в БЛОКАХ. 28.08 гонка 50572467 ($2 375) — наш
    # выстрел сел в +1 блок, 50576516 — в +11 (22 с); без этой строки оба читались как «нас не было».
    if d.get("liq_idx") and not _our_idx:
        d["our_next"]=None
        for _k in range(1,13):
            try:
                _nb=call("eth_getBlockByNumber",[hex(int(b)+_k),True]) or {}
            except Exception:
                break
            _ni=[int(t["transactionIndex"],16) for t in _nb.get("transactions",[])
                 if (t.get("to") or "").lower() in (OUR,OUR2)]
            if _ni:
                d["our_next"]=[_k,min(_ni),len(_nb.get("transactions",[]))]
                break
print(json.dumps(out))
''' % (OUR, MAX_SPAN)


def _load_state(head):
    try:
        return int(open(STATE).read().strip())
    except Exception:                                   # noqa: BLE001
        return max(0, head - LOOKBACK)


def main():
    sys.path.insert(0, os.path.expanduser("~/.fleet-watch"))
    from notify import notify
    # --dry: печатать вместо отправки. Нужен для ПОЗИТИВНОГО КОНТРОЛЯ детектора на известном
    # призовом блоке: без него молчание прибора неотличимо от «призов не было».
    dry = "--dry" in sys.argv
    if dry:
        _real = notify
        def notify(text, **kw):                          # noqa: F811
            print("[dry] %s | key=%s hil=%s" % (text.replace("\n", " / "), kw.get("key"), kw.get("hil")))
            return "dry"
    # --from N: начать с этого блока (только вместе с --dry)
    _from = None
    for i, a in enumerate(sys.argv):
        if a == "--from" and i + 1 < len(sys.argv):
            _from = int(sys.argv[i + 1])
    # ЗАЩИТА ОТ НУЛЕВОГО КОНТРОЛЯ: --from берёт АБСОЛЮТНЫЙ номер блока, а рука тянется подать
    # смещение («--from 800» = «800 блоков назад»). Тогда прогон уходит в блоки 800..9800, где
    # Morpho ещё нет, печатает пусто и rc=0 — молчание прибора неотличимо от «призов нет».
    # Такой прогон я принял за позитивный контроль 27.08. Теперь он падает вслух.
    _dry_from_guard = _from
    # ПОКРЫТИЕ. Незнание НЕ ДОЛЖНО молча становиться приговором «вне покрытия»: первая
    # редакция глотала ошибку чтения в пустое множество, и позитивный контроль показал
    # cbXRP (покрытый!) как «ВНЕ ПОКРЫТИЯ». Теперь cov=None означает «не знаем», и в строке
    # пишется именно это.
    # ПОКРЫТИЕ приходит С БОЕВОЙ МАШИНЫ тем же запросом: covered_markets.json живёт там, а не
    # в репозитории (первая редакция читала несуществующий путь, глотала ошибку в пустое
    # множество и печатала покрытый cbXRP как «ВНЕ ПОКРЫТИЯ» — незнание молча стало приговором).
    cov = None
    # первый вызов только чтобы узнать голову
    probe = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", HOST,
                            "/root/liquidator/venv/bin/python - 0"],
                           input=REMOTE, capture_output=True, text=True, timeout=180)
    if probe.returncode != 0 or not probe.stdout.strip():
        # ТРАНСПОРТ МОЛЧИТ — это НЕ «призов нет». Молчание прибора обязано быть слышно.
        notify("prize-watch: чтение цепи не удалось (%s) — приборы молчат, призы НЕ проверены"
               % (probe.stderr or "пусто")[:120], source="prize-watch", hil=False,
               key="prize-watch:transport", dedup_sec=3600)
        return 2
    head = json.loads(probe.stdout)["head"]
    if _dry_from_guard is not None and _dry_from_guard < head - 10_000_000:
        sys.stderr.write(
            "--from %d похоже на СМЕЩЕНИЕ, а нужен абсолютный номер блока (голова %d).\n"
            "Такой прогон вернёт пусто и соврёт, что призов нет. Отказ.\n"
            % (_dry_from_guard, head))
        return 3
    lo = _from if _from is not None else _load_state(head)
    if lo >= head:
        return 0
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", HOST,
                        "/root/liquidator/venv/bin/python - %d" % lo],
                       input=REMOTE, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not r.stdout.strip():
        notify("prize-watch: второй проход не удался — призы НЕ проверены",
               source="prize-watch", hil=False, key="prize-watch:transport2", dedup_sec=3600)
        return 2
    data = json.loads(r.stdout)
    cov = set(data.get("cov") or []) or None
    if cov is None:
        notify("prize-watch: covered_markets.json не прочитан на боевой — колонка покрытия "
               "НЕДОСТОВЕРНА", source="prize-watch", hil=False,
               key="prize-watch:cov-unreadable", dedup_sec=21600)
    for b in sorted(data["blocks"], key=int):
        d = data["blocks"][b]
        n = sum(d["mid"].values())
        def _mark(m):
            if cov is None:
                return " покрытие?"
            return "" if m.lower() in cov else " ВНЕ ПОКРЫТИЯ"
        _pairs = data.get("pair") or {}
        mids = ", ".join("%s %s x%d%s" % (_pairs.get(m, "?"), m[:10], c, _mark(m)) for m, c in d["mid"].items())
        we_won = OUR.lower() in {k.lower() for k in d["caller"]}
        usd = d.get("raw", 0) / 1e6           # заём почти везде USDC; WETH-рынки завысят — назван
        head_line = ("\U0001F3C6 НАША ПОБЕДА" if we_won else
                     ("\U0001F3AF мы БЫЛИ в призовом блоке" if d["ours"] else
                      "\U0001F4A4 приз мимо: нас в блоке НЕ БЫЛО"))
        # приз считаем по LIF того рынка, где была ликвидация (если рынков несколько — берём
        # минимальный LIF, чтобы НЕ ЗАВЫСИТЬ; незнание LIF закрывает претензию на число, а не гард)
        _lifs = [v for k, v in (data.get("lif") or {}).items() if k in d["mid"] and v]
        _lif = min(_lifs) if _lifs else None
        _prize = (usd * (_lif - 1.0)) if _lif else None
        _ptxt = ("приз ~$%.2f (брутто, до свопа и газа)" % _prize) if _prize is not None \
                else "приз НЕИЗВЕСТЕН (не прочитан lltv)"
        # ГЕОМЕТРИЯ: чужих между транзитом и нами — прямая мера промаха по месту
        # KPI (kelbic 28.08) печатается ВСЕГДА, когда есть победитель: и с транзитом, и без него
        # (28.08 гонка 50576516: таймерная ликвидация без AnswerUpdated — KPI 34 был посчитан, но
        # строка жила только в ветке «транзит есть» и в тревогу не попала).
        _gw = d.get("gap_to_winner")
        _nx = d.get("our_next")
        if _gw is not None:
            _gwt = ("\nKPI: между победителем и нами %d позиций" % _gw if _gw > 0 else
                    "\nKPI: мы стояли РАНЬШЕ победителя на %d — проиграли не местом, а целью/типом" % (-_gw))
        elif d.get("liq_idx") and _nx:
            _gwt = ("\nKPI: нас в призовом блоке НЕТ — первая наша tx через +%d блок(а) (~%d с), tx#%d из %d"
                    % (_nx[0], 2 * _nx[0], _nx[1], _nx[2]))
        elif d.get("liq_idx"):
            _gwt = "\nKPI: нас нет ни в призовом блоке, ни в 12 следующих"
        else:
            _gwt = ""
        if d.get("transit_idx"):
            _us = ("+%d (чужих между: %d)" % (d["our_delta"], d["our_strangers"])
                   if d.get("our_delta") is not None else "нас за транзитом НЕТ")
            _wn = ("+%d (чужих: %d)" % (d["win_delta"], d["win_strangers"])
                   if d.get("win_delta") is not None else "ДО первого апдейта в блоке (цена сменилась раньше или таймерная)")
            _tref = d.get("our_transit", d.get("win_transit"))
            _geo = ("\nгеометрия: транзит idx %s (всего апдейтов %d) | победитель %s | мы %s%s"
                    % (_tref if _tref is not None else "—", len(d["transit_idx"]), _wn, _us, _gwt))
        else:
            _geo = ("\nгеометрия: транзита в блоке НЕТ (таймерная ликвидация либо фид без AnswerUpdated)"
                    + _gwt)
        # (28.08 kelbic: строку «ПОЧЕМУ НАС НЕ БЫЛО» в тревогу не добавлять — формат гонки оставить как есть)

        notify("%s\nблок %s: %s | погашено ~$%.0f (размер позиции, НЕ приз)\n"
               "ликвидаций %d (%s)\nнаших проб в блоке: %d из %d tx блока%s"
               % (head_line, b, _ptxt, usd, n, mids, d["ours"], d.get("total", 0), _geo),
               source="prize-watch", hil=True, key="prize-watch:%s" % b, dedup_sec=86400)
    if not dry:
        with open(STATE, "w") as f:
            f.write(str(data["hi"] + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
