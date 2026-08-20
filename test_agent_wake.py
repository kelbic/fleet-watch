#!/usr/bin/env python3
"""Тесты будильника разбора тревог.

Что здесь защищается (поломка 10-11.08): дефолтная модель упёрлась в квоту, `claude -p` падал
за 5 секунд одной строкой, и будильник ЗАСЧИТЫВАЛ это как попытку разбора. За три цикла живые
тревоги закрылись «escalated-to-human», не будучи прочитанными ни разу, а человеку ушло
«агент не поднимается» без причины — причина лежала в третьем файле.

Шов стоит на ГРАНИЦЕ ПРОЦЕССА (`_run_session`), не выше: классификатор `_start_failure` и всё
ветвление `main()` исполняются настоящие. Транспорт к человеку закрыт своим сторожем
(FLEET_ALERT_MUTE), а инбокс уведён в tmp ещё до импорта — тест не имеет доступа к боевой очереди.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="agent-wake-test-")
os.environ["FLEET_ALERT_MUTE"] = "1"                       # сторож на транспорте, не на дисциплине
os.environ["FLEET_INBOX"] = os.path.join(_TMP, "inbox.jsonl")
os.environ["FLEET_WAKE_GAP"] = "0"                          # частотный гард тут не предмет
os.environ.setdefault("FLEET_WAKE_MODELS", "opus,sonnet")

sys.path.insert(0, os.path.expanduser("~/.fleet-watch"))
_spec = importlib.util.spec_from_file_location(   # AW_PATH — для отрицательного контроля на копии
    "agent_wake", os.environ.get("AW_PATH", os.path.expanduser("~/.fleet-watch/agent-wake.py")))
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)

# ЭТАЛОН, а не сочинение: строки сняты дословно из agent-wake-sessions.log — все КОРОТКИЕ
# выводы несостоявшихся сессий за всю историю файла. 17-19.08 суита была зелёной, пока бой
# был слеп: фикстура QUOTA была списана с того же написания, что и список подстрок в продукте,
# — тест и код делили одно допущение. Поэтому основная фикстура теперь та, на которой продукт
# СЛОМАЛСЯ, а прежняя оставлена рядом как исторический вариант.
QUOTA = "You've hit your weekly limit · resets Aug 19, 5pm (UTC)"
QUOTA_LEGACY = "You've reached your Fable 5 limit. Run /usage-credits to continue or switch models with /model."
QUOTA_CORPUS = (
    QUOTA_LEGACY,
    "You've hit your weekly limit · resets Aug 19, 5pm (UTC)",
    "You've hit your weekly limit · resets Aug 19, 5pm (UTC)\nClient.listTools() called but"
    " server does not advertise tools capability - returning empty list",
    "You've hit your weekly limit · resets 5pm (UTC)",
    "You've hit your weekly limit · resets 5pm (UTC)\nClient.listTools() called but server"
    " does not advertise tools capability - returning empty list",
)


class WakeBase(unittest.TestCase):
    def setUp(self) -> None:
        self.inbox = os.path.join(_TMP, "inbox.jsonl")
        aw.INBOX = self.inbox
        aw.HANDLED = os.path.join(_TMP, "handled.jsonl")
        aw.STAMP = os.path.join(_TMP, "stamp")
        aw.SESSION_LOG = os.path.join(_TMP, "sessions.log")
        aw.WAKE_GAP_SEC = 0.0
        aw.WAKE_MODELS = ["opus", "sonnet"]
        for p in (self.inbox, aw.HANDLED, aw.STAMP, aw.SESSION_LOG):
            if os.path.exists(p):
                os.remove(p)
        aw._log = lambda *a, **k: None                       # не пачкать боевой notify.log
        self.alerts: list[dict] = []
        aw.notify = lambda text, **kw: (self.alerts.append({"text": text, **kw}), "inbox")[1]
        self.runs: list[str] = []

    def seed(self, attempts: int = 0, **extra) -> dict:
        rec = {"ts": 1786425423, "iso": "2026-08-11T05:17:03Z", "source": "hyperlend-depeg",
               "hil": False, "key": "hl-depeg", "text": "сжатие LST-ряда 0.96%",
               "attempts": attempts, "handled": False, **extra}
        with open(self.inbox, "w") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def inbox_recs(self) -> list[dict]:
        return aw._read(self.inbox)

    def handled_recs(self) -> list[dict]:
        return aw._read(aw.HANDLED)

    def stub(self, table: dict[str, tuple[int, str]]) -> None:
        """table: модель -> (rc, вывод). Записывает порядок обращений."""
        def fake(prompt: str, model: str):
            self.runs.append(model)
            return table[model]
        aw._run_session = fake


class TestStartFailureDoesNotBurnAttempts(WakeBase):
    def test_quota_leaves_alert_pending_and_attempts_untouched(self):
        self.seed(attempts=1)
        self.stub({"opus": (1, QUOTA), "sonnet": (1, QUOTA)})
        self.assertEqual(aw.main(), 0)

        pend = self.inbox_recs()
        self.assertEqual(len(pend), 1, "тревога обязана остаться в очереди — разбора не было")
        self.assertFalse(pend[0].get("handled"))
        self.assertEqual(pend[0]["attempts"], 1, "попытка не тратится: сессия не запускалась")
        self.assertEqual(self.runs, ["opus", "sonnet"], "обязан быть перебор всей цепочки")

        self.assertEqual(len(self.alerts), 1)
        a = self.alerts[0]
        self.assertTrue(a["hil"], "отказ самой автоматики — пункт 3 критерия HIL")
        self.assertEqual(a["key"], "wake-blocked:quota", "класс причины — в ключе дедупа")
        self.assertIn(QUOTA[:40], a["text"], "тревога обязана нести ПРИЧИНУ, а не отсылку")

    def test_every_refusal_observed_in_the_log_is_recognised(self):
        """Позитивный контроль по эталону: КАЖДОЕ написание, которое бой реально видел.

        Сюда дописывать новые строки из agent-wake-sessions.log, а не подгонять их под regexp.
        """
        for txt in QUOTA_CORPUS:
            self.assertEqual(aw._start_failure(1, txt), "quota", f"не опознано: {txt[:60]!r}")

    def test_session_report_mentioning_a_limit_is_not_a_quota(self):
        """Ложная «квота» опаснее промаха: она ВОЗВРАЩАЕТ попытку, и настоящий отказ
        крутился бы вечно, ни разу не дойдя до человека. Якорь you/your обязателен."""
        for txt in ("I checked the rate limit config and reset the counter",
                    "raised the limit, will reset tomorrow",
                    "boom"):
            self.assertIsNone(aw._start_failure(1, txt), f"ложное срабатывание на {txt!r}")
        self.assertIsNone(aw._start_failure(1, "таймаут 1800s"),
                          "таймаут — сессия РАБОТАЛА и была убита, попытка обязана сгореть")

    def test_auth_failure_is_a_separate_dedup_key(self):
        self.seed()
        self.stub({"opus": (1, "Invalid API key · Please run /login"),
                   "sonnet": (1, "Invalid API key · Please run /login")})
        aw.main()
        self.assertEqual(self.alerts[0]["key"], "wake-blocked:auth",
                         "смена причины обязана позвонить заново, а не схлопнуться дедупом")

    def test_repeated_blocks_never_drain_the_queue(self):
        """Главный регресс: 10 циклов подряд с выбранной квотой не должны закрыть тревогу."""
        self.seed()
        self.stub({"opus": (1, QUOTA), "sonnet": (1, QUOTA)})
        for _ in range(10):
            aw.main()
        pend = self.inbox_recs()
        self.assertEqual(len(pend), 1)
        self.assertLess(pend[0]["attempts"], aw.MAX_ATTEMPTS)
        self.assertEqual(self.handled_recs(), [], "ничего не закрыто: не было ни одного разбора")


class TestFallbackChain(WakeBase):
    def test_second_model_rises_when_first_is_out_of_quota(self):
        self.seed()
        self.stub({"opus": (1, QUOTA), "sonnet": (0, "разобрал, починил, записал")})
        self.assertEqual(aw.main(), 0)
        self.assertEqual(self.runs, ["opus", "sonnet"])
        self.assertEqual(self.inbox_recs(), [], "разобранная тревога уходит из очереди")
        done = self.handled_recs()
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["outcome"], "agent")
        self.assertEqual(self.alerts, [], "человека при успешном разборе не беспокоим")

    def test_first_model_wins_and_second_is_not_touched(self):
        self.seed()
        self.stub({"opus": (0, "ок"), "sonnet": (0, "не должно вызваться")})
        aw.main()
        self.assertEqual(self.runs, ["opus"], "запасная модель — только по отказу старта")


class TestRealFailuresStillBurnAttempts(WakeBase):
    def test_session_that_ran_and_died_burns_an_attempt(self):
        self.seed(attempts=0)
        long_death = "разбирал тревогу...\n" * 60 + "Traceback: упал на середине"
        self.stub({"opus": (1, long_death), "sonnet": (1, long_death)})
        aw.main()
        pend = self.inbox_recs()
        self.assertEqual(pend[0]["attempts"], 1, "сессия работала и упала — это честная попытка")
        self.assertEqual(self.runs, ["opus"], "не отказ старта ⇒ цепочку не перебираем")
        self.assertEqual(self.alerts, [])

    def test_timeout_burns_an_attempt(self):
        self.seed()
        self.stub({"opus": (124, "таймаут 1800s"), "sonnet": (124, "таймаут 1800s")})
        aw.main()
        self.assertEqual(self.inbox_recs()[0]["attempts"], 1)

    def test_long_output_mentioning_a_limit_is_not_a_start_failure(self):
        """Своё же слово «limit» внутри полноценного разбора не должно читаться как отказ."""
        self.assertIsNone(aw._start_failure(1, "x" * 600 + "reached your limit"))
        self.assertEqual(aw._start_failure(1, QUOTA), "quota")
        self.assertIsNone(aw._start_failure(0, QUOTA), "успешная сессия — никогда не отказ старта")


class TestEscalationCarriesTheCause(WakeBase):
    def test_wake_broken_names_why(self):
        """Тревога человеку обязана нести причину: 10-11.08 она дважды сказала «не поднимается»,
        а «You've reached your … limit» лежало в третьем файле — час археологии на ровном месте."""
        self.seed(attempts=aw.MAX_ATTEMPTS, last_error="Traceback: сессия упала на разборе")
        self.stub({"opus": (0, "ок"), "sonnet": (0, "ок")})
        aw.main()
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0]["key"], "wake-broken")
        self.assertIn("Последняя ошибка сессии", self.alerts[0]["text"])
        self.assertIn("Traceback", self.alerts[0]["text"])
        self.assertEqual(self.handled_recs()[0]["outcome"], "escalated-to-human")

    def test_failed_session_records_its_error_on_the_item(self):
        self.seed()
        long_death = "работал...\n" * 60 + "ПОСЛЕДНЯЯ СТРОКА ОШИБКИ"
        self.stub({"opus": (1, long_death), "sonnet": (1, long_death)})
        aw.main()
        self.assertIn("ПОСЛЕДНЯЯ СТРОКА ОШИБКИ", self.inbox_recs()[0]["last_error"])


