"""Правило пользователя от 03.09.2026 про пересечение операций.

Ж) «если операции пересекаются между собой то просто брать максимум».

Повод — строка итога по ст.151 за 27.08: «работа при станке 5,72 ч + операции при
остановке 10,65 ч = 11,25 из 11,25 ч». Слагаемые не складывались в свой же итог по
двум причинам: (1) «операции при остановке» считались сырой суммой записей, а
шпулярник в тот день меняли ПЯТЬ человек одновременно (16:15–18:50), и одни и те же
минуты попали в число пять раз, да ещё с чужими фамилиями; (2) сама строка была
собрана не по той формуле, по которой считается итог: время работы станка — это
время МАШИНЫ, а часы человека складываются из его операций и контроля за работой
машины (правило Р22).

Теперь и число, и строка идут от одного источника — own_operator_stops(), где
наложение по времени уже снято: пересечённые минуты считаются один раз, а
вложенные окна дают максимум.

Запуск: python3 tests/test_shift_0309_overlap.py
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (BOBBIN_LABEL,
                                   build_shift_reports_from_structured,
                                   format_shift_report)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
T_151 = 10.4          # отходов в минуту, станок 151


def _one(machine: dict, date: str = "03.09.2026"):
    return build_shift_reports_from_structured(
        {"date": date, "machines": [machine]})[0]


def _counter_for(hours: float, rate: float = T_151) -> int:
    return int(round(hours * rate * 60))


def _crew_day():
    """Ст.151 за 27.08 в чистом виде: пятеро меняют шпулярник одновременно."""
    return _one({
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(5.72)}],
        "events": [
            {"kind": "подмена", "start": "16:15", "end": "17:45",
             "reason": "замена шпулярника, 40 бабин", "performer": "Вашурина"},
            {"kind": "подмена", "start": "16:15", "end": "18:45",
             "reason": "замена шпулярника, 58 бабин", "performer": "Петровский"},
            {"kind": "подмена", "start": "16:15", "end": "18:45",
             "reason": "замена шпулярника, 50 бабин", "performer": "Бектошев"},
            {"kind": "подмена", "start": "16:15", "end": "18:45",
             "reason": "замена шпулярника, 58 бабин", "performer": "Камбаров"},
            {"kind": "подмена", "start": "17:40", "end": "18:50",
             "reason": "замена шпулярника, 40 бабин", "performer": "Абдулаев"},
        ]}, date="27.08.2026")


# ——— число ———

def test_nested_windows_give_maximum():
    """Вложенные окна: 16:15–18:45 и 16:15–17:45 — это 2,5 ч, а не 4."""
    r = _one({
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(5.0)}],
        "events": [
            {"kind": "остановка", "start": "16:15", "end": "18:45",
             "reason": "чинка кромки", "performer": "Вашурина"},
            {"kind": "остановка", "start": "16:15", "end": "17:45",
             "reason": "чинка пропусков", "performer": "Вашурина"},
        ]})
    assert abs(r.operator_stop_hours - 2.5) < 0.02, r.operator_stop_hours
    print(f"✅ вложенные окна → максимум {r.operator_stop_hours} ч")


def test_partial_overlap_counts_minutes_once():
    """Частичное пересечение: 08:00–10:00 и 09:00–11:00 = 3 ч (правило Р25).

    Здесь «максимум» = 2 ч стёр бы час, который человек реально отработал,
    поэтому пересечённые минуты считаются один раз, а не отбрасывается окно.
    """
    r = _one({
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(5.0)}],
        "events": [
            {"kind": "остановка", "start": "08:00", "end": "10:00",
             "reason": "чинка кромки", "performer": "Вашурина"},
            {"kind": "остановка", "start": "09:00", "end": "11:00",
             "reason": "чинка пропусков", "performer": "Вашурина"},
        ]})
    assert abs(r.operator_stop_hours - 3.0) < 0.02, r.operator_stop_hours
    print(f"✅ частичное пересечение → минуты один раз, {r.operator_stop_hours} ч")


def test_crew_hours_are_not_multiplied():
    """Станку — окно 2,58 ч, оператору — только его 1,5 ч, остальным — их часы."""
    r = _crew_day()
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert abs(stops.get(BOBBIN_LABEL, 0.0) - 2.58) < 0.02, stops
    assert abs(r.operator_stop_hours - 1.5) < 0.02, r.operator_stop_hours
    assert abs(r.attracted_total - 8.67) < 0.05, r.attracted_total
    print(f"✅ пятеро на шпулярнике: станок {stops.get(BOBBIN_LABEL)} ч, "
          f"оператор {r.operator_stop_hours} ч, привлечённые {r.attracted_total} ч")


# ——— строка итога ———

def test_parts_sum_to_operator_hours():
    r = _crew_day()
    parts = r.operator_hours_parts()
    total = round(sum(h for _n, h in parts), 2)
    assert abs(total - r.operator_hours) < 0.02, (parts, r.operator_hours)
    assert all(h > 0.01 for _n, h in parts), parts
    print(f"✅ слагаемые {parts} = {r.operator_hours} ч")


def test_parts_sum_on_real_days():
    """Инвариант на всех сохранённых днях пользователя."""
    checked = 0
    for path in sorted(glob.glob(os.path.join(TESTS_DIR, "day_*.json"))):
        import json
        data = json.load(open(path, encoding="utf-8"))
        for r in build_shift_reports_from_structured(data):
            parts = r.operator_hours_parts()
            total = round(sum(h for _n, h in parts), 2)
            assert abs(total - r.operator_hours) < 0.02, \
                (os.path.basename(path), r.machine, parts, r.operator_hours)
            checked += 1
    assert checked > 20, checked
    print(f"✅ слагаемые складываются в итог на {checked} блоках реальных дней")


def test_text_line_arithmetic_is_visible():
    r = _crew_day()
    line = next(x for x in format_shift_report(r).splitlines()
                if "👷 Сотрудник:" in x)
    left, right = line.split("=")
    nums = [float(w.replace(",", ".")) for w in left.split()
            if w.replace(",", ".").replace(".", "").isdigit()]
    total = float(right.split()[0].replace(",", "."))
    assert abs(sum(nums) - total) < 0.02, line
    assert "10,65" not in line, line
    print(f"✅ строка сходится: {line.strip()}")


if __name__ == "__main__":
    test_nested_windows_give_maximum()
    test_partial_overlap_counts_minutes_once()
    test_crew_hours_are_not_multiplied()
    test_parts_sum_to_operator_hours()
    test_parts_sum_on_real_days()
    test_text_line_arithmetic_is_visible()
