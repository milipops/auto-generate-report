# -*- coding: utf-8 -*-
"""Аудит интервальной арифметики: один и тот же час не считается дважды.

Правила расчёта (HANDOFF): смена станка — 12 ч, смена оператора — 11,25 ч
(обед 45 мин), допуск ±20 мин. Из этого следуют два инварианта, которые
раньше держались «на честном слове»:

1) строки простоя станка не могут занять больше смены — а если мастер описал
   одни минуты двумя причинами, повтор обязан быть СНЯТ со строк и НАЗВАН
   (cross_label_deduped + cross_label_dedup_detail), а не спрятан в цифре
   баланса;
2) слагаемые блока «Работа оператора» складываются в его же итог (правило Р22).

Оба проверяются ПО ВЫПУЩЕННОМУ отчёту, а не по отдельной функции: предшественник
этого файла молчал о трёх дефектах сразу, и база тестов была зелёной.

Что закрыто (каждый случай — живая формулировка из переписки участка):

3. Обёртка «07:00–14:04 машина на подмене» и навивка «07:00–12:40» — это ОДНИ
   и те же минуты. _squeeze_time_overlaps снимал наложение цифрой часов, но
   группировка простоя и вырезание обеда считались по СЫРОМУ окну: ст.321 за
   10.09.2026 давала 16,25 ч простоя в 12-часовую смену, а оператор терял
   0,67 ч («!!! РАСХОЖДЕНИЕ: оператор −0,67 ч») — обед снимался с тех минут,
   которых у записи уже не было.
4. Итог оператора ограничен нормой, когда станок отработал всю смену
   (прогон 16.09.2026, ст.151), — но строки продолжали печатать «до
   ограничения»: 11,72 ч при итоге 11,25 ч.
5. Человек у станка должен быть ДОКАЗАН: фамилия, его собственная операция
   или сдвинувшийся счётчик. Прогон 24.09.2026: ст.601 и ст.251 получили по
   11,25 ч «работы по контролю за работой машины» и по строке обеда, хотя
   мастер написал «251 машина(отсутствие оператора)», а ст.15тр — 11,25 ч
   работы, записанные словами «машина в стопе до 15.10.2026 г.». В ручных
   файлах пользователя у таких станков стоит 0 часов оператора и нет обеда.
6. «Отсутствие оператора» — заглушка, добивающая баланс до 12 ч, а не вторая
   строка простоя: стоявший всю смену станок не может дать 23,25 ч (24.09 и
   22.09, ст.15тр).
7. Потолок смены для человека: 12 ч (07:00–19:00), правило пользователя от
   25.09.2026. Филлер «контроль за работой машины» дорастал до времени станка
   по счётчику ПОСЕРЕДИ названных операций, и ст.251 за 21.09.2026 давал 13,05 ч
   работы оператора там, где физически не может быть больше 12.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shift_report import (                    # noqa: E402
    MACHINE_SHIFT_HOURS,
    OPERATOR_SHIFT_HOURS,
    TOLERANCE_H,
    build_shift_reports_from_structured,
    fill_park_machines,
    is_lunch_label,
)

DAYS_DIR = Path(__file__).resolve().parent.parent / "data" / "shifts"


def _day(events, lines=None, **machine):
    m = {"machine": "321", "operator": "Олег", "operator_shift_end": "19:00",
         "events": events}
    if lines:
        m["lines"] = lines
    m.update(machine)
    return build_shift_reports_from_structured({"date": "10.09.2026",
                                                "machines": [m]})[0]


# ─────────────── 1. инварианты по всем сохранённым сменам ───────────────

def _all_reports():
    """(дата, станок, отчёт) по каждому сохранённому разбору ai_raw.json."""
    for path in sorted(DAYS_DIR.glob("*/ai_raw.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        date = path.parent.name
        for r in fill_park_machines(
                build_shift_reports_from_structured(raw), date):
            yield date, r


def _counter_is_plausible(r) -> bool:
    """Показания счётчика целые — значит, баланс смены есть чем проверять.

    Опечатку мастера («Общ сч 1200807» вместо 120807 на ст.321 за 14.09) правит
    слой выгрузки — services/day_pipeline._validate_counters, и ему нужны тексты
    переписки и журнал счётчиков. Здесь перечитывается ТОЛЬКО сохранённый ответ
    модели, поэтому станок с невозможной для смены работой пропускаем честно.
    """
    return r.work_hours <= MACHINE_SHIFT_HOURS + 0.01


def test_operator_rows_sum_to_his_total():
    """Слагаемые «Работа оператора» складываются в итог (правило Р22).

    Проверяются ОБА вида строк: слагаемые итога (operator_hours_parts) и
    строки блока в Excel/1С (operator_work_by_reason) — иначе лист печатает
    11,72 ч при итоге 11,25 ч, как на ст.151 за 16.09.2026.
    """
    for date, r in _all_reports():
        for name, rows in (("слагаемые", r.operator_hours_parts()),
                           ("строки блока", r.operator_work_by_reason())):
            if not rows:
                continue
            summed = round(sum(h for _n, h in rows), 2)
            assert abs(summed - r.operator_hours) <= TOLERANCE_H, (
                date, r.machine, name, summed, r.operator_hours, rows)
    print("✅ строки оператора складываются в итог на всех сохранённых сменах")


def test_machine_balance_never_silently_exceeds_shift():
    """Станок не может «переработать» сверх 12 ч незаметно.

    Превышение допустимо, только если его объясняет обед (lunch_in_balance):
    минуты, названные двумя причинами, снимаются со строк простоя до расчёта
    (см. _deduped_nonabsence), а не вычитаются из баланса задним числом.
    """
    for date, r in _all_reports():
        if not _counter_is_plausible(r):
            continue
        assert r.machine_excess <= TOLERANCE_H, (
            date, r.machine, r.machine_excess, r.downtime_by_reason(),
            r.cross_label_deduped, r.cross_label_dedup_detail)
    print("✅ ни один станок не перерабатывает молча")


def test_operator_never_gets_more_than_a_twelve_hour_shift():
    """Часы оператора ограничены сменой 07:00–19:00 = 12 ч (правило 25.09.2026).

    Слово в слово от пользователя: «больше 12 часов не может быть работы
    оператора так как их смена максимум 12 часов с 7 до 19:00». До этого правила
    филлер «контроль за работой машины» дорастал до времени станка по счётчику
    ПОСЕРЕДИ названных операций, и Петровский на ст.251 за 21.09.2026 получал
    13,05 ч работы в 12-часовую смену.
    """
    for date, r in _all_reports():
        assert r.operator_hours <= MACHINE_SHIFT_HOURS + 0.01, (
            date, r.machine, r.operator_hours, r.operator_hours_parts())
        if not r.has_operator:
            # Человека у станка не доказано — его часов нет вовсе (правило из
            # test_idle_nameless_machines_have_no_operator_hours), хотя
            # «названные» минуты в переписке и есть: ст.14тр за 25.09 стоит в
            # инженерной службе всю смену, и 11,25 ч этой остановки никуда не
            # идут. Проверять сохранность нечего.
            assert r.operator_hours == 0.0, (date, r.machine, r.operator_hours)
            continue
        # Если потолок что-то отрезал, отрезан ровно выдуманный филлер:
        # названные мастером часы остаются как есть.
        assert r.operator_explicit_hours <= r.operator_hours + 0.01, (
            date, r.machine, r.operator_explicit_hours, r.operator_hours)
    print("✅ ни один оператор не получил больше своей смены")


def test_downtime_rows_do_not_reinvent_minutes():
    """Сумма строк простоя НЕ больше смены: задвоенные минуты сняты со строк.

    Раньше правило было мягче — «сумма минус названное наложение». Оно держало
    отчёт в рамки только цифрой баланса, а лист и чат печатали 14,78 ч простоя
    за 12-часовую смену (25.09.2026, ст.111: наладка 5,88 ч + чинка 4,47 ч в
    тех же минутах). Слова пользователя: «машина была в наладке и в это время
    производилась чинка, из за этого вышло задвоение часов, надо исправить».
    Теперь станок в общих минутах стоит ОДИН раз, а снятое — названо.
    """
    for date, r in _all_reports():
        if not _counter_is_plausible(r):
            continue
        rows = round(sum(h for _l, h, _a in r.downtime_by_reason()), 2)
        assert rows <= MACHINE_SHIFT_HOURS + TOLERANCE_H, (
            date, r.machine, rows, r.cross_label_deduped,
            r.downtime_by_reason())
        # Всё снятое обязано быть названо: цифра и список причин сходятся,
        # иначе человек видит «повтор снят», но не знает, откуда именно.
        detail = round(sum(h for _l, h in r.cross_label_dedup_detail), 2)
        assert detail == r.cross_label_deduped, (
            date, r.machine, detail, r.cross_label_deduped)
    print("✅ строки простоя не больше смены, а снятое задвоение названо")


# ─────────────── 2. обёртка и вложенное в неё окно ───────────────

WRAPPER_EVENTS = [
    {"kind": "подмена", "start": "07:00", "end": "14:04",
     "reason": "машина в рабочем состоянии, поставлена на подмену; подмена "
               "шпулей; счётчик за смену не изменился"},
    {"kind": "навивка", "start": "07:00", "end": "12:40",
     "reason": "навивка шпуль на подмену с весовым контролем"},
    {"kind": "навивка", "start": "12:40", "end": "14:04",
     "reason": "навил ещё 18 шпуль на подмену"},
    {"kind": "остановка", "start": "14:04", "end": "19:00",
     "reason": "машина после подмены готова к пропуску узлов, пуска в "
               "переписке нет"},
]


def _wrapper_day():
    return _day(WRAPPER_EVENTS,
                lines=[{"counter_start": 118526, "counter_end": 118526}])


def test_nested_window_is_not_stopped_twice():
    """Обёртка смены не складывается с записями внутри себя."""
    r = _wrapper_day()
    rows = round(sum(h for _l, h, _a in r.downtime_by_reason()), 2)
    assert rows <= MACHINE_SHIFT_HOURS + TOLERANCE_H, (
        rows, r.downtime_by_reason())
    # Вложенная навивка без слова «стоп» — не простой станка, поэтому снимать
    # её минуты с обёртки нечего: задвоения здесь нет вообще.
    assert r.cross_label_deduped <= TOLERANCE_H, (
        r.cross_label_deduped, r.cross_label_dedup_detail,
        r.downtime_by_reason())
    print(f"✅ вложенное окно учтено один раз: простой {rows} ч")


def test_lunch_is_cut_from_free_minutes_only():
    """Обед снимается с тех минут записи, которые не заняли другие операции.

    Навивка 07:00–12:40 забрала раннюю часть обёртки 07:00–14:04, у обёртки
    осталось 12:40–14:04 — там обеда почти нет. Вычитать все 45 мин из её
    часов — терять час работы оператора (прогон 10.09.2026, ст.321: «−0,67 ч»).
    """
    r = _wrapper_day()
    assert abs(r.operator_hours - OPERATOR_SHIFT_HOURS) <= TOLERANCE_H, (
        r.operator_hours, r.operator_hours_parts())
    assert r.operator_shortfall <= TOLERANCE_H, r.operator_shortfall
    print(f"✅ оператор получил свою смену: {r.operator_hours} ч")


def test_windows_survive_the_squeeze():
    """У записи после снятия наложения остаётся её СВОБОДНОЕ окно."""
    r = _wrapper_day()
    for s in r.own_operator_stops():
        free = s.windows_min
        assert free, s
        span = round(sum((e - b) for b, e in free) / 60.0, 2)
        # Часы не могут быть больше своих свободных минут (обед уже снят —
        # поэтому с запасом в 45 мин).
        assert s.hours <= span + 0.76, (s.hours, free, s.stop_label)
    print("✅ часы записи не больше минут, которые она реально занимает")


# ─────────── 3. человек у станка обязан быть доказан ───────────

def _bare(machine: str, **fields):
    """Станок без фамилии оператора — ровно то, что прислал мастер."""
    m = {"machine": machine, "operator_shift_end": "19:00"}
    m.update(fields)
    return build_shift_reports_from_structured(
        {"date": "24.09.2026", "machines": [m]})[0]


def test_idle_nameless_machines_have_no_operator_hours():
    """Ни одного часа оператора и обеда там, где человека не было.

    Проверка по ВСЕМ сохранённым сменам: правила «присутствие доказано» и
    «отсутствие — заглушка до 12 ч» должны держать инвариант на любых данных,
    а не только на разбор 24.09.2026, из-за которого они появились.
    """
    for date, r in _all_reports():
        if r.has_operator:
            continue
        lunch = [row for row in r.downtime_by_reason()
                 if is_lunch_label(row[0])]
        assert r.operator_hours == 0.0, (date, r.machine, r.operator_hours)
        assert not r.operator_work_by_reason(), (date, r.machine,
                                                 r.operator_work_by_reason())
        assert not lunch, (date, r.machine, lunch)
    print("✅ недоказанный оператор не получает ни часов, ни обеда")


def test_absence_filler_never_adds_to_a_stood_machine():
    """Стоявший всю смену станок остаётся в 12 ч: «отсутствия» второй строки нет."""
    r = _bare("15тр", events=[{"kind": "остановка", "start": "07:00",
                               "end": "19:00",
                               "reason": "машина в стопе до 15.10.2026 г."}])
    rows = r.downtime_by_reason()
    assert len(rows) == 1, rows
    label, hours, active = rows[0]
    assert label.startswith("машина в стопе до 15.10"), label
    assert abs(hours - MACHINE_SHIFT_HOURS) <= TOLERANCE_H, rows
    # В долгосрочном стопе у машины никого — это статус станка, а не работа.
    assert active is False, rows
    assert r.operator_hours == 0.0, r.operator_work_by_reason()
    print(f"✅ стоп до даты: {label} {hours} ч, оператора нет")


def test_absence_without_operator_is_one_row():
    """«Отсутствие оператора» — одна строка-заглушка, без часов и обеда оператора."""
    r = _bare("251", lines=[{"counter_start": 300125}],
              events=[{"kind": "отсутствие", "start": "07:00", "end": "19:00",
                       "reason": "отсутствие оператора"}])
    rows = r.downtime_by_reason()
    assert len(rows) == 1, rows
    assert rows[0][0] == "отсутствие оператора", rows
    assert abs(rows[0][1] - OPERATOR_SHIFT_HOURS) <= 0.01, rows
    assert r.has_operator is False, r.has_operator
    assert r.operator_hours == 0.0, r.operator_work_by_reason()
    print("✅ отсутствие оператора = 11,25 ч простоя и 0 часов оператора")


def test_lone_counter_reading_is_not_a_person():
    """Одно показание счётчика — цифра из переписки, а не движение машины."""
    idle = _bare("601", lines=[{"counter_start": 3083.0, "panels": 2,
                                "assortment": "ПСШПЭМН 2/6,0-81,0-49,5"}])
    assert idle.has_operator is False, idle.has_operator
    assert idle.operator_hours == 0.0, idle.operator_work_by_reason()
    assert idle.lunch_hours == 0.0, idle.lunch_hours
    # Зеркало правила: счётчик СДВИНУЛСЯ — станок кто-то вёл, человека это
    # доказывает, и его смена считается (иначе правило съело бы реальных
    # операторов без фамилии в разборе).
    ran = _bare("601", lines=[{"counter_start": 3083.0,
                               "counter_end": 4083.0, "panels": 2,
                               "assortment": "ПСШПЭМН 2/6,0-81,0-49,5"}])
    assert ran.has_operator is True, ran.work_hours
    print("✅ одиночное показание счётчика не рождает оператора")


def test_ceiling_takes_only_the_invented_control_hours():
    """Потолок смены снимает часы с «контроля за машиной», а не с записей мастера.

    Филлер «работа по контролю за работой машины» — единственное слагаемое,
    которого в переписке нет: его и режет правило 25.09.2026. Названные операции
    обязаны остаться в целости, а слагаемые — сложиться в итог (Р22).
    """
    events = [{"kind": "остановка", "start": "07:00", "end": "12:00",
               "reason": "чинка полотна, машина в стопе"},
              {"kind": "подмена", "start": "12:45", "end": "16:15",
               "reason": "заправка полотна на подмене"}]
    # Счётчик на 10 ч работы станка (норма ст.98 = 7,3 узл/мин): «контроля за
    # машиной» из него выходит 10 ч, и без потолка было бы 8,5 + 10 = 18,5 ч.
    r = _day(events, lines=[{"counter_start": 0, "counter_end": 4380}],
             machine="98")
    assert abs(r.operator_explicit_hours - 8.5) <= 0.01, r.operator_explicit_hours
    assert abs(r.operator_hours - MACHINE_SHIFT_HOURS) <= 0.01, r.operator_hours
    assert r.operator_ceiling_clip > 0.01, r.operator_ceiling_clip
    # Часы мастера остались, обрезан только филлер.
    assert abs(r.operator_control_printed
               - (r.operator_hours - r.operator_explicit_hours)) <= 0.01, (
        r.operator_control_printed, r.operator_hours_parts())
    for name, rows in (("слагаемые", r.operator_hours_parts()),
                       ("строки блока", r.operator_work_by_reason())):
        assert abs(round(sum(h for _n, h in rows), 2)
                   - r.operator_hours) <= TOLERANCE_H, (name, rows)
    print(f"✅ потолок снял {r.operator_ceiling_clip} ч филлера, "
          f"записи мастера целы")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nПроверено тестов: {len(fns)}.")
