# -*- coding: utf-8 -*-
"""«Плановый останов, наладчик Ефремов» на весь день — это наладка станка.

Ответ владельца на вопрос №2 (документ «Отчет о работе 29.09.2026 (правила чтения
переписки).docx»): «считать наладкой».

Станок №91 за 28.09.2026: единственная запись смены — «плановый останов наладчик
Ефремов» без времени и без оператора, счётчик только начальный (1692500), выпуска
нет. Код и до приказа складывал такие минуты в «наладку/переналадку» и закрывал
ими всю смену (12,00 ч), но название держится на одном слове модели («kind:
наладка»), поэтому его нужно запереть: без этой строки остаток смены ушёл бы в
«причина не указана», а станок выглядел брошенным, а не отданным службе наладки.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (                     # noqa: E402
    build_shift_reports_from_structured,
)

NALADKA = "наладка/переналадка"

RAW_91 = {
    "date": "28.09.2026", "warnings": [],
    "machines": [{
        "machine": "91", "operator": None, "cuts": None,
        "lines": [{"assortment": None, "client": None, "order_no": None,
                   "counter_start": 1692500, "counter_end": None}],
        "events": [{"start": None, "end": None, "kind": "наладка",
                    "reason": "плановый останов наладчик Ефремов",
                    "performer": "Ефремов", "other_machine": None,
                    "machine_kept_running": None, "hours": None}]}]}


def _r():
    return build_shift_reports_from_structured(RAW_91)[0]


def test_planovyj_ostov_schitaetsya_naladkoj():
    r = _r()
    # Третья цифра строки — работал ли в эти минуты оператор; на 91-й его нет.
    rows = {lab: (h, opr) for lab, h, opr in r.downtime_by_reason()}
    assert rows == {NALADKA: (12.0, False)}, rows
    stop = r.tech_stops[0]
    assert stop.machine_stopped is True and stop.hours == 12.0, stop
    # Вся смена названа одним словом — остатка «причина не указана» не остаётся.
    assert r.tech_stops_total == 12.0
    assert r.unexplained_downtime == 0.0
    assert r.absence_hours == 0.0


def test_stanok_bez_operatora_ne_poluchasa_chasov():
    """У 91-й оператора нет: часы человека из простоя службы не выводятся."""
    r = _r()
    assert r.operator_hours == 0.0
    assert r.zero_output_all_shift is True
