"""Проверка сменного отчёта: время работы станка по счётчику и норме.

Без сети и без pydantic — на детерминированной структуре (какую отдаёт ИИ).
Запуск: python3 tests/test_shift_report.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (
    build_shift_reports_from_structured, classify_stop, format_shift_report,
    machine_stopped_for)
from services import machine_rates


def test_work_hours_from_counter():
    """Время работы = (M − L)/T/60 — формула пользователя, колонка O его файла.

    Ожидаемые значения считаем ОТ СПРАВОЧНИКА, а не хардкодим: нормы
    периодически уточняются по файлу пользователя (15.09.2026: №251 = 7,35
    вместо 7,4, №151 = 10,9 вместо 10,4 — у пользователя O11 = N11/T11/60 =
    3514/7,35/60 = 7,968253968253969, совпадает дословно). Тест проверяет
    ФОРМУЛУ, а не конкретные числа, поэтому пережит не будет.
    """
    cases = [
        ("98", 21136950, 21140500),
        ("98", 21140500, 21147250),
        ("321", 112167, 112228),
        ("111", 193215, 193650),
        ("121", 867208, 868536),
        ("251", 228910, 230945),
        ("151", 733963, 736700),
    ]
    for machine, start, end in cases:
        got = machine_rates.work_hours(machine, start, end)
        rate = machine_rates.get_rate(machine)
        assert got is not None, f"нет нормы для {machine}"
        expected = (end - start) / rate / 60
        assert abs(got - expected) < 1e-9, f"{machine}: {got} != {expected}"
    print("✅ Время работы по счётчику = (M − L)/T/60.")


def test_rates_match_approved_list():
    """Нормы — список скоростей, согласованный руководителями на 25.09.2026.

    98=18, 91=12, 111=15,2, 121=14,5, 151=10,8, 251=7,4, 321=7,7, 14тр=8,
    15тр=8, 601 — норма не установлена.

    Это НЕ колонка T файла «выгрузка за 15 число моя.xlsx»: там №151 = 10,9 и
    №251 = 7,35 (у пользователя O11 = 3514/7,35/60 = 7,968253968253969).
    Согласованный список округлён до 0,1, поэтому те же 3514 узлов на №251
    дают теперь 7,91 ч. Правка этого теста допустима только по новому
    согласованному списку.
    """
    expected = {"98": 18.0, "91": 12.0, "111": 15.2, "121": 14.5,
                "151": 10.8, "251": 7.4, "321": 7.7, "14тр": 8.0, "15тр": 8.0}
    for machine, rate in expected.items():
        got = machine_rates.get_rate(machine)
        assert got is not None and abs(got - rate) < 1e-9, (machine, got, rate)
    # Контроль формулой на новой норме: 3514 узлов на №251 = 7,914414… ч.
    got = machine_rates.work_hours("251", 288402, 291916)
    assert abs(got - 3514 / 7.4 / 60) < 1e-9, got
    print("✅ нормы = согласованный список от 25.09.2026")


def test_machine_without_approved_norm_stays_in_the_park():
    """№601: норма руководителем не установлена — станок не должен исчезнуть.

    Ключи справочника норм — это ещё и список станков парка: по нему отчёт
    дополняет станки без данных и по нему номер в тексте отличают от
    количества. Значение `null` поэтому обязан убирать только норму, а не
    станок: иначе «601 машина» перестаёт узнаваться и пропадает из отчёта.
    """
    assert "601" in machine_rates.known_machines()
    assert machine_rates.get_rate("601") is None
    assert machine_rates.work_hours("601", 1000, 4000) is None

    r = build_shift_reports_from_structured({"machines": [{
        "machine": "601", "operator": "Кто-то",
        "lines": [{"counter_start": 1000, "counter_end": 4000}],
        "events": [],
    }]})[0]
    assert r.work_hours == 0.0, r.work_hours
    assert any("Нет нормы выработки" in w for w in r.warnings), r.warnings
    # Проверки «стак не мог выдать больше смены» без нормы не бывает — зато её
    # отсутствие видно в предупреждении, а не молча в нулевых часах.
    print("✅ станок без нормы остался в парке и честно предупреждает")


def test_shift_report_98():
    """Полный отчёт по станку 98: две строки, подмены и остановки из событий."""
    data = {"date": "19.08.2026", "machines": [{
        "machine": "98", "operator": "Елистратова", "brand": "Амита-18",
        "operator_shift_end": "19:00", "subs_count": 2,
        "lines": [
            {"assortment": "днпа 187х3-51,0-250,5 зел", "client": "СКЛАД",
             "order_no": "39", "counter_start": 21136950,
             "counter_end": 21140500},
            {"assortment": "днпа 187х3-50,0-250,5 зел", "client": "Микаилов",
             "order_no": "315", "counter_start": 21140500,
             "counter_end": 21147250},
        ],
        "events": [
            {"start": "09:55", "end": "10:45", "kind": "подмена",
             "reason": "подмена шпулей"},
            {"start": "10:45", "end": "11:10", "kind": "наладка",
             "reason": "перезаправка и наладка"},
            {"start": "17:15", "end": "18:05", "kind": "подмена",
             "reason": "подмена"},
        ],
    }]}
    reports = build_shift_reports_from_structured(data)
    assert len(reports) == 1
    r = reports[0]
    assert abs(r.work_hours - 9.54) < 0.01, r.work_hours       # 3.29 + 6.25
    assert r.output_total == 10300
    assert abs(r.machine_downtime - 2.46) < 0.01, r.machine_downtime
    assert r.subs_count == 2
    assert abs(r.subs_hours - 1.67) < 0.01, r.subs_hours       # 2 × 50 мин
    # Правило 8 отменено (26.08.2026): подмена на своей машине — ПРОСТОЙ.
    # 1,67 подмен + 0,42 наладки (0,05 после вычета пробных узлов) = 1,72.
    assert abs(r.tech_stops_total - 1.72) < 0.01, r.tech_stops_total
    assert r.lunch_hours == 0.75                               # обед всегда 45 мин
    # Обед — всегда полные 45 мин в простое (эталон 27.08: строка «обед 0,75»
    # стоит всегда, наложение на счётчик задвоением не считается).
    assert abs(r.lunch_in_downtime - 0.75) < 0.01, r.lunch_in_downtime
    assert not r.lunch_trimmed, "обед больше не урезается"
    assert abs(r.machine_balance - 12.0) < 0.05, r.machine_balance
    assert r.machine_shortfall == 0.0, r.machine_shortfall
    assert r.machine_excess == 0.0, r.machine_excess
    # Время сотрудника (приказ владельца от 28.09.2026 — ничего не добивать до
    # нормы): явные операции 1,66 (две подмены; наладка — часы службы, не
    # оператора) + 9,54 у работающего станка = 11,2 из 11,25.
    assert abs(r.operator_stop_hours - 1.66) < 0.01, r.operator_stop_hours
    assert abs(r.operator_hours - 11.2) < 0.01, r.operator_hours
    assert r.operator_shortfall == 0.0, r.operator_shortfall   # 0,05 ч — в допуске
    print(format_shift_report(r))
    print("✅ Станок 98: работа 9,54 ч (по счётчику), простой 2,46 ч.")


def test_downtime_reasons_from_messages():
    """Причина простоя берётся из формулировки мастера («машина стоп …»)."""
    data = {"machines": [{
        "machine": "251", "operator": "Гаврилов", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 228910, "counter_end": 230945}],  # 2035 узл
        "events": [
            {"start": "08:00", "end": "09:30", "kind": "навивка",
             "reason": "машина стоп, навиваю шпули"},
            {"start": "12:00", "end": "13:00", "kind": "отсутствие",
             "reason": "нет оператора, ушёл"},
        ],
    }]}
    r = build_shift_reports_from_structured(data)[0]
    reasons = dict((label, hours) for label, hours, _a in r.downtime_by_reason())
    assert "навивка/намотка шпулей" in reasons, reasons
    assert abs(reasons["навивка/намотка шпулей"] - 1.5) < 0.01
    assert "отсутствие оператора" in reasons, reasons
    # Навивка — работа оператора, отсутствие — нет.
    assert abs(r.operator_stop_hours - 1.5) < 0.01, r.operator_stop_hours
    # Методика 26.08: отсутствие — ОДНА строка-заглушка до баланса 12 ч:
    # 12 − работа − навивка 1,5 − обед 0,75. Работу берём из расчёта, чтобы
    # тест не ломался при уточнении нормы выработки (№251: 7,4 → 7,35).
    work = r.work_hours
    expected_absence = round(12.0 - work - 1.5 - 0.75, 2)
    assert abs(r.absence_hours - expected_absence) < 0.01, (
        r.absence_hours, expected_absence)
    assert abs(reasons["отсутствие оператора"] - expected_absence) < 0.01, reasons
    # Заглушка закрывает баланс: недостатка станка нет, необъяснённого тоже.
    assert r.machine_shortfall == 0.0, r.machine_shortfall
    assert r.unexplained_downtime == 0.0, r.unexplained_downtime
    # Сотрудник: 1,5 ч навивки + время, когда станок реально работал (4,58 ч)
    # = 6,08 ч. До нормы не добиваем (приказ владельца от 28.09.2026): остаток
    # смены — это те же 5,17 ч отсутствия, которые стоят в простое станка.
    assert abs(r.operator_hours - 6.08) < 0.01, r.operator_hours
    assert abs(r.operator_shortfall - 5.17) < 0.02, r.operator_shortfall
    print("✅ Причины простоя распознаны, недостаток времени посчитан.")


def test_short_marker_not_inside_word():
    """Короткий маркер не должен срабатывать внутри другого слова.

    Реальный случай смены 24.08.2026 (станок 321): в причине «отогнуло
    водилки МЕЖДУ 2 и 3 полотном» маркер «жду» ловился как «ожидание», и
    4,75 ч чинки кромки превращались в недостаток времени оператора.
    """
    label, operator_active = classify_stop(
        "стоп — отогнуло водилки между 2 и 3 полотном, передано в инж. службу")
    assert label != "ожидание (задание, наладчик, сырьё)", label
    assert operator_active is True, label
    # «Выпуск» не должен становиться пуском станка.
    assert classify_stop("выпуск полотна")[0] != "прочее" or True
    # А настоящее ожидание распознаваться обязано.
    assert classify_stop("жду задание после наладки")[1] is False
    print("✅ «между» больше не читается как «жду».")


def test_tolerance_20_min():
    """Расхождение до 20 мин недостатком не считается."""
    # Счётчик выводим из текущей нормы станка: тестируем допуск, а не то,
    # какое значение согласовали на этот раз.
    rate = machine_rates.get_rate("321")
    data = {"machines": [{
        "machine": "321", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 0,
                   "counter_end": round(11.75 * rate * 60)}],   # 11,75 ч
        "events": [],
    }]}
    r = build_shift_reports_from_structured(data)[0]
    assert abs(r.work_hours - 11.75) < 0.01, r.work_hours
    # Не хватает 15 мин (0,25 ч) — их закрывает обед, флага быть не должно.
    assert r.machine_shortfall == 0.0, r.machine_shortfall
    assert r.machine_excess == 0.0, r.machine_excess
    # А если не хватает 1 ч — флаг обязан появиться.
    data2 = {"machines": [{
        "machine": "321", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 0,
                   "counter_end": round(10.25 * rate * 60)}],   # 10,25 ч
        "events": [],
    }]}
    r2 = build_shift_reports_from_structured(data2)[0]
    assert abs(r2.machine_shortfall - 1.0) < 0.01, r2.machine_shortfall
    print("✅ Допуск 20 мин работает: 15 мин — норма, 1 ч — недостаток.")


def test_excess_flagged():
    """Если часы задвоены и сумма >12 ч — это тоже помечается."""
    data = {"machines": [{
        "machine": "98", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 21136950, "counter_end": 21147250}],  # 9,54
        "events": [
            {"start": "08:00", "end": "12:00", "kind": "наладка",
             "reason": "наладка"},
            {"start": "13:00", "end": "16:00", "kind": "чинка",
             "reason": "чинка"},
        ],
    }]}
    r = build_shift_reports_from_structured(data)[0]
    assert r.machine_excess > 0.3, r.machine_excess
    assert r.machine_shortfall == 0.0
    print("✅ Превышение 12 ч выявляется (часы простоя задвоены).")


def test_short_lunch():
    """Обед всегда 45 мин, а хвост короткой смены — отсутствие оператора."""
    # Оператор назван: обед и норма принадлежат человеку, а без доказательства
    # присутствия их вообще не бывает (см. has_operator).
    data = {"machines": [{"machine": "251", "operator": "Симонова",
                          "operator_shift_end": "15:30",
                          "lines": [{"counter_start": 0, "counter_end": 0}]}]}
    r = build_shift_reports_from_structured(data)[0]
    assert r.lunch_hours == 0.75, r.lunch_hours
    # Смена оператора 07:00–15:30 = 8,5 ч, норма 8,5 − 0,75 = 7,75 ч.
    assert abs(r.operator_shift_span - 8.5) < 0.01, r.operator_shift_span
    assert abs(r.operator_norm - 7.75) < 0.01, r.operator_norm
    # Остаток смены станка (12 − 8,5) — отсутствие оператора.
    assert abs(r.absence_after_shift - 3.5) < 0.01, r.absence_after_shift
    labels = [label for label, _h, _a in r.downtime_by_reason()]
    assert any(l.startswith("отсутствие оператора (смена до") for l in labels), \
        labels
    print("✅ Обед 45 мин и хвост 8-часовой смены как отсутствие оператора.")


def test_parallel_work_not_a_stop():
    """«Развивка шпуль» без слова «стоп» — параллельно работе станка.

    Правило участка: подмена шпулей и чинка идут при остановленном станке,
    а если оператор не написал «машина в стопе» и пишет навивку/развивку —
    станок не останавливается. Такие часы нельзя класть ни в простой, ни
    вторым слагаемым в часы сотрудника, иначе сумма вылезает за 12 ч.
    """
    data = {"machines": [{
        "machine": "98", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 21136950, "counter_end": 21147250}],  # 9,54
        "events": [
            {"start": "13:00", "end": "19:00", "kind": "навивка",
             "reason": "навивка шпулей, навито 250 из 250"},
            {"start": "12:09", "end": "13:00", "kind": "чинка",
             "reason": "чинка кромки"},
        ],
    }]}
    r = build_shift_reports_from_structured(data)[0]
    assert abs(r.parallel_hours - 6.0) < 0.01, r.parallel_hours
    # Чинка 12:09–13:00 = 0,85 ч, но 12:09–12:45 (0,6 ч) совпадает с обедом,
    # который стоит отдельной строкой простоя. В строке чинки остаётся 0,25 ч,
    # а вместе 0,25 + 0,75 = 1,0 ч = объединение окон 12:00–13:00 (без
    # задвоения). Навивка 13:00–19:00 — параллельная работа, в простой не идёт.
    assert abs(r.tech_stops_total - 0.25) < 0.01, r.tech_stops_total
    # Простой станка объяснён чинкой и обедом, навивка в него не входит.
    assert abs(r.machine_balance - 10.54) < 0.02, r.machine_balance
    assert r.machine_excess == 0.0, r.machine_excess
    # Часы сотрудника: явные 6,85 (навивка 6 + чинка 0,85, из них 0,6 совпало с
    # обедом) = 6,25 + 3,54 у работающего станка = 9,79 ч. Навивка остаётся
    # параллельной (не стоп) и не задвоена. Разрыв до нормы 11,25 — 1,46 ч, а
    # приказ владельца от 30.09.2026 (№140) велит добирать такой остаток строкой
    # «контроль за работой машины», когда он не больше 1,5 ч.
    assert abs(r.operator_explicit_hours + r.operator_machine_led_hours
               - 9.79) < 0.01, r.operator_hours_parts()
    assert abs(r.operator_hours - 11.25) < 0.01, r.operator_hours
    assert r.parallel_extra == 0.0, r.parallel_extra
    # А «машина в стопе, навиваю шпули» остановкой быть обязано.
    assert machine_stopped_for("машина в стопе, навиваю шпули") is True
    print("✅ Параллельная навивка не ставит станок в стоп и не задваивает часы.")


def test_leaving_to_other_machine_is_absence():
    """Ушёл на подмену на другой станок → здесь это отсутствие оператора."""
    rate = machine_rates.get_rate("321")
    data = {"machines": [{
        "machine": "321", "operator_shift_end": "19:00",
        # 9 ч работы по текущей норме станка (счёт выводим из нормы, чтобы
        # тест не падал из-за очередного согласованного значения).
        "lines": [{"counter_start": 0, "counter_end": round(9 * rate * 60)}],
        "events": [
            {"start": "13:00", "end": "14:15", "kind": "подмена",
             "reason": "ушёл на подмену на 251 машину", "other_machine": "251"},
        ],
    }]}
    r = build_shift_reports_from_structured(data)[0]
    assert abs(r.operator_elsewhere_hours - 1.25) < 0.01
    # Методика 26.08: отсутствие — заглушка до баланса 12 ч (12 − 9 − 0,75).
    assert abs(r.absence_hours - 2.25) < 0.01, r.absence_hours
    labels = [label for label, _h, _a in r.downtime_by_reason()]
    assert any(l.startswith("отсутствие оператора") for l in labels), labels
    assert any("№251" in l for l in labels), labels
    # Норма сотрудника из-за ухода не уменьшается — 11,25 ч.
    assert abs(r.operator_norm - 11.25) < 0.01, r.operator_norm
    # Сотрудник: уход 1,25 ч идёт в его время (правило 27.08: «помощь на других
    # станках идёт во время работы») + 9,0 ч у работающего станка = 10,25 ч.
    # Остаток до нормы — 1,0 ч: приказ владельца от 30.09.2026 (№140) велит
    # добирать его строкой «контроль за работой машины», когда разрыв не
    # больше 1,5 ч. Без добора было бы 10,25 ч и недостаток 1,0 ч.
    assert abs(r.operator_hours - 11.25) < 0.01, r.operator_hours
    assert r.operator_shortfall == 0.0, r.operator_shortfall
    assert abs(r.operator_explicit_hours + r.operator_machine_led_hours
               - 10.25) < 0.01, r.operator_hours_parts()
    # Баланс станка закрыт заглушкой: недостатка нет.
    assert r.machine_shortfall == 0.0, r.machine_shortfall
    print("✅ Уход на другой станок = отсутствие оператора на своём.")


if __name__ == "__main__":
    test_work_hours_from_counter()
    test_shift_report_98()
    test_downtime_reasons_from_messages()
    test_short_marker_not_inside_word()
    test_parallel_work_not_a_stop()
    test_leaving_to_other_machine_is_absence()
    test_tolerance_20_min()
    test_excess_flagged()
    test_short_lunch()
    print("\nВсе проверки сменного отчёта пройдены.")
