"""Разбор 31.08.2026: два задвоения часов оператора, найденные на прогоне.

1) Ст.98: «замена шпулярника на протяжении всей смены» (12 ч) плюс две подмены
   по 0,83 ч давали 13,66 ч при норме 11,25 — подмены шли ВНУТРИ смены.
2) Ст.121/151: Вашурина записана оператором обоих станков, её работа на №111
   попадала в блок работы оператора ОБОИХ (ст.121 показал превышение +7,59 ч).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shift_report import build_shift_reports_from_structured


def _by_machine(reports):
    return {r.machine: r for r in reports}


def test_shiftlong_op_absorbs_short_ones():
    day = {"date": "31.08.2026", "machines": [{
        "machine": "98", "operator": "Дроздова",
        "lines": [{"counter_start": 100000, "counter_end": 110000}],
        "events": [
            {"kind": "операция", "hours": 12,
             "reason": "замена шпулярника на протяжении всей смены, "
                       "78 машина в подработке"},
            {"kind": "подмена", "start": "08:30", "end": "09:20",
             "reason": "подмена (заправка полотна)"},
            {"kind": "подмена", "start": "16:15", "end": "17:05",
             "reason": "подмена (заправка полотна)"},
        ]}]}
    r = build_shift_reports_from_structured(day)[0]
    rows = r.operator_work_by_reason()
    total = round(sum(h for _, h in rows), 2)
    assert total == r.operator_hours, (rows, r.operator_hours)
    # Одновременное не складывается: 12 ч, а не 12 + 0,83 + 0,83.
    assert r.operator_hours <= 12.01, (r.operator_hours, rows)
    print(f"✅ ст.98 оператор {r.operator_hours} ч, строки: {rows}")


def test_work_elsewhere_counted_on_one_machine_only():
    day = {"date": "31.08.2026", "machines": [
        {"machine": "151", "operator": "Вашурина",
         "lines": [{"counter_start": 726653, "counter_end": 730222}],
         "events": [{"kind": "работа", "start": "08:15", "end": "16:25",
                     "reason": "работала на 111м станке"}]},
        {"machine": "121", "operator": "Вашурина", "operator_start": "16:30",
         "lines": [{"counter_start": 200000, "counter_end": 201000}],
         "events": [{"kind": "работа", "start": "08:15", "end": "16:25",
                     "reason": "работала на 111м станке"}]},
    ]}
    reports = _by_machine(build_shift_reports_from_structured(day))
    r121 = reports["121"]
    # На станке, куда человек пришёл позже, работа на стороне — отсутствие,
    # а не его часы: превышения быть не должно.
    assert r121.operator_excess == 0.0, (r121.operator_hours,
                                         r121.operator_work_by_reason())
    assert r121.operator_hours <= r121.operator_norm + 0.34, r121.operator_hours
    print(f"✅ ст.121 оператор {r121.operator_hours} ч, превышение "
          f"{r121.operator_excess}; ст.151 — {reports['151'].operator_hours} ч")


def test_machine_number_with_letter_m_is_same_machine():
    """«111м» из ответа модели — тот же станок, а не «другой» (потеря ст.111)."""
    from services.ai_extract import _same_machine
    assert _same_machine("111м", "111")
    assert _same_machine("111 машина", "111")
    assert _same_machine("№98", "98")
    assert not _same_machine("14тр", "14")
    print("✅ «111м» = «111», «14тр» ≠ «14»")


def test_lost_machine_warning_knows_about_counters():
    """Если у потерянного станка в переписке были счётчики — сказать словами."""
    from services.ai_extract import _looks_like_has_numbers
    text = ("–111\n8:15 начало смены. общ.сч-201738\n"
            "–601\nстанок не работал, оператора нет\n")
    assert _looks_like_has_numbers(text, "111")
    assert not _looks_like_has_numbers(text, "601")
    print("✅ счётчики у потерянного станка распознаются")


def test_lunch_not_deducted_from_late_arrival_norm():
    """Обед за смену один: пришёл после 12:00 — обед вычтен на том станке."""
    day = {"date": "31.08.2026", "machines": [{
        "machine": "321", "operator": "Камбаров", "operator_start": "16:30",
        "lines": [{"counter_start": 1000, "counter_end": 2000}],
        "events": []}]}
    r = build_shift_reports_from_structured(day)[0]
    # 19:00 − 16:30 = 2,5 ч, обеда в этом окне нет.
    assert abs(r.operator_norm - 2.5) < 0.02, r.operator_norm
    # Станок по счётчику работал 2,16 ч из 2,5 ч присутствия. Разрыв 0,34 ч —
    # небольшой, и приказ владельца от 30.09.2026 (№140) велит добрать его
    # строкой «контроль за работой машины» (граница — 1,5 ч).
    assert abs(r.operator_hours - 2.5) < 0.02, r.operator_hours
    assert r.operator_shortfall == 0.0, r.operator_shortfall
    assert abs(r.operator_machine_led_hours - 2.16) < 0.02, (
        r.operator_machine_led_hours)
    assert r.operator_excess == 0.0, r.operator_excess
    print(f"✅ поздний приход: норма {r.operator_norm} ч без вычета обеда, "
          f"часы {r.operator_hours} ч, недостаток {r.operator_shortfall} ч")


def test_overlapping_operator_operations_are_unioned():
    """Две подмены 08:00–10:00 и 09:00–11:00 — это 3 ч работы, а не 4."""
    day = {"date": "31.08.2026", "machines": [{
        "machine": "98", "operator": "Дроздова",
        "lines": [{"counter_start": 100000, "counter_end": 110000}],
        "events": [
            {"kind": "подмена", "start": "08:00", "end": "10:00",
             "reason": "подмена (заправка полотна)"},
            {"kind": "подмена", "start": "09:00", "end": "11:00",
             "reason": "подмена (заправка полотна)"},
        ]}]}
    r = build_shift_reports_from_structured(day)[0]
    rows = dict((d, h) for d, h in r.operator_work_by_reason())
    subs = [h for d, h in rows.items() if "подмена" in d]
    assert subs and abs(subs[0] - 3.0) < 0.02, rows
    assert abs(round(sum(rows.values()), 2) - r.operator_hours) < 0.02
    print(f"✅ наложение подмен: {subs[0]} ч (не 4), оператор {r.operator_hours}")


if __name__ == "__main__":
    test_shiftlong_op_absorbs_short_ones()
    test_work_elsewhere_counted_on_one_machine_only()
    test_machine_number_with_letter_m_is_same_machine()
    test_lost_machine_warning_knows_about_counters()
    test_lunch_not_deducted_from_late_arrival_norm()
    test_overlapping_operator_operations_are_unioned()
