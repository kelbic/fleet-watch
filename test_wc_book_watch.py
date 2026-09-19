#!/usr/bin/env python3
"""Стенд сторожа wc-book. Транспорт закрыт НА САМОМ ТРАНСПОРТЕ
([[tests-never-touch-production-channels]]): FLEET_ALERT_MUTE=1 + временные INBOX и
FLEET_DEDUP_STATE (дедуп — не канальная дверь: без подмены стенд засеял бы БОЕВЫЕ ключи
и заглушил настоящую тревогу того же состава на 40 суток), плюс имя файла test_*.
Боевые STATE/LOG/JSONL сторожа НЕ ТРОГАЮТСЯ: у него все три пути переопределяемы
из окружения, поэтому переименовывать продакшн-файлы, как у term-watch, не нужно.

Проверяется НЕ «скрипт не падает», а что тревога ФАКТИЧЕСКИ поднимается на том состоянии,
ради которого сторож поставлен, и НЕ поднимается на здоровом ([[red-bench-is-not-a-diagnosis]]):
каждый из трёх порогов гоняется В ОБЕ СТОРОНЫ, защёлка — на повтор и на перевзвод.
"""
import json, os, subprocess, sys, tempfile

WATCH = os.path.expanduser("~/.fleet-watch")
SCRIPT = os.path.join(WATCH, "wc-book-watch.py")


def run(env_patch=None, workdir=None):
    """Один прогон сторожа в песочнице. workdir переиспользуется, чтобы STATE пережил
    прогон — иначе защёлку (состояние МЕЖДУ прогонами) проверить нечем."""
    d = workdir or tempfile.mkdtemp()
    inbox = os.path.join(d, "inbox.jsonl")
    env = dict(os.environ, FLEET_ALERT_MUTE="1", FLEET_INBOX=inbox,
               FLEET_DEDUP_STATE=os.path.join(d, "dedup.json"),
               WC_BOOK_STATE=os.path.join(d, "state.json"),
               WC_BOOK_LOG=os.path.join(d, "watch.log"),
               WC_BOOK_JSONL=os.path.join(d, "watch.jsonl"))
    for k in ("WC_BOOK_BENCH", "WC_BOOK_VOL_ALARM", "WC_BOOK_POS_ALARM",
              "WC_BOOK_LIQ_N", "WC_BOOK_LIQ_USD", "WC_BOOK_API", "WC_BOOK_ANCHOR_EXTRA"):
        env.pop(k, None)
    env.update(env_patch or {})
    p = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True,
                       env=env, timeout=1800)
    alarms = []
    for f_ in (inbox, inbox + ".muted"):   # при заглушённом транспорте notify уводит
        if os.path.exists(f_):             # запись в файл-дублёр (notify.py) — читать ОБА,
            alarms += [json.loads(l)["key"] for l in open(f_)]   # иначе «тревог нет» = артефакт стенда
    return p.stdout + p.stderr, alarms, d


def has(alarms, prefix):
    """Ключ несёт хвост-состав (#<хэш>): сравнивать надо ПРЕФИКСОМ, иначе стенд зелен
    ровно там, где сторож поменял ключ."""
    return any(a.split("#")[0] == prefix for a in alarms)


def ok(name, cond, extra=""):
    print(("ПРОШЁЛ  " if cond else "ПРОВАЛ  ") + name + (" | " + extra if extra else ""))
    return cond


