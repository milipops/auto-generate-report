# -*- coding: utf-8 -*-
"""Приказ владельца 30.09.2026: шпиндели и шпиндельные столы не относятся к машинам.

Дословно: «наладка шпинеделя это не причина простоя, шпиндели и шпиндельные столы
вообще не относятся к машинам. Наладка - это когда наладка машины».

Что бот делал не так (станок 111 за 29.09.2026): окно «Шпульный стол, шпиндель 2
недовивает заданные обороты 1457 вместо 1550, необходима наладка» (1,52 ч) стало
строкой «наладка/переналадка» и вместе с окном наладки 3,02 ч слилось в клетку
4,53. В его файле у 111-го «0,28+3,01+0,92+0,37 | наладка+чинка+навивка шпуль+
отсутв опер» — наладка там одна, и это 3,01 ч.

Границы правила меряли по всем 12 сохранённым разборкам. Снимается ровно два
окна: 111 за 29.09 (1,52 ч) и «шпульный стол на ассортимент НПА 187х3х2 настроен,
… настройки стола: 1390 оборотов до выключения, скорость мотора 40 HZ» 251 за
14.09 (0,18 ч). Шесть окон со словом про шпиндель остаются простоями машины:
у 14тр за 21/22/24.09 (по 12,0 ч) в причине написано про машину («машина в
стопе»), у 251 за 16.09 (0,42 ч) — «обрыв нити», у 111 за 27.09 (0,07 и 0,08 ч)
— «при схождении бобины».

Проверка переписки `_spindle_only_stop` (приказ 29.09 про «стоп шпиндель») здесь
не годится: она снимает и «стоп, навивка шпулей» (24.09), и остановы, где мастер
прямо написал про машину. Поэтому правило узкое: только шпиндель/шпульный стол,
без слова про машину и без машинной причины в другом предложении.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (                    # noqa: E402
    build_shift_reports_from_structured,
    is_spindle_part_work,
    machine_stopped_for,
)
from services.excel_export import _stops_columns       # noqa: E402

# Дословные строки мастеров из сохранённых разборок.
SPINDLE_111_2909 = ("Шпульный стол, шпиндель 2 недовивает заданные обороты 1457 "
                    "вместо 1550, необходима наладка")
CREEL_TABLE_251_1409 = ("шпульный стол на ассортимент НПА 187х3х2 настроен, "
                        "средний вес 950 гр, диапазон 935-960 гр, настройки "
                        "стола: 1390 оборотов до выключения, скорость мотора "
                        "40 HZ")
THREAD_BREAK_251_1609 = ("обрыв нити, требуется помощь наладчика, шпиндель №1 "
                         "в наладке")
SPINDLE_EXPAND_111_2709 = ("Стоп шпиндель №2. При схождении бобины шпули "
                           "начинает расширять")
WINDING_STOP_2409 = "стоп, навивка шпулей"
MACHINE_ON_SUBMEN_2809 = ("Машина на подмене, шпинделя √1,2 требуют наладки, "
                          "навиваю шпули")

FIXTURE = "ai_raw_29_09_2026.json"


def _r(num, fixture=FIXTURE):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fixture)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for r in build_shift_reports_from_structured(data):
        if str(r.machine) == num:
            return r
    raise AssertionError(f"нет станка {num} в {fixture}")


def _downtime(r):
    return {label: hours for label, hours, _active in r.downtime_by_reason()}


# ——— 1. что правило снимает ———

def test_spindle_naladka_is_not_machine_downtime():
    """Наладка шпинделя 2 у 111-й — не техостановка машины (приказ 30.09)."""
    assert is_spindle_part_work(SPINDLE_111_2909) is True
    assert machine_stopped_for(SPINDLE_111_2909) is False


def test_creel_table_setup_is_not_machine_downtime():
    """Строка про настройку шпульного стола у 251-й — тоже не останов машины."""
    assert is_spindle_part_work(CREEL_TABLE_251_1409) is True
    assert machine_stopped_for(CREEL_TABLE_251_1409) is False


def test_111_2909_spindle_window_leaves_the_tech_stops_column():
    """Ст.111 за 29.09: наладка машины осталась одна, 1,52 ч шпинделя — вне Q.

    До приказа клетка была 4,53 (наладка 3,02 слиплась со шпинделем 1,52); у
    владельца наладка — 3,01. Остальные слагаемые Q теперь видны отдельными
    строками, а не спрятаны под одной «наладкой».
    """
    r = _r("111")
    row = next(s for s in r.tech_stops if s.reason == SPINDLE_111_2909)
    assert row.machine_stopped is False, row
    assert row.label == "наладка/переналадка", row.label
    assert _downtime(r)["наладка/переналадка"] == 3.02, _downtime(r)
    q, _reason = _stops_columns(r)
    assert q == "3,02+0,98+0,2+0,15", q
    assert "1,52" not in q, q


# ——— 2. что правило НЕ снимает ———

def test_machine_word_keeps_the_stop():
    """Слово про машину перевешивает: «стоп машина, шпиндель 2» — станок стоит."""
    for text in ("16:07 стоп машина, шпиндель 2", MACHINE_ON_SUBMEN_2809):
        assert is_spindle_part_work(text) is False, text
        assert machine_stopped_for(text) is True, text


def test_other_machine_cause_in_another_clause_keeps_the_stop():
    """Машинная причина в другом предложении — останов, даже со шпинделем рядом."""
    for text in (THREAD_BREAK_251_1609, SPINDLE_EXPAND_111_2709):
        assert is_spindle_part_work(text) is False, text
        assert machine_stopped_for(text) is True, text


def test_winding_stop_without_spindle_is_untouched():
    """Приказ 29.09 про «стоп, навивка шпулей» правило про шпиндель не трогает."""
    assert is_spindle_part_work(WINDING_STOP_2409) is False
    assert machine_stopped_for(WINDING_STOP_2409) is True


def test_111_2709_spindle_stops_are_still_downtime():
    """27.09: «Стоп шпиндель» при схождении бобины — машинная причина, часы в Q.

    Правило про шпиндель эти окна не снимает: мастер назвал бобину, и 0,15 ч
    простоя остаются в отчёте. Это пограничный случай, он назван владельцу.
    """
    r = _r("111", "ai_raw_27_09_2026.json")
    spindle = [s for s in r.tech_stops
               if "шпиндел" in (s.reason or "").lower()]
    assert spindle, "нет ни одного окна со шпинделем"
    assert all(s.machine_stopped for s in spindle), spindle
