"""Два правила пользователя от 03.09.2026.

А) «Если оператор пишет чинка, то в это время машина стоит и это идёт в
   простой». Чинка сильнее любого признака параллельности: даже фраза «машина
   в работе» в том же сообщении не отменяет остановку (реальная смена 02.09,
   ст.151: «10:36 Чинка , машина в работе 10:39 чинка завершена»).
   Заодно проверяем, что слово «начинка» больше не превращается в чинку, а
   «починка» — по-прежнему чинка.

Б) «Смена начинается с 7 утра у всех, и если сообщения по этому станку
   начинаются намного позже, например с 8 утра, то этот час идёт тоже в простой
   машины и пишется по нему отсутствие оператора».
   Считаем это ТОЛЬКО по отдельному полю first_info_time (время первой
   информации по станку) и только когда в 12 ч смены есть свободное место.
   Первая ОСТАНОВКА в 10:00 при данных с 07:00 не должна давать ни отсутствия,
   ни урезанной нормы оператора.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shift_report import (TechStop, build_shift_reports_from_structured,
                                   classify_stop, machine_stopped_for)

T_121 = 14.5          # отходов в минуту, станок 121


def _one(machine: dict, date: str = "03.09.2026"):
    return build_shift_reports_from_structured(
        {"date": date, "machines": [machine]})[0]


def _counter_for(hours: float, rate: float = T_121) -> int:
    """Показание счётчика, дающее ровно `hours` часов работы станка."""
    return int(round(hours * rate * 60))


# ——— Правило А: чинка = станок стоит ———

def test_repair_beats_parallel_flag():
    """«Чинка, машина в работе» — станок всё равно стоит."""
    s = TechStop(hours=0.05, reason="10:36 Чинка , машина в работе",
                 is_parallel=True)
    assert s.machine_stopped is True
    print(f"✅ чинка при is_parallel=True → простой: {s.label}")


def test_repair_beats_parallel_label():
    """Чинка рядом со словом «шпул» больше не уезжает в параллельную работу.

    Порядок категорий ставит «шпул» раньше «чинк», поэтому раньше «чинка
    шпуледержателя, машина в работе» получала ярлык навивки и выпадала из
    простоя станка целиком.
    """
    assert machine_stopped_for("чинка шпуледержателя, машина в работе") is True
    assert classify_stop("чинка шпуледержателя")[0] == "чинка/ремонт", \
        classify_stop("чинка шпуледержателя")
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(10.0)}],
        "events": [
            {"kind": "чинка", "start": "10:36", "end": "11:36",
             "reason": "чинка шпуледержателя, машина в работе",
             "performer": "Вашурина"},
        ]})
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    parallel = sum(h for _l, h in r.parallel_by_reason())
    assert abs(stops.get("чинка/ремонт", 0.0) - 1.0) < 0.02, stops
    assert parallel == 0.0, r.parallel_by_reason()
    print(f"✅ чинка в простое станка: {stops}, параллельно {parallel} ч")


def test_stuffing_is_not_a_repair():
    """«Вес начинки со шпулярника большая» (реальная смена 05.08) — не чинка."""
    label, _active = classify_stop("Вес начинки со шпулярника большая")
    assert label != "чинка/ремонт", label
    assert machine_stopped_for("Вес начинки со шпулярника большая") is False
    print(f"✅ «начинка» → {label}, станок не останавливаем")


def test_pochinka_is_still_a_repair():
    label, _active = classify_stop("починка полотна, 11:00-11:30")
    assert label == "чинка/ремонт", label
    assert machine_stopped_for("починка полотна") is True
    print(f"✅ «починка» → {label}")


# ——— Правило Б: данные начались позже 07:00 ———

def test_late_first_message_is_operator_absence():
    """Первое сообщение в 08:15 → 1,25 ч простоя «нет данных с 07:00 до 08:15».

    Норма оператора при этом честно уменьшается до 10 ч (он был у станка с
    08:15 до 19:00 минус обед), поэтому ложного недостатка не возникает.
    """
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "first_info_time": "08:15",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(10.0)}],
        "events": []})
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    row = [(l, h) for l, h in stops.items() if "данные по станку с" in l]
    assert row and abs(row[0][1] - 1.25) < 0.02, stops
    assert "08:15" in row[0][0], row
    assert abs(r.operator_norm - 10.0) < 0.02, r.operator_norm
    assert r.operator_shortfall == 0.0 and r.operator_excess == 0.0
    assert abs(r.machine_balance - 12.0) < 0.02, r.machine_balance
    assert any("Первая информация по станку" in w for w in r.warnings), r.warnings
    print(f"✅ поздние данные: {row[0]}, норма оператора {r.operator_norm} ч, "
          f"смена {r.machine_balance} ч")


def test_late_first_stop_does_not_invent_absence():
    """Данные с 07:00, первый СТОП в 10:00 — ни отсутствия, ни урезанной нормы.

    Это главный предохранитель правила Б: время первого простоя не является
    временем прихода оператора.
    """
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(10.25)}],
        "events": [
            {"kind": "чинка", "start": "10:00", "end": "11:00",
             "reason": "чинка полотна", "performer": "Вашурина"},
        ]})
    labels = [l for l, *_ in r.downtime_by_reason()]
    assert not any("отсутствие" in l for l in labels), labels
    assert abs(r.operator_norm - 11.25) < 0.02, r.operator_norm
    print(f"✅ первый стоп в 10:00: норма {r.operator_norm} ч, простой {labels}")


def test_full_shift_leaves_no_room_for_invented_absence():
    """Счётчик закрыл смену: часы не выдумываем, а предупреждаем словами."""
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "first_info_time": "08:15",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(11.25)}],
        "events": []})
    labels = [l for l, *_ in r.downtime_by_reason()]
    assert not any("данные по станку" in l for l in labels), labels
    assert r.data_gap_before_shift == 0.0, r.data_gap_before_shift
    assert abs(r.operator_norm - 11.25) < 0.02, r.operator_norm
    assert any("свободного времени нет" in w for w in r.warnings), r.warnings
    print(f"✅ смена закрыта работой: простой {labels}, "
          f"предупреждение есть")


def test_absence_stays_one_row_and_warning_names_the_empty_start():
    """Отсутствие оператора — одна строка-заглушка до 12 ч, как в эталоне.

    Пустое начало смены (08:36 вместо 07:00 = 1,6 ч) складывается в ту же
    строку с остальным необъяснённым временем, поэтому ярлык не обещает
    длительность («данные по станку с 08:36»), а точную величину пустого начала
    называет предупреждение. Реальный пример — ст.151 за 02.09, где первое
    сообщение пришло в 08:36.
    """
    r = _one({
        "machine": "151", "operator": "Вашурина",
        "first_info_time": "08:36",
        "lines": [{"counter_start": 733963, "counter_end": 736700}],
        "events": [
            {"kind": "чинка", "start": "10:36", "end": "10:39",
             "reason": "Чинка , машина в работе, чинка завершена",
             "performer": "Вашурина"},
        ]})
    rows = [(l, h) for l, h, *_ in r.downtime_by_reason()
            if "отсутствие" in l]
    assert len(rows) == 1, r.downtime_by_reason()
    assert "данные по станку с 08:36" in rows[0][0], rows
    # Заглушка добивает баланс до 12 ч, поэтому её размер зависит от времени
    # работы станка: 12 − работа − чинка 0,05 − обед 0,75. Работу берём из
    # расчёта, чтобы тест не ломался при уточнении нормы выработки (№151:
    # 10,4 → 10,9 в файле пользователя от 15.09.2026).
    want = round(12.0 - r.work_hours - 0.05 - 0.75, 2)
    assert abs(rows[0][1] - want) < 0.06, (rows, want, r.work_hours)
    assert abs(rows[0][1] - r.absence_hours) < 0.02, (rows, r.absence_hours)
    assert abs(r.data_gap_before_shift - 1.6) < 0.02, r.data_gap_before_shift
    gap_note = [w for w in r.warnings if "Первая информация" in w]
    assert gap_note and "1,6" in gap_note[0], r.warnings
    assert abs(r.machine_balance - 12.0) < 0.02, r.machine_balance
    print(f"✅ одна строка отсутствия {rows[0]}, пустое начало "
          f"{r.data_gap_before_shift} ч названо в предупреждении")


def test_operator_start_wins_over_first_info():
    """Прямые слова о приходе человека сильнее времени первого сообщения."""
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "operator_start": "09:00", "first_info_time": "08:15",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(9.0)}],
        "events": []})
    labels = [l for l, *_ in r.downtime_by_reason()]
    assert any("пришёл в 09:00" in l for l in labels), labels
    assert not any("данные по станку" in l for l in labels), labels
    assert r.data_gap_before_shift == 0.0, r.data_gap_before_shift
    print(f"✅ operator_start сильнее: {labels}")


def test_first_info_inside_tolerance_changes_nothing():
    """07:15 — это не «намного позже»: допуск 20 минут."""
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "first_info_time": "07:15",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(10.0)}],
        "events": []})
    labels = [l for l, *_ in r.downtime_by_reason()]
    assert not any("данные по станку" in l for l in labels), labels
    assert abs(r.operator_norm - 11.25) < 0.02, r.operator_norm
    assert not any("Первая информация" in w for w in r.warnings), r.warnings
    print(f"✅ 07:15 в допуске: норма {r.operator_norm} ч, простой {labels}")


if __name__ == "__main__":
    test_repair_beats_parallel_flag()
    test_repair_beats_parallel_label()
    test_stuffing_is_not_a_repair()
    test_pochinka_is_still_a_repair()
    test_late_first_message_is_operator_absence()
    test_late_first_stop_does_not_invent_absence()
    test_full_shift_leaves_no_room_for_invented_absence()
    test_absence_stays_one_row_and_warning_names_the_empty_start()
    test_operator_start_wins_over_first_info()
    test_first_info_inside_tolerance_changes_nothing()
