"""Часы оператора не выравниваются по норме 11,25 — ни вверх, ни вниз.

Приказ владельца от 28.09.2026, дословно: «так же я заметил у тебя такую вещь,
что смены не могут быть больше 11,25, как например по 98 машине. не нужно их
добивать до 11,25, если не хватает или переизбыток — то оставляй как есть».

Это второе подтверждение правила В от 03.09.2026 («если оператор работал
больше, то такое может быть — не нужно уменьшать ему время работы… Если есть
какие-то расхождения и в большую, и в меньшую сторону — это надо оставлять как
есть»). До 28.09 код делал две вещи, которые правило запрещает:

* добивал итог строкой «контроль за работой машины» до нормы — у оператора
  почти всегда получалось ровно 11,25, и реального недостатка отчёт не показывал
  (прогон 26.09.2026, ст.98: названо 8,64 ч операций, станок работал 9,4 ч, а
  в отчёте стояло 11,25 из 11,25);
* обрезал итог до нормы, когда часов выходило больше.

Что остаётся обязательным:

* «контроль за работой машины» — только время работающего станка;
* потолок смены 12 ч (приказ от 25.09.2026) — он физический, его не снимаем;
* часы оператора не больше его ПРИСУТСТВИЯ у станка (operator_shift_span) — это
  тоже физика; но мерой служит присутствие, а не норма: норма вычитает обед
  второй раз и была бы запрещённой обрезкой под 11,25;
* строки оператора складываются в его итог (правило Р22);
* недостаток и переработка видны словами и в чате, и в Excel;
* баланс СТАНКА по-прежнему закрывается заглушкой до 12 ч — это другой счёт.

Поправка от 30.09.2026 (вопрос №140, дословно): «остаток часов оператора до
твоей 11,25 добирать строкой „контроль за работой машины“» — «это да, но если
расхождение не прям большое (до 1-1,5 часа)». То есть запрет добивки действует
для БОЛЬШОГО разрыва; разрыв до 1,5 ч добор строкой закрывает, и добор тоже
упирается в присутствие, а не в норму. Граница — в OPERATOR_PAD_MAX_H, её
проверяет tests/test_operator_pad_3009.py.

Запуск: python tests/test_shift_no_norm_padding.py (или через tests/_run_all.py).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services.shift_report import (MACHINE_SHIFT_HOURS, NALADKA_LABEL,  # noqa: E402
                                   TOLERANCE_H,
                                   build_shift_reports_from_structured,
                                   format_shift_report)
from services.excel_export import _operator_extra_note  # noqa: E402


def _nodes(hours: float, machine: str = "98") -> int:
    """Показание счётчика, соответствующее `hours` работы станка."""
    from services import machine_rates
    return round(hours * machine_rates.get_rate(machine) * 60)


def _short_shift():
    """Станок работал меньше смены, названо две операции. Часов не хватает."""
    return build_shift_reports_from_structured({
        "date": "26.09.2026", "machines": [{
            "machine": "98", "operator": "Белякова",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 0, "counter_end": _nodes(6.0)}],
            "events": [
                {"kind": "чинка", "start": "10:00", "end": "11:00",
                 "reason": "чинка полотна после обрыва нити",
                 "performer": "Белякова"},
                {"kind": "обед", "start": "12:00", "end": "12:45",
                 "reason": "обед"},
            ]}]})[0]


def _surplus_shift():
    """Названная работа при стоявшем станке + время идущего станка больше нормы."""
    return build_shift_reports_from_structured({
        "date": "26.09.2026", "machines": [{
            "machine": "98", "operator": "Белякова",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 0, "counter_end": _nodes(6.0)}],
            "events": [
                {"kind": "чинка", "start": "07:30", "end": "13:00",
                 "reason": "чинка полотна после обрыва нити",
                 "performer": "Белякова"},
                {"kind": "обед", "start": "13:00", "end": "13:45",
                 "reason": "обед"},
            ]}]})[0]


def _ceiling_shift():
    """Подмены на чужих станках на всю смену — больше 12 ч физически не бывает."""
    return build_shift_reports_from_structured({
        "date": "26.09.2026", "machines": [{
            "machine": "98", "operator": "Белякова",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 0, "counter_end": _nodes(12.0)}],
            "events": [
                {"kind": "подмена", "start": "07:00", "end": "13:00",
                 "reason": "подмена на 251 машину", "performer": "Белякова",
                 "other_machine": "251"},
                {"kind": "подмена", "start": "13:00", "end": "19:00",
                 "reason": "подмена на 111 машину", "performer": "Белякова",
                 "other_machine": "111"},
            ]}]})[0]


def test_hours_are_not_padded_up_to_norm():
    """Нехватку видно как нехватку, а не как ровную норму."""
    r = _short_shift()
    assert r.operator_hours < r.operator_norm - TOLERANCE_H, (
        r.operator_hours, r.operator_norm)
    assert r.operator_hours != 11.25, r.operator_hours_parts()
    assert r.operator_shortfall > TOLERANCE_H, r.operator_shortfall
    assert r.operator_control_hours <= r.work_hours + 0.01, (
        r.operator_control_hours, r.work_hours)
    print(f"✅ нет добивки: оператор {r.operator_hours} ч при норме "
          f"{r.operator_norm}, недостаток {r.operator_shortfall} ч")


def test_padded_row_is_only_the_machines_running_time():
    """«Контроль за работой машины» = время работающего станка, не остаток до нормы."""
    r = _short_shift()
    assert abs(r.operator_control_hours - r.operator_machine_led_hours) < 0.01, (
        r.operator_control_hours, r.operator_machine_led_hours)
    assert abs(r.operator_hours - (r.operator_explicit_hours
                                   + r.operator_control_hours)) <= 0.01, (
        r.operator_hours, r.operator_explicit_hours, r.operator_control_hours)
    print(f"✅ контроль {r.operator_control_hours} ч = работа машины "
          f"{r.work_hours} ч за минусом названных окон")


def test_surplus_over_norm_is_left_alone():
    """Переизбыток тоже не режем: сколько сложилось, столько и стоит."""
    r = _surplus_shift()
    assert abs(r.operator_hours - 11.5) < 0.02, r.operator_hours_parts()
    assert r.operator_hours > r.operator_norm, (r.operator_hours, r.operator_norm)
    assert abs(r.operator_hours - (r.operator_explicit_hours
                                   + r.operator_control_hours)) <= 0.01
    # 0,25 ч сверх нормы — внутри допуска ±20 мин, поэтому флага переработки
    # здесь нет. Обрезки до 11,25 тоже нет: стоит ровно 11,5 ч.
    assert r.operator_excess == 0.0, r.operator_excess
    print(f"✅ переизбыток не обрезан: {r.operator_hours} ч при норме "
          f"{r.operator_norm}")


def test_shift_ceiling_still_cuts():
    """Потолок смены 12 ч (приказ от 25.09.2026) остался: физически больше не было.

    Норму 11,25 не подгоняем, но смена длиннее 12 ч быть не может: здесь два
    названных окна подмены по 6 ч дают 12,0 ч, и переработка 0,75 ч видна
    отдельным флагом, а не спрятана в норму.
    """
    r = _ceiling_shift()
    assert abs(r.operator_hours - MACHINE_SHIFT_HOURS) < 0.01, r.operator_hours
    assert abs(r.operator_excess - 0.75) < 0.02, r.operator_excess
    assert r.operator_ceiling_clip >= 0.0, r.operator_ceiling_clip
    print(f"✅ потолок смены: {r.operator_hours} ч, переработка "
          f"{r.operator_excess} ч, срезано {r.operator_ceiling_clip} ч")


def test_rows_still_sum_to_the_total():
    """Правило Р22: строки блока оператора дают его итог — без «остатка до нормы»."""
    for r in (_short_shift(), _surplus_shift(), _ceiling_shift()):
        parts = dict(r.operator_hours_parts())
        assert parts, r.operator
        assert abs(sum(parts.values()) - r.operator_hours) <= 0.01, (
            parts, r.operator_hours)
    print("✅ слагаемые складываются в итог")


def test_machine_balance_stub_is_untouched():
    """Заглушка баланса СТАНКА до 12 ч осталась — это другой счёт, не часы человека.

    Методику 26.08 для станка трогать нельзя: у станка, откуда человек уходил на
    подмену, остаток смены закрыт строкой «отсутствие оператора». Его же часы по
    приказу от 30.09.2026 (№140) добираются до нормы, когда разрыв небольшой:
    здесь не хватает ровно 1,0 ч, и они стоят строкой «контроль за работой
    машины».
    """
    r = _absence_shift()
    labels = [lab for lab, _h, _a in r.downtime_by_reason()]
    assert any(lab.startswith("отсутствие оператора") for lab in labels), labels
    assert abs(r.machine_balance - MACHINE_SHIFT_HOURS) <= TOLERANCE_H, (
        r.machine_balance, labels)
    assert abs(r.operator_hours - 11.25) < 0.02, r.operator_hours_parts()
    assert r.operator_shortfall == 0.0, r.operator_shortfall
    print(f"✅ баланс станка закрыт заглушкой {labels}, оператор "
          f"{r.operator_hours} ч, недостаток {r.operator_shortfall} ч")


def _absence_shift():
    """Станок 321: оператор уходил на подмену — баланс станка закрывает заглушка."""
    return build_shift_reports_from_structured({
        "date": "26.09.2026", "machines": [{
            "machine": "321", "operator": "Петровский",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 0, "counter_end": _nodes(9.0, "321")}],
            "events": [
                {"kind": "подмена", "start": "13:00", "end": "14:15",
                 "reason": "ушёл на подмену на 251 машину",
                 "performer": "Петровский", "other_machine": "251"},
            ]}]})[0]


def test_report_says_the_gap_in_words():
    """Человек видит расхождение словами, а не молчаливую норму."""
    r = _short_shift()
    text = format_shift_report(r)
    assert "недостаток" in text.lower(), text[:600]
    assert str(round(r.operator_hours, 2)).replace(".", ",") in text or \
        f"{r.operator_hours:g}".replace(".", ",") in text, text[:600]
    print("✅ в тексте отчёта есть слово «недостаток» и реальные часы")


# ——— Ветки, которые приказ 28.09.2026 вскрыл при ревизии правил ———

def _naladka_day(hours=8.0):
    """Станок весь день в наладке, оператор назван, мастер указал не всю смену."""
    ev = {"kind": "наладка", "reason": "машина в наладке весь день",
          "performer": "Вашурина"}
    if hours:
        ev["hours"] = hours
    return build_shift_reports_from_structured({
        "date": "01.09.2026", "machines": [{
            "machine": "121", "operator": "Вашурина", "events": [ev]}]})[0]


def test_naladka_day_tops_up_the_machine_not_the_person():
    """Остаток смены наладки закрывает день СТАНКА, а не часы человека.

    Ветка `naladka_all_shift` при названном операторе добирала остаток строкой
    «наладка (весь день)» как его работу — то есть ровно добивала смену до
    11,25 ч, что приказ от 28.09.2026 запрещает (плюс наладка — время службы
    наладки, правило Г от 03.09.2026).
    """
    r = _naladka_day()
    assert r.naladka_all_shift is True
    assert r.has_operator is True
    filler = [s for s in r.tech_stops
              if "весь день" in (s.reason or "").lower()]
    assert filler, r.tech_stops
    assert all(not s.operator_active for s in filler), \
        [(s.hours, s.operator_active) for s in filler]
    assert r.operator_hours == 0.0, r.operator_hours_parts()
    # День станка от этого не разъехался: 11,25 наладки + 45 мин обеда = 12 ч.
    labels = {lab: h for lab, h, *_ in r.downtime_by_reason()}
    assert abs(labels.get(NALADKA_LABEL, 0.0) - 11.25) < 0.02, labels
    assert abs(r.machine_balance - MACHINE_SHIFT_HOURS) < 0.02, r.machine_balance
    print(f"✅ наладка добивает станок ({labels}), а не человека "
          f"({r.operator_hours} ч)")


def test_naladka_top_up_is_the_same_when_time_is_missing():
    """Без времени в сообщении итот тот же: станок 12 ч, у человека 0 ч."""
    r = _naladka_day(hours=None)
    assert r.operator_hours == 0.0, r.operator_hours_parts()
    assert abs(sum(s.hours for s in r.tech_stops if not s.elsewhere)
               - 11.25) < 0.02, r.tech_stops
    print(f"✅ наладка без времени: станок закрыт, оператор "
          f"{r.operator_hours} ч")


def _long_running_machine():
    """Станок по счётчику работал 12 ч — больше нормы сотрудника в 11,25 ч."""
    return build_shift_reports_from_structured({
        "date": "26.09.2026", "machines": [{
            "machine": "98", "operator": "Белякова",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 0, "counter_end": _nodes(12.0)}],
            "events": []}]})[0]


def test_machine_time_longer_than_norm_is_not_cut():
    """`operator_machine_led_hours` не сжимается нормой: 12 ч станка так и есть."""
    r = _long_running_machine()
    assert r.work_hours > r.operator_norm + TOLERANCE_H, (
        r.work_hours, r.operator_norm)
    assert abs(r.operator_control_hours - r.work_hours) < 0.02, (
        r.operator_control_hours, r.work_hours)
    assert r.operator_control_hours > r.operator_norm, r.operator_control_hours
    # Переизбыток теперь виден, а не спрятан обрезкой.
    assert r.operator_excess > TOLERANCE_H, r.operator_excess
    assert abs(r.operator_hours - MACHINE_SHIFT_HOURS) < 0.01, r.operator_hours
    print(f"✅ станок {r.work_hours} ч: контроль {r.operator_control_hours} ч "
          f"не обрезан нормой {r.operator_norm}, переработка "
          f"{r.operator_excess} ч видна")


def _rows_over_shift():
    """Мастер описал 14 ч названных работ при смене 07:00–19:00."""
    return build_shift_reports_from_structured({
        "date": "01.09.2026", "machines": [{
            "machine": "98", "operator": "Иванова",
            "operator_shift_end": "19:00",
            "lines": [{"counter_start": 0, "counter_end": _nodes(1.0)}],
            "events": [
                {"kind": "остановка", "hours": 7.0, "reason": "чистка машины",
                 "performer": "Иванова"},
                {"kind": "остановка", "hours": 7.0,
                 "reason": "перестановка телег", "performer": "Иванова"},
            ]}]})[0]


def test_silent_row_cut_is_named_in_report_and_excel():
    """Срез «с самой длинной строки» обязан быть назван, а не молча сделан."""
    r = _rows_over_shift()
    assert r.operator_explicit_hours > MACHINE_SHIFT_HOURS, (
        r.operator_explicit_hours)
    assert r.operator_rows_over_shift > 0.01, r.operator_rows_over_shift
    desc, cut = r.operator_rows_clip
    assert cut > 0.01 and desc, (desc, cut)
    rows = r.operator_work_by_reason()
    assert abs(sum(h for _d, h in rows) - MACHINE_SHIFT_HOURS) < 0.02, rows
    text = format_shift_report(r)
    assert "длиннее смены" in text, text[-900:]
    assert desc in text, (desc, text[-900:])
    # В Excel — те же слова, иначе человек в файле видит обрезанную строку без
    # объяснения (так уже было с переработкой: задача #67).
    note = _operator_extra_note(r)
    assert "снято" in note and desc in note, note
    # Фраза про «контроль за работой машины» была бы здесь неправдой: филлера
    # в обрезке нет ни минуты.
    assert "контроля за работой машины" not in note, note
    print(f"✅ срез назван: строка «{desc}» потеряла {cut} ч и в чате, "
          f"и в Excel")



def _short_presence_long_machine():
    """Станок работал 10,19 ч, а человек у него был только до 15:00 (8 ч)."""
    return build_shift_reports_from_structured({
        "date": "26.09.2026", "machines": [{
            "machine": "98", "operator": "Белякова",
            "operator_shift_end": "15:00",
            "lines": [{"counter_start": 0, "counter_end": _nodes(10.19)}],
            "events": []}]})[0]


def test_presence_is_the_ceiling_not_the_norm():
    """Больше присутствия человек не получает, но и нормой эти часы не режутся.

    Смена до 15:00 (8 ч), станок по счётчике 10,19 ч. Правильный потолок — 8 ч
    ПРИСУТСТВИЯ: «контроля» за часы, когда человека у станка не было, быть не
    может (это выдуманное время, а не переизбыток). Норма 7,25 ч потолком быть
    не может: она вычитает обед второй раз (обед уже в простое станка) и была бы
    той самой обрезкой под 11,25, которую запретил владелец.
    """
    r = _short_presence_long_machine()
    assert abs(r.operator_shift_span - 8.0) < 0.01, r.operator_shift_span
    assert r.operator_norm < r.operator_shift_span, (r.operator_norm,
                                                     r.operator_shift_span)
    assert r.operator_led_raw_hours > r.operator_shift_span, (
        r.operator_led_raw_hours)
    assert abs(r.operator_control_hours - r.operator_shift_span) < 0.01, (
        r.operator_control_hours, r.operator_shift_span)
    assert abs(r.operator_hours - r.operator_shift_span) < 0.01, r.operator_hours
    assert abs(r.operator_span_clip
               - (r.operator_led_raw_hours - r.operator_shift_span)) < 0.01, (
        r.operator_span_clip)
    # Снятое названо словами и в чате, и в Excel — молча часы не теряются.
    text = format_shift_report(r)
    assert "времени у станка сверх присутствия" in text, text[-900:]
    note = _operator_extra_note(r)
    assert "сверх присутствия" in note, note
    print(f"✅ станок {r.operator_led_raw_hours} ч при присутствии "
          f"{r.operator_shift_span} ч: контроль {r.operator_control_hours} ч, "
          f"снято {r.operator_span_clip} ч названо (норма {r.operator_norm} ч "
          "не мера)")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
    print("\nПроверено тестов: "
          f"{sum(1 for k in globals() if k.startswith('test_'))}.")
