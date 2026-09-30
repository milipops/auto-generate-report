# -*- coding: utf-8 -*-
"""Разбор смены 21.09.2026: четыре правила, которые бот нарушал.

Пользователь прислал претензию по факту отчёта («брат опять не то что надо») и
назвал четыре причины. Все четыре воспроизводятся на настоящих текстах этого
дня, и каждая проверка взята из переписки, а не из выдуманных фраз:

1. СЧЁТЧИК. Бот объявил честное «Общ. Счётчик 21509000» опечаткой и подставил
   21501650: регулярка не видела слово «Счётчик» и считала запись 11:05
   последним показанием смены, а модальная длина числа поднимала тревогу из-за
   того, что начало смены брали из утренней сводки (21486750 — состояние ДО
   смены), а не из первого показания оператора (21497900 в 06:53).
2. ОБЕД. «Передаю машину во время обеда другому оператору» писали ОТДЕЛЬНЫМ
   сообщением, а не в причине события обеда, — и простой 0,75 оставался.
3. ПАРАЛЛЕЛЬНЫЕ ДЕЛА. «Машина в работе, продолжаю навивку шпулей и подстановку
   шпулярника» получало ярлык «подмена шпулярника (станок стоит)»: 4,55 ч
   записывались в простой ИДУЩЕЙ машины, а её же время по счётчику добавлялось
   оператору сверху — 14,0 ч при смене 12 ч.
4. ВРЕМЯ. «Связан 1 срез из 3, 1350 узл., общ.сч 297413» — отметка о готовом
   срезе. Конец дотягивался до следующего события, и 3,02 ч РАБОТЫ по
   счётчику превращались в простой: станок 13,81 ч из 12 ч. Плюс модель
   склеивала два дела в одну строку («...навивки шпулей на 251 по завершению
   протяжки узлов на 321»), и бот считал это уходом на 321.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import counter_check as cc                          # noqa: E402
from services import machine_rates                                # noqa: E402
from services.shift_report import (                               # noqa: E402
    LUNCH_LABEL, _chain_half_intervals, _detect_other_machine,
    build_shift_reports_from_structured, machine_stopped_for)

RATE_98 = 18.0          # узлов в минуту, станок 98


# ——— 1. счётчик: метка, хронология, отказ от подмены ———

# Настоящий раздел №98 из выгрузки 21,09,2026.csv. Утреннюю сводку (06:38)
# пишет старший смены Черныш, показания оператора — Белякова.
REAL_98 = """98 машина(Белякова) - информация за смену 21.09.2026г. 06:26
Юлия Черныш, 06:38: Общий сч.21486750 Со.сч.1000 из 1450 Срез 4 .из 4
Белякова С., 06:53: 7.00 машина в работе 187.3 51 250.5 Зел склад 39 22.07.26 Сч 21497900 Навито и развито 250 из 250 7.35-8.25 подмена Развито и навито 250шп
Белякова С., 11:05: Переход на Склад з. 49-24.08.26 187.3 51 250.5 зел Сч 21501650 (3 среза) Подмена 13.55-14.45
Белякова С., 19:06: Общ. Счётчик 21509000 Ср. Сч 21509600 3Ср из 4 200Уз из 1450 Всего 8 срезов Развито 160 шп Навито 160 шп Замена шпулярника в течении смены 19.00 конец смены
"""


def _fix_98(lines):
    notes = cc.fix_machine_counters("98", lines, text=REAL_98,
                                    prev_end=None, prev_is_adjacent=False)
    return lines, notes


def test_operator_reading_opens_the_shift_not_the_morning_summary():
    """Начало смены — первое показание ОПЕРАТОРА, а не сводка старшего смены."""
    lines, notes = _fix_98([{"counter_start": 21486750,
                             "counter_end": 21509000}])
    assert lines[0]["counter_start"] == 21497900, lines
    assert lines[0]["counter_end"] == 21509000, lines
    assert any("утренней сводки" in n for n in notes), notes
    assert not any("опечатк" in n.lower() for n in notes), notes
    print("✅ начало взято из записи оператора 06:53, «опечатки» нет")


def test_end_counter_of_the_same_length_is_never_a_typo():
    """21509000 и 21497900 — одной длины; модальная длина не «исправляет» их."""
    for n in _fix_98([{"counter_start": 21497900,
                       "counter_end": 21509000}])[1]:
        assert "не похоже на остальные" not in n, n
    print("✅ честные восьмизначные показания не тронуты")


def test_time_from_the_validated_counters():
    """Станок 98 за 21.09 = 11 100 узлов / 18 узл/мин = 10,28 ч."""
    r = build_shift_reports_from_structured(
        {"date": "21.09.2026", "machines": [
            {"machine": "98", "operator": "Белякова",
             "lines": [{"counter_start": 21497900,
                        "counter_end": 21509000}], "events": []}]})[0]
    assert abs(r.work_hours - 11100 / (RATE_98 * 60)) < 0.01, r.work_hours
    print(f"✅ работа станка {_check_h(r.work_hours)} ч по счётчику")


def _check_h(value):
    return f"{value:.2f}".replace(".", ",")


def test_without_proof_the_number_is_left_alone():
    """Доказательств не хватает — число остаётся как написано, и бот просит проверить.

    Раньше при молчаливой нехватке аргументов код подставлял «крайнее
    достоверное показание», и в отчёт и в counter_ledger уходила выдуманная
    цифра.
    """
    text = """98 машина(Белякова)
