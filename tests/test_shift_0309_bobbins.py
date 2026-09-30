"""Правило пользователя от 03.09.2026 про бобины и шпулярник.

Е) «При подстановке или подмене бобин машина тоже в стопе, это время идёт в
   простой. При подмене шпулярника тоже машина в стопе и идёт простой».

До этого правила «шпулярник» ловился категорией «замена шпулярника / переход на
ассортимент», которая считалась работой ПАРАЛЛЕЛЬНО работающей машине, поэтому
подмена шпулярника из простоя станка выпадала. Бобины без слова «шпулярник»
уходили в «прочее» — простой был, но назывался никак.

Исключение (арифметика, а не подгонка): если мастер записал такую работу ОДНОЙ
строкой на всю смену («замена шпулярника на протяжении всей смены», реальные
ст.98 за 27.08 / 31.08 / 01.09), а счётчик показывает, что машина крутилась, то
12 ч простоя + 10 ч работы не влезают в 12-часовую смену. Такая запись остаётся
параллельной работой, и бот говорит об этом словами.

Второе исключение — прямое слово мастера (прогон 21.09.2026, ст.151): если в
записи нет ни одного слова про останов, но есть «машина в работе», станок в стоп
не ставится. См. test_bobbins_stop_yield_to_explicit_word_that_machine_runs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shift_report import (BOBBIN_LABEL, SPOOLER_STOP_LABEL,
                                   WINDING_LABEL,
                                   build_shift_reports_from_structured,
                                   classify_stop, format_shift_report,
                                   is_swap_stop, machine_stopped_for)

T_121 = 14.5          # отходов в минуту, станок 121
T_98 = 18.0           # отходов в минуту, станок 98
ASSORT_LABEL = "замена шпулярника / переход на ассортимент"


def _one(machine: dict, date: str = "03.09.2026"):
    return build_shift_reports_from_structured(
        {"date": date, "machines": [machine]})[0]


def _counter_for(hours: float, rate: float = T_121) -> int:
    return int(round(hours * rate * 60))


# ——— классификация ———

def test_bobbins_and_spooler_are_machine_stops():
    for text in ("Подстановка 13 бобин на шпулярник", "Подставил 17 бобин",
                 "подмена бобин", "замена бобин", "замена шпулярника, 58 бабин",
                 "подмена шпулярника", "Стоп. Подмена шпулярника"):
        label, active = classify_stop(text)
        assert label in (BOBBIN_LABEL, SPOOLER_STOP_LABEL), (text, label)
        assert active is True, text
        assert machine_stopped_for(text, label) is True, text
        assert is_swap_stop(text) is True, text
    print("✅ бобины и шпулярник → простой станка, оператор при этом работает")


def test_bobbins_stop_yield_to_explicit_word_that_machine_runs():
    """Правило E действует, пока мастер сам не сказал обратное.

    «Подстановка бобин, машина в работе» (прогон 21.09.2026, ст.151: «7.00 …
    машина в работе, продолжаю навивку шпулей и подстановку шпулярника»,
    07:00–11:33) — это запись о работе ВДОГОНКУ к идущей машине, а не про
    останов. Слово мастера о том, что станок шёл, приоритетнее общей формулировки
    правила E: правило описывает операцию, а запись — этот конкретный момент.

    Проверка монотонностью (почему иначе нельзя): ст.151 за 21.09 имеет счётчик
    787984 -> 790570 = 3,95 ч работы. Если бы 07:00–11:33 был простоем, простой
    по всем записям вышел 12,41 ч + 3,95 ч работы = 16,4 ч в 12-часовой смене.
    С этой записью как параллельной: 5,95 ч подмены шпулярника (11:33–17:30) +
    3,95 ч работы + мелкие простои ~1,9 ч = ~11,8 ч — сходится со сменой.

    Но прямое слово о стопе первее: «стоп машина, …, потом машина в работе»
    остаётся простоем (см. вторую проверку).
    """
    assert machine_stopped_for("подстановка бобин, машина в работе") is False
    assert machine_stopped_for("подмена шпулярника, машина в работе") is False
    assert machine_stopped_for("стоп машина, подмена бобин; 12:00 машина "
                               "в работе") is True
    print("✅ «машина в работе» без слова о стопе → параллельно; «стоп» первее")


def test_stuffing_remark_is_not_a_stop():
    """«Вес начинки со шпулярника большая» (смена 05.08) — не операция.

    Голое слово «шпулярник» не рождает ни «подмену шпулярника (станок стоит)»,
    ни параллельную «замену шпулярника / переход на ассортимент»: правило
    03.09.2026 требует названный глагол операции. Такая запись обязана остаться
    в параллельной категории (навивка), а не съехать в «прочее» — неизвестную
    причину код по умолчанию считает остановкой станка.
    """
    text = "Вес начинки со шпулярника большая"
    label, _a = classify_stop(text)
    assert label == WINDING_LABEL, label
    assert machine_stopped_for(text) is False
    assert is_swap_stop(text) is False
    print(f"✅ замечание о весе → «{label}», станок не останавливаем")


def test_repair_still_beats_spooler():
    """Правило А сильнее: «чинка шпулярника» — это чинка/ремонт."""
    assert classify_stop("чинка шпулярника")[0] == "чинка/ремонт"
    assert machine_stopped_for("чинка шпулярника") is True
    print("✅ чинка шпулярника → чинка/ремонт")


def test_assortment_change_unchanged():
    """«Переход на ассортимент» пользователь не менял — остаётся как было."""
    label, _a = classify_stop("переход на ассортимент")
    assert label == "замена шпулярника / переход на ассортимент", label
    assert machine_stopped_for("переход на ассортимент") is False
    print(f"✅ «{label}» без изменений")


def test_sorting_and_cleaning_are_not_an_assortment_change():
    """«Сортировка ченарышей ассортимента» — не перестройка на ассортимент.

    Слово «ассортимента» в названии остатков и загрязнений не переводит запись
    в параллельную «замену шпулярника / переход на ассортимент»: на смене
    22.09.2026 (ст.151) из-за этого 1,03 ч чистки машины вписались в
    «подмены, ч», хотя ни подмены, ни перестройки в них нет.
    """
    for text in ("сортировка ченарышей ассортимента ПА187×3 со шпулярника, "
                 "вес нити с катушкой 490–495 гр.",
                 "чистка машины от загрязнений полиэтиленового ассортимента"):
        label, _a = classify_stop(text)
        assert label != ASSORT_LABEL, (text, label)
    assert classify_stop("переход на др. ассортимент")[0] == ASSORT_LABEL
    print("✅ «ассортимента» в названии остатков — не перестройка")


# ——— отчёт ———

def test_bobbins_go_into_downtime_and_operator_hours():
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(9.0)}],
        "events": [
            {"kind": "остановка", "start": "09:00", "end": "09:30",
             "reason": "Подстановка 13 бобин на шпулярник",
             "performer": "Вашурина"},
            {"kind": "остановка", "start": "10:00", "end": "11:00",
             "reason": "Стоп. Подмена шпулярника", "performer": "Вашурина"},
        ]})
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert abs(stops.get(BOBBIN_LABEL, 0.0) - 0.5) < 0.02, stops
    assert abs(stops.get(SPOOLER_STOP_LABEL, 0.0) - 1.0) < 0.02, stops
    assert r.parallel_by_reason() == [], r.parallel_by_reason()
    rows = dict(r.operator_work_by_reason())
    assert any("бобин" in d for d in rows), rows
    assert any("шпулярник" in d for d in rows), rows
    assert abs(sum(rows.values()) - r.operator_hours) < 0.02, rows
    assert abs(r.machine_balance - 11.25) < 0.02, r.machine_balance
    print(f"✅ простой: {stops}, часы оператора {r.operator_hours} ч")


def test_shiftlong_spooler_record_stays_parallel():
    """Ст.98: «замена шпулярника всю смену» + работа по счётчику 10,05 ч."""
    r = _one({
        "machine": "98", "operator": "Белякова С.",
        "lines": [{"counter_start": 0,
                   "counter_end": _counter_for(10.05, T_98)}],
        "events": [
            {"kind": "остановка", "hours": 12,
             "reason": "замена шпулярника на протяжении всей смены",
             "performer": "Белякова С."},
            {"kind": "подмена", "start": "08:30", "end": "09:20",
             "reason": "подмена", "performer": "Белякова С."},
            {"kind": "подмена", "start": "16:15", "end": "17:05",
             "reason": "подмена", "performer": "Белякова С."},
        ]}, date="27.08.2026")
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert SPOOLER_STOP_LABEL not in stops, stops
    assert BOBBIN_LABEL not in stops, stops
    parallel = dict(r.parallel_by_reason())
    assert any("шпулярник" in d for d in parallel), parallel
    assert all("станок стоит" not in d for d in parallel), parallel
    assert r.machine_excess == 0.0, r.machine_excess
    assert abs(r.operator_hours - 11.71) < 0.05, r.operator_hours
    assert any("оставлена ПАРАЛЛЕЛЬНОЙ" in w for w in r.warnings), r.warnings
    print(f"✅ ст.98: простой {stops}, параллельно {parallel}, "
          f"оператор {r.operator_hours} ч (эталон 11,82)")


def test_short_bobbin_stop_is_not_parallelized():
    """Обычная подстановка бобин в 12 ч влезает — остаётся простоем."""
    r = _one({
        "machine": "98", "operator": "Белякова С.",
        "lines": [{"counter_start": 0,
                   "counter_end": _counter_for(10.05, T_98)}],
        "events": [
            {"kind": "остановка", "start": "08:30", "end": "09:20",
             "reason": "подстановка бобин", "performer": "Белякова С."},
        ]})
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert abs(stops.get(BOBBIN_LABEL, 0.0) - 0.83) < 0.02, stops
    assert not any("оставлена ПАРАЛЛЕЛЬНОЙ" in w for w in r.warnings), r.warnings
    print(f"✅ короткая подстановка бобин осталась простоем: {stops}")


def test_bobbins_on_another_machine_stay_absence():
    """«Ушёл на подмену шпулярника на 151» — на своём станке это отсутствие."""
    r = _one({
        "machine": "321", "operator": "Камбаров",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(8.0, 6.0)}],
        "events": [
            {"kind": "отсутствие", "start": "13:00", "end": "15:00",
             "reason": "ушёл на подмену шпулярника на 151 машину",
             "other_machine": "151", "performer": "Камбаров"},
        ]})
    labels = [label for label, _h, *_ in r.downtime_by_reason()]
    assert any("отсутствие оператора" in lab for lab in labels), labels
    assert BOBBIN_LABEL not in labels and SPOOLER_STOP_LABEL not in labels, labels
    print(f"✅ уход на 151 остался отсутствием: {labels}")


def test_text_report_names_the_operation():
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 0, "counter_end": _counter_for(9.0)}],
        "events": [
            {"kind": "остановка", "start": "09:00", "end": "09:30",
             "reason": "Подстановка 13 бобин на шпулярник",
             "performer": "Вашурина"},
        ]})
    text = format_shift_report(r)
    assert "бобин" in text, text
    print("✅ в тексте отчёта операция названа словами мастера")


def test_spooler_work_on_other_machine_not_counted_twice():
    """Ст.251 за 27.08: Камбаров менял шпулярник на 151 — 2,5 ч.

    Эти часы стоят в его блоке отдельной строкой «работа на машине №151»,
    значит в «контроль за работой своей машины» те же минуты второй раз идти не
    могут. До правки правило Е ломало это: строка стала простоем и выпала из
    вычета, у Камбарова выходило 11,49 ч вместо проверенных 11,25.
    """
    reports = build_shift_reports_from_structured({
        "date": "27.08.2026", "machines": [
            {"machine": "251", "operator": "Камбаров",
             "lines": [{"counter_start": 0,
                        "counter_end": _counter_for(6.32, 7.4)}],
             "events": [
                 {"kind": "отсутствие", "hours": 2.67, "other_machine": "111",
                  "reason": "оператор переведён на 111, "
                            "переоборудование шпулярника",
                  "performer": "Камбаров"},
             ]},
            {"machine": "151", "operator": "Вашурина",
             "lines": [{"counter_start": 0,
                        "counter_end": _counter_for(5.72, 10.4)}],
             "events": [
                 {"kind": "остановка", "hours": 2.5,
                  "reason": "замена шпулярника, 58 бабин",
                  "performer": "Камбаров"},
             ]},
        ]})
    r251 = next(x for x in reports if x.machine == "251")
    assert abs(r251.away_own_hours - 2.5) < 0.02, r251.away_own_hours
    # Добивки до нормы нет (приказ владельца от 28.09.2026): сколько сложилось —
    # столько и стоит. Прежние 11,25 закрывал филлер, а не строки мастера.
    assert abs(r251.operator_hours - 8.99) < 0.02, r251.operator_hours
    assert abs(r251.operator_hours - (r251.operator_explicit_hours
                                      + r251.operator_control_hours)) < 0.02
    rows = dict(r251.operator_work_by_reason())
    assert any("работа на машине №151" in d for d in rows), rows
    print(f"✅ часы на 151 не задвоены: Камбаров {r251.operator_hours} ч")


if __name__ == "__main__":
    test_bobbins_and_spooler_are_machine_stops()
    test_bobbins_stop_even_if_message_says_machine_runs()
    test_stuffing_remark_is_not_a_stop()
    test_repair_still_beats_spooler()
    test_assortment_change_unchanged()
    test_bobbins_go_into_downtime_and_operator_hours()
    test_shiftlong_spooler_record_stays_parallel()
    test_short_bobbin_stop_is_not_parallelized()
    test_bobbins_on_another_machine_stay_absence()
    test_text_report_names_the_operation()
    test_spooler_work_on_other_machine_not_counted_twice()
