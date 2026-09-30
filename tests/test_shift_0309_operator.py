"""Правила пользователя от 03.09.2026 про часы оператора (смена 02.09).

В) «Если оператор работал больше, то такое может быть — не нужно уменьшать ему
   время работы, как например с 98 машиной: у неё минимум работа по машине 10.2,
   а отчёт ставит 9.75, чтобы было 11.25 ровно, но так не надо делать. Если есть
   какие-то расхождения — и в большую, и в меньшую сторону — это надо оставлять
   как есть».
   Значит время работы станка по счётчику — НИЖНЯЯ граница часов оператора,
   а не то, что надо сжать до нормы 11,25.

Г) «Если машина весь день в наладке или инженерной службе, у неё не надо писать
   в простое обед оператора — там просто пишется 12 часов наладка».
   Обед — обед ОПЕРАТОРА, поэтому он есть только когда фамилия оператора
   названа: эталон 02.09 (ст.111/121, фамилии нет) — обеда нет; эталон 01.09
   (ст.121, Вашурина) — «11,25 наладка + 0,75 обед».

Д) «Работу оператора пишите точнее, а не просто работа за машиной — они для
   этого и пишут свои сообщения по машине и что делали в определённое время».
   Операции, названные БЕЗ времени (ст.321 за 02.09: подстановка бобин, чинка
   кромки, упаковка гильз), в часы поставить нельзя, но они обязаны быть
   названы: в расшифровке часов оператора и в подписи простоя станка.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shift_report import (CONTROL_LABEL, NALADKA_LABEL, TOLERANCE_H,
                                   UNEXPLAINED_LABEL,
                                   build_shift_reports_from_structured,
                                   format_shift_report)


def _one(machine: dict, date: str = "02.09.2026"):
    return build_shift_reports_from_structured(
        {"date": date, "machines": [machine]})[0]


# ——— Правило В: часы оператора не урезаем под 11,25 ———

def _report_98():
    """Реальная смена 02.09, ст.98 (Белякова): счётчик 21287900 → 21298900."""
    return _one({
        "machine": "98", "operator": "Белякова С.",
        "lines": [{"counter_start": 21287900, "counter_end": 21298900,
                   "assortment": "6 маш."}],
        "events": [
            {"kind": "подмена", "start": "08:10", "end": "08:55",
             "reason": "подмена", "performer": "Белякова С."},
            {"kind": "подмена", "start": "16:25", "end": "17:10",
             "reason": "подмена", "performer": "Белякова С."},
        ]})


def test_operator_hours_not_squeezed_to_norm():
    """1,5 ч подмен + 10,19 ч работы станка = 11,69 ч, а не ровно 11,25."""
    r = _report_98()
    assert abs(r.work_hours - 10.19) < 0.02, r.work_hours
    assert abs(r.operator_explicit_hours - 1.5) < 0.02, r.operator_explicit_hours
    assert abs(r.operator_control_hours - 10.19) < 0.02, r.operator_control_hours
    assert abs(r.operator_hours - 11.69) < 0.03, r.operator_hours
    assert r.operator_shortfall == 0.0, r.operator_shortfall
    print(f"✅ ст.98: {r.operator_explicit_hours} + {r.operator_control_hours} "
          f"= {r.operator_hours} ч (эталон 11,7 из 11,25)")


def test_excess_from_counter_is_explained_not_flagged():
    """Переработка из счётчика — пояснение, а не «проверьте данные»."""
    from services.excel_export import _discrepancy_text


    r = _report_98()
    assert r.operator_excess > 0.01, r.operator_excess
    assert r.excess_from_machine_time is True
    text = format_shift_report(r)
    assert "не урезаем" in text, text
    assert "проверьте данные" not in text.split("Сотрудник")[-1]
    assert "оператор +" not in _discrepancy_text(r), _discrepancy_text(r)
    print(f"✅ переработка {r.operator_excess} ч объяснена счётчиком, "
          f"в красную строку не попала")


def test_control_hours_never_exceed_operator_shift():
    """Смена короче — контроль ограничен присутствием, часы станка не дарим оператору.

    Мера — ПРИСУТСТВИЕ (operator_shift_span = 07:00→15:00 = 8 ч), а не норма
    (7,25 ч): норма вычитает обед второй раз, хотя обеда в работе машины и так
    нет (он в простое станка). Нормой эти часы не режутся и по приказу владельца
    от 28.09.2026.
    """
    r = _one({
        "machine": "98", "operator": "Белякова С.",
        "operator_shift_end": "15:00",
        "lines": [{"counter_start": 21287900, "counter_end": 21298900}],
        "events": []})
    assert r.operator_shift_span == 8.0, r.operator_shift_span
    assert abs(r.operator_control_hours - r.operator_shift_span) < 0.01, (
        r.operator_control_hours, r.operator_shift_span)
    assert r.operator_control_hours <= r.operator_shift_span + 0.01, (
        r.operator_control_hours, r.operator_shift_span)
    # Станок по счётчику работал дольше, чем человек у него был, — снятое
    # называется словами, а не теряется молча (и в чате, и в Excel).
    assert abs(r.operator_span_clip - (r.operator_led_raw_hours
                                       - r.operator_shift_span)) < 0.01, (
        r.operator_span_clip, r.operator_led_raw_hours)
    assert r.operator_led_raw_hours > r.operator_shift_span + 0.01, (
        r.operator_led_raw_hours, r.operator_shift_span)
    text = format_shift_report(r)
    assert "времени у станка сверх присутствия" in text, text[-800:]
    assert "Сверьте, кто вёл станок" in text, text[-800:]
    print(f"✅ смена до 15:00: станок {r.operator_led_raw_hours} ч, присутствие "
          f"{r.operator_shift_span} ч, контроль {r.operator_control_hours} ч, "
          f"снято {r.operator_span_clip} ч (норма {r.operator_norm} ч — не мера)")


def test_parallel_operations_not_counted_twice():
    """Навивка при работающем станке уже внутри работы машины — не задваиваем.

    Станок работал 10,19 ч, из них 3 ч оператор параллельно вил шпули. Часы
    оператора по слагаемым — 3 + 7,19 = 10,19 ч, а НЕ 3 + 10,19 = 13,19.
    Приказ владельца от 30.09.2026 (№140) добивает остаток до 11,25 строкой
    «контроль за работой машины», когда разрыв не больше 1,5 ч: здесь 1,06 ч,
    поэтому итог 11,25 — но задвоения по-прежнему нет, и больше 13,19 не стало.
    """
    r = _one({
        "machine": "98", "operator": "Белякова С.",
        "lines": [{"counter_start": 21287900, "counter_end": 21298900}],
        "events": [
            {"kind": "навивка", "start": "09:00", "end": "12:00",
             "reason": "навивка шпулей, машина в работе",
             "performer": "Белякова С."},
        ]})
    assert abs(r.parallel_own_hours - 3.0) < 0.02, r.parallel_own_hours
    assert abs(r.operator_machine_led_hours - 7.19) < 0.02, (
        r.operator_machine_led_hours)
    assert abs(r.operator_explicit_hours + r.operator_machine_led_hours
               - 10.19) < 0.02, r.operator_hours_parts()
    assert abs(r.operator_hours - 11.25) < 0.02, r.operator_hours
    assert r.operator_hours < 13.19 - TOLERANCE_H, r.operator_hours
    print(f"✅ навивка 3 ч внутри работы станка: слагаемые "
          f"{r.operator_explicit_hours + r.operator_machine_led_hours} ч "
          f"(не 13,19), итог {r.operator_hours} ч при работе станка "
          f"{r.work_hours} ч")


# ——— Правило Г: весь день наладка без оператора — 12 ч, без обеда ———

_NALADKA_TEXT = "весь рабочий день машина не работала, в очереди на наладку"


def _report_111(hours=12.0):
    ev = {"kind": "наладка", "reason": _NALADKA_TEXT}
    if hours:
        ev["hours"] = hours
    return _one({"machine": "111", "events": [ev]})


def test_naladka_all_shift_without_operator_has_no_lunch():
    r = _report_111()
    assert r.naladka_all_shift is True
    assert r.naladka_all_shift_no_operator is True
    assert r.has_operator is False
    assert r.lunch_in_downtime == 0.0, r.lunch_in_downtime
    assert r.operator_hours == 0.0, r.operator_hours
    assert r.operator_work_by_reason() == [], r.operator_work_by_reason()
    assert r.operator_shortfall == 0.0, r.operator_shortfall
    rows = r.downtime_by_reason()
    assert rows == [(NALADKA_LABEL, 12.0, False)], rows
    assert abs(r.machine_balance - 12.0) < 0.02, r.machine_balance
    assert r.machine_shortfall == 0.0 and r.unexplained_downtime == 0.0
    assert r.balance_unknown == "", r.balance_unknown
    print(f"✅ ст.111 без оператора: {rows}, обед {r.lunch_in_downtime}, "
          f"баланс {r.machine_balance} ч")


def test_naladka_all_shift_note_and_text_report():


    r = _report_111()
    assert "очереди на наладку" in r.operator_block_note, r.operator_block_note
    text = format_shift_report(r)
    assert "весь день в наладке" in text, text
    assert "Обед" not in text, text
    print(f"✅ подпись блока оператора: «{r.operator_block_note}»")


def test_naladka_all_shift_untimed_tops_up_to_12():
    """Времени в сообщении нет — простой всё равно закрывает все 12 ч."""
    r = _report_111(hours=None)
    rows = r.downtime_by_reason()
    assert rows == [(NALADKA_LABEL, 12.0, False)], rows
    assert r.lunch_in_downtime == 0.0
    assert abs(r.machine_balance - 12.0) < 0.02, r.machine_balance
    print(f"✅ наладка без времени: {rows}")


def test_named_operator_keeps_lunch_on_naladka_day():
    """Эталон 01.09, ст.121 (Вашурина): 11,25 наладка + 0,75 обед = 12 ч.

    Часы оператора здесь — 0: станок весь день простоял в наладке, названной
    работы за машиной мастер не писал, а по правилу Г и правилу 27.08 наладка
    — время службы наладки, а не оператора. Раньше сюда приходило 11,25 — это
    добивка до нормы, отменённая приказом владельца от 28.09.2026.
    """
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "events": [{"kind": "наладка", "hours": 11.25,
                    "reason": "машина в наладке весь день",
                    "performer": "Вашурина"}]}, date="01.09.2026")
    assert r.naladka_all_shift is True
    assert r.naladka_all_shift_no_operator is False
    assert r.has_operator is True
    assert abs(r.lunch_in_downtime - 0.75) < 0.01, r.lunch_in_downtime
    assert r.operator_hours == 0.0, r.operator_hours
    assert r.operator_work_by_reason() == [], r.operator_work_by_reason()
    labels = {lab: h for lab, h, *_ in r.downtime_by_reason()}
    assert abs(labels.get(NALADKA_LABEL, 0.0) - 11.25) < 0.02, labels
    assert abs(r.machine_balance - 12.0) < 0.02, r.machine_balance
    print(f"✅ ст.121 с фамилией: наладка {labels}, обед "
          f"{r.lunch_in_downtime} ч, оператор {r.operator_hours} ч")


def test_machine_without_any_data_says_no_data():
    """Эталон 02.09 (601, 14тр, 15тр): «нет данных / станок не работал»."""
    r = _one({"machine": "601", "events": []})
    assert r.no_shift_data is True
    assert r.operator_block_note == "нет данных / станок не работал"
    print(f"✅ ст.601: «{r.operator_block_note}»")


# ——— Правило Д: работу оператора писать точнее, а не «работа за машиной» ———

_321 = {
    "machine": "321", "operator": "Петровский",
    "lines": [{"counter_start": 115841, "counter_end": 115949}],
    "events": [
        {"kind": "остановка", "reason": "Подстановка 13 бобин на шпулярник",
         "performer": "Петровский"},
        {"kind": "чинка", "reason": "Стоп. Чинка кромки 4 полотна",
         "performer": "Петровский"},
        {"kind": "чинка", "reason": "Чинка кромки полотна",
         "performer": "Петровский"},
        {"kind": "простой", "reason": "Упаковка пустых картонных гильз",
         "performer": "Петровский"},
        {"kind": "наладка", "reason": "наладка машины",
         "performer": "Шишкин П.В."},
    ]}


def test_untimed_own_operations_are_named_in_operator_row():
    """Операции без времени попадают в расшифровку часов оператора."""
    r = _one(_321)
    assert "Подстановка 13 бобин" in r.untimed_own_summary, r.untimed_own_summary
    assert "Чинка кромки полотна (2 раза)" in r.untimed_own_summary
    rows = r.operator_work_by_reason()
    assert len(rows) == 1, rows
    descr, hours = rows[0]
    assert descr.startswith(CONTROL_LABEL), descr
    assert "в т.ч." in descr and "Упаковка пустых картонных гильз" in descr
    assert "время не указано" in descr, descr
    assert abs(hours - r.operator_hours) < 0.02, (hours, r.operator_hours)
    print(f"✅ ст.321: {descr[:110]}…")


def test_other_persons_untimed_work_stays_out_of_operator_row():
    """Наладка Шишкина без времени — привлечённый персонал, не часы оператора."""
    r = _one(_321)
    assert "наладка" not in r.untimed_own_summary.lower(), r.untimed_own_summary
    assert any("Шишкин" in d for d, _h in r.attracted_by_person()), \
        r.attracted_by_person()
    print(f"✅ Шишкин в привлечённых: {r.attracted_by_person()}")


def test_unexplained_downtime_names_the_reasons():
    """Дыра в 12 ч подписана причинами из переписки, а не «причина не указана»."""
    r = _one(_321)
    assert r.unexplained_downtime > 0.01, r.unexplained_downtime
    label = r.unexplained_label
    assert label != UNEXPLAINED_LABEL, label
    assert "Чинка кромки" in label and "наладка машины" in label, label
    text = format_shift_report(r)
    assert "время не указано у причин" in text, text
    print(f"✅ простой {r.unexplained_downtime} ч: «{label[:100]}…»")


def test_excel_row_uses_named_reasons():
    """В Excel строка простоя тоже подписана причинами."""
    import tempfile

    from openpyxl import load_workbook

    from services.excel_export import export_shift_reports

    r = _one(_321)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as fh:
        path = fh.name
    export_shift_reports([r], path, date="02.09.2026")
    ws = load_workbook(path)["Пояснение 02.09"]
    texts = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
    assert any("время не указано у причин" in t for t in texts), texts
    assert any(t.startswith(CONTROL_LABEL) and "в т.ч." in t for t in texts), \
        [t for t in texts if CONTROL_LABEL in t]
    print("✅ Excel: причины названы и в блоке оператора, и в простое станка")


def test_no_untimed_keeps_plain_labels():
    """Когда всё со временем — ярлыки прежние, без хвостов."""
    r = _report_98()
    assert r.untimed_own_summary == "", r.untimed_own_summary
    assert r.control_label == CONTROL_LABEL, r.control_label
    assert r.unexplained_label == UNEXPLAINED_LABEL, r.unexplained_label
    print(f"✅ ст.98 без записей без времени: «{r.control_label}»")


if __name__ == "__main__":
    test_operator_hours_not_squeezed_to_norm()
    test_excess_from_counter_is_explained_not_flagged()
    test_control_hours_never_exceed_operator_shift()
    test_parallel_operations_not_counted_twice()
    test_naladka_all_shift_without_operator_has_no_lunch()
    test_naladka_all_shift_note_and_text_report()
    test_naladka_all_shift_untimed_tops_up_to_12()
    test_named_operator_keeps_lunch_on_naladka_day()
    test_machine_without_any_data_says_no_data()
    test_untimed_own_operations_are_named_in_operator_row()
    test_other_persons_untimed_work_stays_out_of_operator_row()
    test_unexplained_downtime_names_the_reasons()
    test_excel_row_uses_named_reasons()
    test_no_untimed_keeps_plain_labels()