Белякова С., 06:53: Сч 21497900
Белякова С., 19:06: Общ. Счётчик 215090000
"""
    lines = [{"counter_start": 21497900, "counter_end": 215090000}]
    notes = cc.fix_machine_counters("98", lines, text=text)
    assert lines[0]["counter_end"] == 215090000, (lines, notes)
    assert any("пришлите" in n for n in notes), notes
    print("✅ без однозначного восстановления число не подменено")


# ——— 2. обед: передачу видно из всего раздела станка ———

def test_lunch_handover_written_as_a_separate_message():
    """«Передаю машину на обед» отдельным сообщением — станок не стоял."""
    r = build_shift_reports_from_structured(
        {"date": "20.09.2026", "machines": [{
            "machine": "151", "operator": "Вашурина",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 787984, "counter_end": 790570}],
            "section_text": "12:00 передаю машину во время обеда другому "
                            "оператору, машина не останавливалась\n",
            "events": [{"start": "12:00", "end": "12:45", "kind": "обед",
                        "reason": "обед"}]}]})[0]
    assert r.lunch_handed_over is True
    assert r.lunch_in_downtime == 0.0, r.lunch_in_downtime
    assert LUNCH_LABEL not in [lab for lab, _h, *_ in r.downtime_by_reason()]
    print("✅ передача машины на обед гасит простой 0,75")


def test_lunch_survives_when_nobody_took_the_machine():
    """«Наладчик занимался моей машиной, хотя машину остановил я» — обед на месте."""
    r = build_shift_reports_from_structured(
        {"date": "22.09.2026", "machines": [{
            "machine": "321", "operator": "Демидов",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 123000, "counter_end": 123938}],
            "section_text": "наладчик занимался моей машиной, хотя машину "
                            "остановил я\n",
            "events": [{"start": "11:00", "end": "11:45", "kind": "обед",
                        "reason": "обед"}]}]})[0]
    assert r.lunch_handed_over is False
    assert r.lunch_in_downtime == 0.75, r.lunch_in_downtime
    print("✅ обычный обед по-прежнему 0,75 ч простоя")


# ——— 3. параллельные дела при работающей машине ———

def test_explicit_word_that_machine_runs_beats_one_keyword_label():
    """«Машина в работе…» — станок не в простое, даже если ярлык стоп-_only."""
    assert machine_stopped_for("машина в работе, продолжаю навивку шпулей "
                               "и подстановку шпулярника") is False
    # Слово про останов первее: «стоп» в записи есть — это простой.
    assert machine_stopped_for("стоп машина, навивка шпулей, потом машина в "
                               "работе") is True
    # Чинка сильнее любого признака параллельности (правило 03.09).
    assert machine_stopped_for("чинка, пропуск, машина в работе") is True
    print("✅ «машина в работе» снимает простой, «стоп» и «чинка» — нет")


def test_parallel_work_does_not_double_count_machine_time():
    """Ст.151 за 21.09: 4,55 ч навивки на ходу + 3,95 ч работы по счётчику.

    До правки навивка становилась простоем идущей машины, и оператор получал
    14,0 ч при смене 12 ч.
    """
    r = build_shift_reports_from_structured(
        {"date": "21.09.2026", "machines": [{
            "machine": "151", "operator": "Вашурина",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 787984, "counter_end": 790570}],
            "events": [
                {"start": "07:00", "end": "11:33", "kind": "навивка",
                 "reason": "машина в работе, продолжаю навивку шпулей и "
                           "подстановку шпулярника"}]}]})[0]
    assert r.work_hours > 3.0, r.work_hours
    assert (r.work_hours + r.accounted_downtime) <= 12.0 + 0.34, \
        (r.work_hours, r.accounted_downtime)
    assert r.operator_hours <= r.operator_norm + 0.34, r.operator_hours
    print(f"✅ оператор {r.operator_hours} ч при работе станка {r.work_hours} ч")


# ——— 4. время: отметка о срезе — не останов, и чужой станок в строке — не уход ———

def test_completed_cut_is_not_stretched_into_downtime():
    """«Связан 1 срез из 3» — точка, а не окно до следующей записи."""
    events = [
        {"start": "12:16", "end": None, "kind": "срез",
         "reason": "связан 1 срез из 3, 1350 узл., общ.сч 297413"},
        {"start": "15:17", "end": None, "kind": "срез",
         "reason": "довязан 2 срез из 3, 1350 узл., общ.сч 298764"},
    ]
    fixed = _chain_half_intervals(events)
    assert all(ev.get("end") is None for ev in fixed), fixed
    print("✅ отметка о срезе не растягивается на часы работы")


def test_ordinary_open_record_is_still_stretched():
    """Обычную запись без конца дотягивать надо — иначе часы уходят в «не указано»."""
    fixed = _chain_half_intervals([
        {"start": "07:10", "end": None, "kind": "остановка",
         "reason": "стоп машина, обрыв нити"},
        {"start": "09:00", "end": None, "kind": "работа", "reason": "пуск"}])
    assert fixed[0]["end"] == "09:00", fixed
    print("✅ «стоп машина, обрыв нити» по-прежнему дотягивается")


def test_own_machine_in_the_line_cancels_the_supposed_walk_away():
    """Номер своего станка в записе — оператор работал здесь, а не ушёл."""
    reason = ("продолжение навивки шпулей на 251 по завершению протяжки узлов "
              "на 321; за смену навито 124 шпули из 303")
    assert _detect_other_machine(reason, "251") is None
    # Обычный уход: своего станка в формулировке нет.
    assert _detect_other_machine("ушёл на подмену на 251 машину", "98") == "251"
    print("✅ «навивка на 251 … на 321» — не уход с 251")


def test_composite_line_does_not_stop_our_machine():
    """Склейка двух дел не должна ставить станок в простой на 1,37 ч."""
    r = build_shift_reports_from_structured(
        {"date": "21.09.2026", "machines": [{
            "machine": "251", "operator": "Петровский",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 295880, "counter_end": 300125}],
            "events": [
                {"start": "17:37", "end": "18:59", "kind": "навивка",
                 "reason": "продолжение навивки шпулей на 251 по завершению "
                           "протяжки узлов на 321"}]}]})[0]
    labels = [lab for lab, _h, *_ in r.downtime_by_reason()]
    assert "протяжка узлов" not in labels, labels
    assert r.machine_excess == 0.0, r.machine_balance
    print("✅ чужая протяжка не легла простым нашего станка")


def test_cut_reports_of_251_keep_the_shift_balance_inside_12_hours():
    """Всё дерево 21.09, ст.251: 9,56 ч работы (норма 7,4) + простои = 12 ч."""
    r = build_shift_reports_from_structured(
        {"date": "21.09.2026", "machines": [{
            "machine": "251", "operator": "Петровский",
            "lines": [{"counter_start": 295880, "counter_end": 300125}],
            "events": [
                {"start": "12:16", "end": None, "kind": "срез",
                 "reason": "связан 1 срез из 3, 1350 узл., общ.сч 297413"},
                {"start": "15:17", "end": None, "kind": "срез",
                 "reason": "довязан 2 срез из 3, 1350 узл., общ.сч 298764"},
                {"start": "15:57", "end": "17:37", "kind": "отсутствие",
                 "reason": "при вывязывании 3 среза машина стоп; оператор "
                           "переведён на 321", "other_machine": "321"},
                {"start": "17:37", "end": "18:59", "kind": "навивка",
                 "reason": "продолжение навивки шпулей на 251 по завершению "
                           "протяжки узлов на 321"},
                {"start": "18:47", "end": None, "kind": "срез",
                 "reason": "довязан 3 срез из 3, 1360 узл., сч 300125"}]}]})[0]
    assert abs(r.work_hours - 4245 / (machine_rates.get_rate("251") * 60)) \
        < 0.01, r.work_hours
    assert r.machine_balance <= 12.0 + 0.34, r.machine_balance
    assert r.operator_hours <= r.operator_norm + 0.34, r.operator_hours
    assert len(r.untimed_stops) == 3, r.untimed_stops
    print(f"✅ ст.251: работа {r.work_hours} ч, баланс {r.machine_balance} ч, "
          f"оператор {r.operator_hours} ч")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
