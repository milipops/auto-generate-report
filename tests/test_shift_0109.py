"""Сверка прогона 01.09.2026 с эталоном пользователя: пять дефектов.

1) ст.98 — сменная операция при РАБОТАЮЩЕЙ машине длится столько, сколько
   машина крутилась по счётчику (у него 11,43 = 10,6 + 0,83), а не ровно 12 ч
   с ложным флагом «+0,75 ч сверх нормы».
2) ст.151 — «машина в службе наладки, оператор навивает шпули»: простой станка
   подписывался работой оператора, наладчик исчезал.
3) ст.111 — «ориентировочное окончание работ 10-00»: наладчику начислили 3 ч,
   которых никто не отрабатывал. В эталоне он стоит с нулём.
4) ст.121 — «наладчики Пронкин и Шишкин» без времени: модель выдала каждому
   3,5 ч. Человек без времени обязан попасть в отчёт с нулём часов.
5) ст.321 — первое показание счётчика взято из СЕРЕДИНЫ смены (115423 вместо
   115329 по журналу): часы станка занижены, и об этом надо сказать словами.

Плюс правило пользователя от 02.09.2026: узлы, которые наладчик прогнал внутри
окна наладки, — работа станка, и это время вычитается ИЗ наладки (ст.121:
машина 0,76 ч по счётчику → наладка 11,25 → 10,49, смена сходится к 12 ч).
Обычный день и невозможный счётчик правилом не затрагиваются.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shift_report import build_shift_reports_from_structured


def _one(machine: dict, date: str = "01.09.2026"):
    return build_shift_reports_from_structured(
        {"date": date, "machines": [machine]})[0]


def test_98_shiftlong_parallel_lasts_as_long_as_machine_ran():
    """Эталон: оператор 11,43 = подмена 0,83 + шпулярник 10,6 (= часы машины)."""
    r = _one({
        "machine": "98", "operator": "Дроздова",
        "lines": [{"counter_start": 21254550, "counter_end": 21266100}],
        "events": [
            {"kind": "прочее", "start": "07:00", "end": "19:00",
             "reason": "замена шпулярника в течении смены, 78 маш. в подработке",
             "performer": "Дроздова"},
            {"kind": "подмена", "start": "09:00", "end": "09:50",
             "reason": "подмена (заправка полотна)", "performer": "Дроздова"},
        ]})
    rows = dict((d, h) for d, h, *_ in r.operator_work_by_reason())
    long_row = [h for d, h in rows.items() if "шпулярник" in d]
    assert long_row and abs(long_row[0] - r.work_hours) < 0.02, (rows,
                                                                 r.work_hours)
    assert abs(r.operator_hours - round(r.work_hours + 0.83, 2)) < 0.02, rows
    assert r.operator_excess == 0.0, (r.operator_hours, r.operator_norm)
    print(f"✅ 98: машина {r.work_hours} ч, оператор {r.operator_hours} ч "
          f"без ложного превышения, строки {rows}")


def test_151_service_holds_machine_operator_does_own_work():
    """Простой станка — «наладка», работа оператора — его навивка; часы
    наладчика идут ему, а не оператору."""
    r = _one({
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 733963, "counter_end": 736700}],
        "events": [
            {"kind": "наладка", "start": "08:00", "end": "14:00",
             "reason": "машина в службе наладки, обрыв, занимается Пронкин",
             "performer": "Пронкин"},
            {"kind": "навивка", "start": "08:00", "end": "14:00",
             "reason": "навивка шпулей", "performer": "Вашурина"},
        ]})
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    # Наладка 08:00–14:00 = 6 ч, но внутри окна обед 12:00–12:45, который стоит
    # отдельной строкой простоя. В строке наладки остаётся 5,25 ч, и
    # 5,25 + 0,75 = 6,0 — длина окна без задвоения (правило: станок 12 ч,
    # обед 45 мин отдельной строкой). Часы самого Пронкина ниже — полные 6 ч:
    # он человек, и его время считается по своему интервалу.
    assert abs(stops.get("наладка/переналадка", 0.0) - 5.25) < 0.02, stops
    assert abs(stops.get("обед (станок стоит)", 0.0) - 0.75) < 0.01, stops
    op_rows = dict((d, h) for d, h, *_ in r.operator_work_by_reason())
    assert not any("наладк" in d for d in op_rows), op_rows
    people = dict(r.attracted_by_person())
    pronkin = [h for who, h in people.items() if who.startswith("Пронкин")]
    assert pronkin and abs(pronkin[0] - 6.0) < 0.02, people
    print(f"✅ 151: простой {stops}, наладчик {pronkin[0]} ч, "
          f"оператор {op_rows}")


def test_111_estimated_end_gives_person_zero_hours():
    r = _one({
        "machine": "111", "operator": "Каленюк",
        "lines": [{"counter_start": 203805, "counter_end": 204261}],
        "events": [
            {"kind": "наладка", "start": "07:00", "end": "10:00",
             "reason": "информация от службы наладки (Каленюк Д.В.): "
                       "ориентировочное окончание работ 10-00",
             "performer": "Петровский"},
        ]})
    people = dict(r.attracted_by_person())
    row = [h for who, h in people.items() if who.startswith("Петровский")]
    assert row and row[0] == 0.0, people
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert abs(stops.get("наладка/переналадка", 0.0) - 3.0) < 0.02, stops
    print(f"✅ 111: {people} — часы не начислены, простой станка {stops}")


def test_named_person_without_time_is_zero_row_not_invented_hours():
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 887105, "counter_end": 887770}],
        "events": [
            {"kind": "наладка", "start": None, "end": None,
             "reason": "станок в наладке, наладчики Пронкин и Шишкин",
             "performer": "Пронкин"},
            {"kind": "наладка", "start": None, "end": None,
             "reason": "станок в наладке, наладчики Пронкин и Шишкин",
             "performer": "Шишкин"},
        ]})
    people = dict(r.attracted_by_person())
    assert len(people) == 2, people
    assert all(h == 0.0 for h in people.values()), people
    assert r.attracted_total == 0.0, r.attracted_total
    assert any("без времени" in w or "без времени" in w.lower()
               or "не указал" in w for w in r.warnings), r.warnings
    print(f"✅ 121: {people} — интервал не выдуман, предупреждение есть")


def test_zero_row_hidden_when_person_has_real_hours():
    """«Пронкин 6 ч» и «Пронкин 0 ч (время не уточнено)» рядом не стоят."""
    r = _one({
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 733963, "counter_end": 736700}],
        "events": [
            {"kind": "наладка", "start": "08:00", "end": "14:00",
             "reason": "обрыв, занимается Пронкин", "performer": "Пронкин"},
            {"kind": "наладка", "start": None, "end": None,
             "reason": "машина в наладке, Пронкин", "performer": "Пронкин"},
        ]})
    people = dict(r.attracted_by_person())
    assert len(people) == 1 and abs(list(people.values())[0] - 6.0) < 0.02, people
    print(f"✅ дубль с нулём скрыт: {people}")


def test_mid_shift_counter_start_is_reported():
    r = _one({
        "machine": "321", "operator": "Олег",
        "lines": [{"counter_start": 115423, "counter_end": 115963,
                   "counter_start_mid_shift": True}],
        "events": []})
    note = [w for w in r.warnings if "середины смены" in w.lower()]
    assert note, r.warnings
    assert "115423" in "".join(note[0].split()), note
    print(f"✅ 321: {note[0]}")


def test_naladka_test_run_counts_as_machine_work_and_leaves_naladka():
    """Правило пользователя от 02.09.2026: узлы, которые наладчик прогнал внутри
    окна наладки, — это работа станка, и её время вычитается ИЗ наладки.

    Ст.121 за 01.09: наладка на всю смену, но счётчик сдвинулся
    887105 → 887770 = 0,76 ч. Значит наладка не 11,25, а 12 − 0,75 обед
    − 0,76 работа = 10,49, и смена сходится в 12 ч без флагов.
    """
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 887105, "counter_end": 887770}],
        "events": [
            {"kind": "наладка", "start": "07:00", "end": "19:00",
             "reason": "станок в наладке", "performer": "Пронкин"},
        ]})
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert abs(r.work_hours - 0.76) < 0.02, r.work_hours
    assert abs(stops.get("наладка/переналадка", 0.0) - 10.49) < 0.02, stops
    assert abs(stops.get("обед (станок стоит)", 0.0) - 0.75) < 0.02, stops
    assert abs(r.machine_balance - 12.0) < 0.02, r.machine_balance
    assert r.machine_excess == 0.0 and r.machine_shortfall == 0.0
    assert r.naladka_test_run_cut > 0.01, r.naladka_test_run_cut
    assert any("снято" in w and "счётчику" in w for w in r.warnings), r.warnings
    print(f"✅ 121: машина {r.work_hours} ч, наладка {stops} — снято "
          f"{r.naladka_test_run_cut} ч, смена {r.machine_balance} ч")


def test_naladka_untouched_on_a_normal_day():
    """Обычный день: наладка 2 ч при работе 8 ч ничего не режет, и недостаток
    времени (1,25 ч) остаётся видимым — правило работает только там, где
    наладка объявлена на время, в котором станок реально крутился."""
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": int(8 * 60 * 14.5)}],
        "events": [
            {"kind": "наладка", "start": "07:00", "end": "09:00",
             "reason": "переналадка", "performer": "Пронкин"},
        ]})
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert abs(r.work_hours - 8.0) < 0.02, r.work_hours
    assert abs(stops.get("наладка/переналадка", 0.0) - 2.0) < 0.02, stops
    assert r.naladka_test_run_cut == 0.0, r.naladka_test_run_cut
    assert abs(r.machine_shortfall - 1.25) < 0.02, r.machine_shortfall
    print(f"✅ обычный день: наладка {stops}, недостаток "
          f"{r.machine_shortfall} ч — ничего не подогнано")


def test_impossible_counter_does_not_erase_naladka():
    """Счётчик даёт больше смены — это дыра в данных, а не пробные узлы:
    наладка остаётся как есть, а превышение видно флагом."""
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": 900000}],
        "events": [
            {"kind": "наладка", "start": "07:00", "end": "19:00",
             "reason": "станок в наладке", "performer": "Пронкин"},
        ]})
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert stops.get("наладка/переналадка", 0.0) > 11.0, stops
    assert r.naladka_test_run_cut == 0.0, r.naladka_test_run_cut
    assert r.machine_excess > 0.0, r.machine_excess
    print(f"✅ невозможный счётчик: наладка {stops}, превышение "
          f"{r.machine_excess} ч")


def test_absence_row_says_where_the_hours_went():
    """Строка отсутствия обещает «часы посчитаны там» только если их там ждут.

    Часы человека между станками не задваиваются, поэтому ссылка на чужой
    отчёт честна лишь тогда, когда человек — свой оператор того станка
    (эталон 26.08: Камбаров с 321 весь день оператор на 251). Если он
    приходил помогать, его минуты остаются в ЕГО блоке по его же станку
    (правило пользователя «помощь этого оператора на других станках тоже идёт
    во время работы оператора»), и обещание «посчитаны там» отправляло бы в
    отчёт, где этих часов нет (25.09.2026, ст.321 → 251).
    """
    def row(machines):
        r = build_shift_reports_from_structured(
            {"date": "01.09.2026", "machines": machines})[0]
        labels = [label for label, *_ in r.downtime_by_reason()]
        got = [l for l in labels if "работа на №111" in l]
        assert got, labels
        return got[0]

    away = {"machine": "121", "operator": "Вашурина",
            "lines": [{"counter_start": 0, "counter_end": int(6 * 60 * 14.5)}],
            "events": [
                {"kind": "прочее", "start": "13:00", "end": "19:00",
                 "reason": "ушла работать на 111 машину", "performer": "Вашурина",
                 "other_machine": "111"},
            ]}
    # 111 ведёт другой человек — часы ВашурИной остаются в её же блоке на 121.
    got = row([away, {"machine": "111", "operator": "Пронкин",
                      "lines": [{"counter_start": 0,
                                 "counter_end": int(6 * 60 * 14.5)}]}])
    assert "часы посчитаны там" not in got, got
    # Тот же уход на станок, где она и есть оператор, — часы действительно там.
    got = row([away, {"machine": "111", "operator": "Вашурина",
                      "lines": [{"counter_start": 0,
                                 "counter_end": int(6 * 60 * 14.5)}]}])
    assert "часы посчитаны там" in got, got
    print(f"✅ отсутствие: обещание только там, где часы считают: {got}")


def test_visited_machine_keeps_the_helper_named_in_its_reason():
    """Станок, куда приходили, обязан увидеть гостя: перенесённая строка — не
    зеркальный дубль, хотя её причина и содержит «ушел на <этот станок>».

    Без этого привлекаемый персонал зависел от глагола: «ушел на 251 смотреть
    ошибку» исчезало из отчёта 251, а «восстановил обрыв ... на 111» оставалось.
    """
    reports = build_shift_reports_from_structured(
        {"date": "25.09.2026", "machines": [
            {"machine": "321", "operator": "Петровский",
             "lines": [{"counter_start": 0, "counter_end": int(6 * 60 * 14.5)}],
             "events": [
                 {"kind": "прочее", "start": "15:00", "end": "15:15",
                  "reason": "ушел на 251 смотреть ошибку",
                  "performer": "Петровский", "other_machine": "251"},
             ]},
            {"machine": "251", "operator": "Гудкова",
             "lines": [{"counter_start": 0,
                        "counter_end": int(6 * 60 * 14.5)}]},
        ]})
    by = {str(r.machine): r for r in reports}
    names = [(s.performer, s.hours) for s in by["251"].attracted_all()]
    assert ("Петровский", 0.25) in names, names
    # Его часы при этом остались в его же блоке — один раз, не два: 0,25 ч на
    # чужом станке + 11,3 ч у своей машины (столько она реально проработала по
    # счётчику при присутствии 12 ч). В 11,25 итог не обрезается — переизбыток
    # оставляем как есть (приказ владельца от 28.09.2026).
    assert abs(by["321"].operator_hours - 11.55) <= 0.02, by["321"].operator_hours
    assert abs(by["321"].operator_explicit_hours - 0.25) <= 0.01, \
        by["321"].operator_explicit_hours
    # 0,30 ч сверх нормы — внутри допуска ±20 мин, поэтому флага переработки
    # здесь нет; обрезки до 11,25 тоже нет.
    assert by["321"].operator_excess == 0.0, by["321"].operator_excess
    assert by["321"].operator_span_clip == 0.0, by["321"].operator_span_clip
    assert not any(n.startswith("Петровский")
                   for n, _ in by["251"].operator_work_by_reason()), \
        by["251"].operator_work_by_reason()
    print(f"✅ 251 видит гостя: {names}")


if __name__ == "__main__":
    test_98_shiftlong_parallel_lasts_as_long_as_machine_ran()
    test_151_service_holds_machine_operator_does_own_work()
    test_111_estimated_end_gives_person_zero_hours()
    test_named_person_without_time_is_zero_row_not_invented_hours()
    test_zero_row_hidden_when_person_has_real_hours()
    test_mid_shift_counter_start_is_reported()
    test_naladka_test_run_counts_as_machine_work_and_leaves_naladka()
    test_naladka_untouched_on_a_normal_day()
    test_impossible_counter_does_not_erase_naladka()
    test_absence_row_says_where_the_hours_went()
    test_visited_machine_keeps_the_helper_named_in_its_reason()