class TestArchiveKeepsWhatTheSessionWrote(WakeBase):
    """Разбор пишет вывод В САМУ ЗАПИСЬ (поле resolution) — архив обязан его донести.

    Дефект 13.08: `_append(HANDLED, batch)` архивировал снапшот, снятый ДО подъёма сессии,
    и вывод разбора терялся; в архиве оставалось «outcome: agent» без единого слова о том,
    что выяснено. Симптома не было, потому что архив читают глазами, а не кодом.
    """

    def _session_that_writes_resolution(self, text: str):
        def fake(prompt: str, model: str):
            self.runs.append(model)
            recs = aw._read(self.inbox)          # сессия читает СВОЮ тревогу...
            for r in recs:
                r["resolution"] = text           # ...и дописывает в неё вывод
            aw._rewrite(self.inbox, recs)
            return 0, "разобрал"
        aw._run_session = fake

    def test_resolution_survives_into_the_archive(self):
        self.seed()
        self._session_that_writes_resolution("факт по цепи: долг $0.0238, действий не требуется")
        aw.main()
        done = self.handled_recs()
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["outcome"], "agent")
        self.assertIn("$0.0238", done[0].get("resolution", ""),
                      "вывод разбора обязан доехать до архива, а не остаться в стёртом инбоксе")
        self.assertEqual(self.inbox_recs(), [], "и запись всё равно уходит из очереди")

    def test_alert_arriving_mid_session_is_not_archived_by_mistake(self):
        """Свежее чтение не должно утащить в архив ЧУЖУЮ тревогу, легшую во время разбора."""
        self.seed()

        def fake(prompt: str, model: str):
            self.runs.append(model)
            recs = aw._read(self.inbox)
            recs.append({"ts": 1786602012, "source": "midnight-monitor", "key": "",
                         "text": "новая тревога посреди разбора", "attempts": 0,
                         "handled": False})
            aw._rewrite(self.inbox, recs)
            return 0, "ок"
        aw._run_session = fake
        aw.main()
        self.assertEqual(len(self.handled_recs()), 1, "в архив едет только разобранная")
        pend = self.inbox_recs()
        self.assertEqual(len(pend), 1, "новая тревога остаётся в очереди")
        self.assertEqual(pend[0]["source"], "midnight-monitor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