def main():
    fails = 0

    # 1. ЗДОРОВОЕ состояние на ЖИВЫХ данных: тревог нет, положительный контроль пройден
    out, al, _ = run()
    fails += not ok("здоровая книга — тревог нет", al == [], f"тревоги={al}")
    fails += not ok("строка census напечатана", "OK книга $" in out, out[-160:])
    fails += not ok("крупнейшая волатильная названа", "крупнейшая ВОЛАТИЛЬНАЯ:" in out)

    # 2. РУЧКИ ПОРОГОВ БЕЗ МАРКЕРА СТЕНДА ИГНОРИРУЮТСЯ: забытая переменная в окружении
    #    крона не имеет права молча подвинуть боевой порог.
    out, al, _ = run({"WC_BOOK_VOL_ALARM": "1000", "WC_BOOK_POS_ALARM": "1",
                      "WC_BOOK_LIQ_USD": "1"})
    fails += not ok("пороги без WC_BOOK_BENCH=1 не действуют", al == [], f"тревоги={al}")

    B = {"WC_BOOK_BENCH": "1"}

    # 3. ВОЛАТИЛЬНЫЙ ДОЛГ: порог ниже факта -> тревога
    out, al, _ = run({**B, "WC_BOOK_VOL_ALARM": "1000000"})
    fails += not ok("порог волатильного долга пробит -> тревога",
                    has(al, "wc-book:volatile-debt"), f"{al}")

    # 4. КРУПНАЯ ПОЗИЦИЯ: порог ниже факта -> тревога
    out, al, _ = run({**B, "WC_BOOK_POS_ALARM": "100000"})
    fails += not ok("порог крупной позиции пробит -> тревога",
                    has(al, "wc-book:big-position"), f"{al}")

    # 5. ПОТОК ЛИКВИДАЦИЙ: оба условия (И) выполнены -> тревога
    out, al, _ = run({**B, "WC_BOOK_LIQ_USD": "1000", "WC_BOOK_LIQ_N": "10"})
    fails += not ok("порог потока ликвидаций пробит -> тревога",
                    has(al, "wc-book:liq-flow"), f"{al}")

    # 5б. И, а не ИЛИ: штук много, суммы нет -> МОЛЧИТ (иначе пыль World Chain звонила бы
    #     каждый месяц: 1469 шт при сумме в десятки долларов)
    out, al, _ = run({**B, "WC_BOOK_LIQ_N": "10", "WC_BOOK_LIQ_USD": "100000000"})
    fails += not ok("много штук без суммы — молчит (И, не ИЛИ)",
                    not has(al, "wc-book:liq-flow"), f"{al}")

    # 6. ЗАЩЁЛКА ДЕРЖИТ МЕСЯЦ, А НЕ СЕКУНДУ. Два прогона по ОДНОМУ снимку API зелены по
    #    построению: между настоящими месячными прогонами каждое число дрейфует (проценты
    #    капают), и защёлка, чей состав собран из точных долларов, звонила бы каждый месяц.
    #    Поэтому второй прогон идёт с ДРЕЙФОМ +2% по всем денежным величинам и обязан
    #    молчать по ВСЕМ ТРЁМ порогам ([[red-bench-is-not-a-diagnosis]]).
    LOW = {**B, "WC_BOOK_VOL_ALARM": "1000000", "WC_BOOK_POS_ALARM": "100000",
           "WC_BOOK_LIQ_N": "10", "WC_BOOK_LIQ_USD": "1000"}
    out, al, d = run(LOW)
    first3 = all(has(al, k) for k in ("wc-book:volatile-debt", "wc-book:big-position",
                                      "wc-book:liq-flow"))
    out2, al2, _ = run({**LOW, "WC_BOOK_DRIFT": "0.02"}, workdir=d)
    second = al2[len(al):]      # инбокс append-only: НОВЫЕ записи — это хвост, а не разность
                                # множеств (повтор того же ключа разностью не виден)
    fails += not ok("защёлка: то же состояние с дрейфом +2% молчит по всем трём",
                    first3 and not second, f"1={al} новые2={second}")
    fails += not ok("дрейф стенда действительно применён", "дрейф денежных величин" in out2)

    # 6б. ...и ПЕРЕВЗВОДИТСЯ, когда значение вернулось под порог
    out3, al3, _ = run({**B, "WC_BOOK_VOL_ALARM": "999000000"}, workdir=d)   # ниже порога
    out4, al4, _ = run({**B, "WC_BOOK_VOL_ALARM": "1000000"}, workdir=d)     # снова пробит
    new4 = al4[len(al3):]
    fails += not ok("защёлка перевзводится после возврата под порог",
                    has(new4, "wc-book:volatile-debt"), f"3={al3} 4={al4}")

    # 6в. СМЕНА СОСТАВА обязана звонить сквозь защёлку: дрейф +80% двигает и набор
    #     рынков ≥$1M, и шаг $5M по долгу — это уже ДРУГАЯ книга, а не тот же месяц.
    out5, al5, _ = run({**LOW, "WC_BOOK_DRIFT": "0.8"}, workdir=d)
    fails += not ok("смена состава звонит сквозь защёлку",
                    has(al5[len(al4):], "wc-book:volatile-debt"), f"новые={al5[len(al4):]}")

    # 7. МЁРТВЫЙ ПРИБОР = ТРЕВОГА, а не «книга пуста»
    out, al, _ = run({"WC_BOOK_API": "http://127.0.0.1:1/dead"})
    fails += not ok("мёртвый API -> instrument-dead", has(al, "wc-book:instrument-dead"), f"{al}")
    fails += not ok("при мёртвом приборе книга НЕ судится",
                    not any(has(al, k) for k in ("wc-book:volatile-debt",
                                                 "wc-book:big-position",
                                                 "wc-book:liq-flow")), f"{al}")

    # 8. КОНТРОЛЬ ЯКОРЕЙ ЖИВОЙ: несуществующий якорный рынок -> instrument-dead
    out, al, _ = run({**B, "WC_BOOK_ANCHOR_EXTRA": "NOSUCH/USDC"})
    fails += not ok("провал контроля якорей -> instrument-dead",
                    has(al, "wc-book:instrument-dead"), f"{al}")

    print("\nИТОГ:", "ВСЁ ЗЕЛЕНО" if not fails else f"ПРОВАЛОВ {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
