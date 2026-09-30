# -*- coding: utf-8 -*-
"""«Машина на подмене» + навивка шпуль в те же минуты = станок стоит.

Приказ владельца от 29.09.2026 по станку №111 за 28.09.2026 (дословно):
«нет-это не отсутвие заказа. по 111 машине же написны останоки по оператору и
это типо было время навивки шпуль. Там тоже в начала смены писалось что машина
на подмене. Если машина на подмене и оператор пишет навивку шпуль в это же
время что и подмена то машина в стопе».

Что было не так. Правило 24.09.2026 («навивка без слова «стоп» — не простой
станка») ставит строкам навивки флаг параллельности, и 7,15 ч работы Арташиной
по шпульному столу не считались простоем машины вовсе. Часы станка при этом
сошлись на остаток: отчёт по 111 за 28.09 печатал «причина не указана 7,18 ч»,
хотя состояние машины мастер записала словами в той же теме.

Разница с правилом 24.09 — в самом слове мастера. Там навивка шла, пока машина
мотала полотно; здесь строка состояния говорит, что машина ЖДЁТ заправки
полотна («07:00 Машина на подмене Продолжаю навивать шпули…»), и в эти минуты
станок стоит. Поэтому правило читает только состояние, названное самой машиной,
только целые записи навивки, легшие в окно состояния, и только те минуты,
которых станку не хватает до 12 ч.

События — реальный оплаченный разбор 28.09.2026 по станку №111; строки
переписки приведены дословно, но сокращены до нужного правилам.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import shift_report as sr                          # noqa: E402
from services.shift_report import (                              # noqa: E402
    WINDING_LABEL,
    build_shift_reports_from_structured,
)

# Тема станка №111 за 28.09.2026 (Погодина, Арташина) — дословно, без длинных
# показаний счётчиков и перечней фамилий.
LOG_111 = """Погодина Л., 06:40: **Шпуля:** навито - _146 из 750 **Состояние машины и полотна:** Машина на подмене
Татьяна Арташина, 07:12: **07:00** Машина на подмене Продолжаю навивать шпули на рабочих шпинделях √5,4,3. Шпинделя √1,2 - требуют наладки, расширяют .
Татьяна Арташина, 11:33: **11:33** прием машины 251 в работу. Продолжаю навивать шпули на 111
Татьяна Арташина, 11:50: **11:45-12:30** обед. Передаю навивку шпулей оператору Вашуриной. Навито 495 из 750, навивка осуществляется на трёх шпинделях √5,4,3. Шпиндели √1,2 требуют наладки — расширяют Машина на подмене .
Никита Калинюк, 13:20: **13:20** Калинюк Д. В. выполнил наладку стола- шпинделей √1,2. Шпиндели №1 и №2 находятся в рабочем состоянии.
Татьяна Арташина, 14:50: **14:45** 753 шт из 753 навиты . Подмена начата .
Татьяна Арташина, 15:55: **15:55** подмена закончена . Арташина 126 Вашурина 126 Елистратова 125
Татьяна Арташина, 16:07: Протяжка наладчиком (21 узел) Об. Сч. 234563 Подмена 7 из 15 Машина остановлена для нумерации челноков, чтобы избежать нарушения нумерации шпуль при подмене
"""

EVENTS_111 = [
    {"start": "07:00", "end": "11:00", "kind": "навивка",
     "reason": "навивка шпулей на рабочих шпинделях √5,4,3"},
    {"start": "11:00", "end": "11:06", "kind": "работа",
     "reason": "приняла под контроль машину 251", "other_machine": "251"},
    {"start": "11:06", "end": "11:33", "kind": "остановка",
     "reason": "стоп навивка, т.к встала 251"},
    {"start": "11:33", "end": "11:45", "kind": "навивка",
     "reason": "продолжаю навивать шпули на 111"},
    {"start": "11:45", "end": "12:30", "kind": "обед", "reason": "обед",
     "machine_kept_running": True},
    {"start": "11:45", "end": "12:30", "kind": "навивка",
     "reason": "передаю навивку шпулей оператору Вашуриной. Навито 495 из 750",
     "performer": "Вашурина"},
    {"start": "12:30", "end": "13:14", "kind": "навивка",
     "reason": "продолжаю навивку шпулей в количестве 228 шт"},
    {"start": "13:14", "end": "13:17", "kind": "наладка",
     "reason": "Каленюк Д. В. приступил к налаживанию шпинделей √1,2",
     "performer": "Каленюк"},
    {"start": "13:17", "end": "14:45", "kind": "навивка",
     "reason": "навивка шпулей до 753 шт из 753"},
    {"start": "14:45", "end": "15:55", "kind": "подмена",
     "reason": "подмена. Арташина 126, Вашурина 126, Елистратова 125",
     "performer": "Арташина"},
    {"start": "15:55", "end": "16:02", "kind": "наладка",
     "reason": "Наладчик Пронкин А. протяжка. Узлы протянуты",
     "performer": "Пронкин"},
    {"start": "16:02", "end": "19:00", "kind": "остановка",
     "reason": "Машина остановлена для нумерации челноков"},
]


def _report(log=LOG_111, events=None):
    data = {"date": "28.09.2026", "warnings": [], "machines": [{
        "machine": "111", "operator": "Арташина", "cuts": 1,
        "operator_shift_end": "19:00",
        "lines": [{"assortment": "днпа 187х3", "counter_start": 234542,
                   "counter_end": 234563}],
        "section_text": log,
        "events": EVENTS_111 if events is None else events}]}
    return build_shift_reports_from_structured(data)[0]


def _with_rule_off(fn):
    """Один и тот же прогон: с новым правилом и без него."""
    real = sr._name_winding_on_submen_state
    try:
        sr._name_winding_on_submen_state = lambda *a, **k: None
        base = fn()
    finally:
        sr._name_winding_on_submen_state = real
    return base, fn()


def _rows(r):
    return {lab: h for lab, h, _stopped in r.downtime_by_reason()}


def _windings(r):
    return [s for s in r.tech_stops if s.stop_label == WINDING_LABEL]


# ——— 1. окно состояния строится по словам мастера ———

def test_okno_sostoyaniya_otkryvaetsya_slovom_i_zakryvaetsya_podmenoj():
    """07:00 «Машина на подмене» … 15:55 «подмена закончена» = 535 мин."""
    assert sr._submen_state_windows(LOG_111, "111") == [
        (7 * 60, 15 * 60 + 55)]
    # «прием машины 251 в работу» (11:33) и «выполнил наладку стола» (13:20)
    # окно не закрывают: первое — чужая машина, второе — наладка шпулярника.
    assert sr._log_strong_run("прием машины 251 в работу", "111") is False
    assert sr._log_strong_run(
        "Калинюк Д. В. выполнил наладку стола- шпинделей √1,2", "111") is False


def test_slovo_pro_podmenu_ne_otkryvaet_okno_stopa():
    """Отметка «submen» не участвует в парах «стоп … пуск».

    Иначе состояние машины добавило бы окно простоя там, где мастер слова
    «стоп» не писал, и часы ушли бы дважды.
    """
    kinds = {k for _t, k, _s in sr._log_state_marks(LOG_111, "111")}
    assert "submen" in kinds and "submen_end" in kinds
    for start, _end, text in sr._log_stop_windows(LOG_111, "111"):
        assert "на подмене" not in text.lower(), (sr._log_hm(start), text)


# ——— 2. навивка внутри окна становится простоем станка ———

def test_navivka_v_okne_sostoyaniya_et_prostojom_mashiny():
    base, got = _with_rule_off(lambda: _report())
    was = [s for s in _windings(base) if not s.machine_stopped]
    now = {s.reason: s.machine_stopped for s in _windings(got)}
    assert len(was) == 5, [(sr._log_hm(s.start_min), s.reason) for s in was]
    assert all(now.values()), now
    # «стоп навивка, т.к встала 251» (11:06–11:33) была простоем и до правила.
    assert len(_windings(got)) == 6
    # Запись остаётся РАБОТОЙ оператора: шпули в эти минуты навивались, и
    # правило переводит их в простой станка, не трогая часов человека.
    for s in _windings(got):
        if s.on_submen_state:
            assert s.is_parallel is True, s.reason


def test_prichina_ne_ukazana_ustupaet_minuty_navivke():
    base, got = _with_rule_off(lambda: _report())
    assert _rows(base)["причина не указана"] == 7.18, _rows(base)
    assert _rows(got)["навивка/намотка шпулей"] == 7.6, _rows(got)
    assert _rows(got)["причина не указана"] <= 0.05, _rows(got)
    assert got.tech_stops_total == 11.85, got.tech_stops_total
    # Баланс станка не поехал: 12 ч по-прежнему закрыты.
    assert round(got.work_hours + got.tech_stops_total
                 + got.lunch_in_downtime + got.absence_hours
                 + got.unexplained_downtime, 2) <= 12.0


def test_chasy_operatora_ne_obrezayutsya():
    """Шпули в эти минуты навивались, поэтому часы мастера остаются его (28.09)."""
    base, got = _with_rule_off(lambda: _report())
    assert got.operator_explicit_hours == base.operator_explicit_hours == 11.09
    assert got.operator_hours <= sr.MACHINE_SHIFT_HOURS


def test_otchet_nazyvaet_okno_sostoyaniya_slovami_mastera():
    got = _report()
    notes = " | ".join(got.log_timed_stops)
    assert "станок назван на подмене" in notes, got.log_timed_stops
    assert "Машина на подмене" in notes, got.log_timed_stops


# ——— 3. правило не трогает то, где состояния «на подмене» нет ———

def test_bez_slova_na_podmene_navivka_ostajetsya_parallelnoj():
    """Правило 24.09 в силе: мотает полотно — навивка не простой станка."""
    log = LOG_111.replace("Машина на подмене", "Шпульный стол")
    assert sr._submen_state_windows(log, "111") == []
    r = _report(log)
    assert _rows(r)["причина не указана"] == 7.18, _rows(r)
    assert all(not s.machine_stopped for s in _windings(r)
               if s.reason != "стоп навивка, т.к встала 251"), _windings(r)


def test_podmena_na_chuoj_mashine_nashe_okno_ne_otkryvaet():
    """«Машина 111 в работе. 251 на подмене» — стоит чужая, своя работает."""
    log = "Татьяна Арташина, 07:12: **07:00** Машина 111 в работе. 251 на подмене\n"
    assert sr._submen_state_windows(log, "111") == []


def test_navivka_vnye_okna_ne_trogaetsya():
    """Запись навивки, которая лежит и внутри окна, и снаружи, не переведена.

    Правило берёт целые записи: резать пополам — значит решать за мастера,
    сколько он стоял, а сколько мотал.
    """
    events = list(EVENTS_111)
    events[0] = {"start": "07:00", "end": "16:30", "kind": "навивка",
                 "reason": "навивка шпулей на рабочих шпинделях √5,4,3"}
    r = _report(events=events)
    row = [s for s in _windings(r) if s.end_min == 16 * 60 + 30][0]
    assert row.machine_stopped is False, row
    assert row.on_submen_state is False
