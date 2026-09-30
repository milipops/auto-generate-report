"""Правила физики смены на данных ЖИВЫХ прогонов 27.08.2026 («один запрос на
станок»).

Правило пользователя 28.08: считаем ВСЁ КАК ЕСТЬ. Ничего не растягиваем, не
урезаем под норму и не добиваем; расхождение показываем флагом, допуск — 20 мин
и для недостатка, и для переработки. Поэтому здесь проверяется ровно два вида
поведения:

  * две операции «в течение смены» у одного человека НЕ складываются (они шли
    одновременно) — но и не подгоняются под время работы машины: в прогоне бот
    дал ст.98 оператору 25,84 ч;
  * одно и то же дело одного привлечённого не задваивается: ст.151, Камбаров
    5 + 2,5 ч вместо одного раза 2,5.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shift_report import (build_shift_reports_from_structured,
                                   fill_park_machines, format_shift_report)


def _day():
    return {
        "date": "27.08.2026",
        "machines": [
            {
                "machine": "98", "operator": "Дроздова",
                "lines": [{"counter_start": 21221050, "counter_end": 21225850},
                          {"counter_start": 21225850, "counter_end": 21231900}],
                "events": [
                    {"kind": "прочее", "start": "07:00", "end": "19:00",
                     "reason": "замена шпулярника в течении смены",
                     "performer": "Дроздова"},
                    {"kind": "прочее", "start": "07:00", "end": "19:00",
                     "reason": "78 маш. в подработке",
                     "performer": "Дроздова"},
                    {"kind": "подмена", "start": "09:00", "end": "09:55",
                     "reason": "заправка полотна", "performer": "Дроздова"},
                    {"kind": "подмена", "start": "15:00", "end": "15:55",
                     "reason": "заправка полотна", "performer": "Дроздова"},
                ],
            },
            {
                "machine": "151", "operator": "Вашурина",
                "lines": [{"counter_start": 726653, "counter_end": 730222}],
                "events": [
                    {"kind": "подмена", "start": "07:00", "end": "09:30",
                     "reason": "замена шпулярника, 58 бабин",
                     "performer": "Камбаров"},
                    {"kind": "подмена", "start": "16:30", "end": "19:00",
                     "reason": "замена шпулярника, 58 бабин",
                     "performer": "Камбаров"},
                    {"kind": "подмена", "start": "10:00", "end": "12:30",
                     "reason": "замена шпулярника, 50 бабин",
                     "performer": "Бектошев"},
                ],
            },
        ],
    }


def _by_machine():
    return {r.machine: r for r in
            build_shift_reports_from_structured(_day())}


def test_98_shiftlong_parallel_not_summed():
    """Две строки «всю смену» — это одно и то же время, а не 12 + 12.

    Сменная операция шла при РАБОТАЮЩЕЙ машине, поэтому длится столько,
    сколько машина крутилась по счётчику (10,05 ч), а подмены при стоящей
    машине прибавляются: эталон пользователя за 27.08 — 11,82 ч.
    """
    r = _by_machine()["98"]
    rows = r.operator_work_by_reason()
    assert abs(r.operator_hours - 11.89) < 0.02, f"часы: {r.operator_hours}"
    assert max(h for _, h, *_ in rows) <= 12.01, rows
    assert abs(round(sum(h for _, h, *_ in rows), 2) - r.operator_hours) < 0.02
    long_rows = [h for d, h, *_ in rows if "шпулярник" in d]
    assert long_rows and abs(long_rows[0] - r.work_hours) < 0.02, rows
    print(f"✅ 98: оператор {r.operator_hours} ч (не 24 и не 12), строки: {rows}")


def test_98_excess_is_shown_not_squeezed():
    """Переработку НЕ подгоняем под норму — показываем флагом (допуск 20 мин)."""
    r = _by_machine()["98"]
    assert r.operator_excess > 0.33, r.operator_excess
    assert abs(r.operator_excess
               - round(r.operator_hours - r.operator_norm, 2)) < 0.02
    print(f"✅ 98: переработка показана как есть — +{r.operator_excess} ч")


def test_151_helper_counted_once():
    r = _by_machine()["151"]
    hours = [s.hours for s in r.attracted_all()
             if (s.performer or "").startswith("Камбаров")]
    assert len(hours) == 1, f"Камбаров задвоен: {hours}"
    print(f"✅ 151: Камбаров учтён один раз — {hours[0]} ч, "
          f"привлечённые всего {r.attracted_total} ч")


def test_mirrored_arrival_not_double_counted():
    """Прогон (6).xlsx: на ст.151 рядом с «Петровский — замена шпулярника,
    58 бабин» встал «Олег — ушёл на подмену шпулярника НА 151 машину» — тот же
    человек и то же дело, 2,5 ч дважды. Пересказ события другого станка должен
    отбрасываться."""
    day = _day()
    day["machines"][1]["events"].append(
        {"kind": "подмена", "start": "16:30", "end": "19:00",
         "reason": "ушёл на подмену шпулярника на 151 машину, пришёл с подмены",
         "performer": "Олег"})
    r = {x.machine: x for x in
         build_shift_reports_from_structured(day)}["151"]
    names = [(s.performer or "") for s in r.attracted_all()]
    assert not any(n.startswith("Олег") for n in names), names
    print(f"✅ 151: пересказ уходa на 151 отброшен, привлечённые {names}")


def test_overlapping_downtime_is_union_not_sum():
    """Одновременные остановки с одной причиной занимают у станка общее окно, а
    не сумму: иначе станок «перерабатывает» сверх 12 ч (прогон (6): ст.151
    «+1,87 ч сверх 12»)."""
    day = {"date": "27.08.2026", "machines": [{
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 726653, "counter_end": 730222}],
        "events": [
            {"kind": "прочее", "start": "07:00", "end": "09:30",
             "reason": "чинка полотна", "performer": "Вашурина"},
            {"kind": "прочее", "start": "08:00", "end": "09:30",
             "reason": "чинка полотна", "performer": "Вашурина"},
        ]}]}
    r = build_shift_reports_from_structured(day)[0]
    rows = {label: hours for label, hours, *_ in r.downtime_by_reason()}
    assert abs(rows["чинка/ремонт"] - 2.5) < 0.02, rows
    print(f"✅ простой по общему окну: чинка {rows['чинка/ремонт']} ч (не 4,0)")


def test_cross_label_overlap_is_deduplicated_not_double_counted():
    """Простои с РАЗНЫМИ причинами в одних минутах не дают двух простоев.

    Сначала здесь стояло другое правило: строки остаются «как написал мастер»,
    а наложение вычитается только из проверки «сверх 12 ч». Так отчёт ст.111 за
    25.09.2026 печатал 14,78 ч простоя за 12-часовую смену, и пользователь
    сказал: «машина была в наладке и в это время производилась чинка, из за
    этого вышло задвоение часов, надо исправить чтобы таких ошибок не повторялось
    в будущем». Теперь станок в общих минутах стоит ОДИН раз (длинная запись =
    состояние машины забирает минуты), а снятое названо по причинам.
    """
    day = {"date": "27.08.2026", "machines": [{
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 726653, "counter_end": 730222}],
        "events": [
            {"kind": "прочее", "start": "07:00", "end": "12:30",
             "reason": "чинка полотна", "performer": "Вашурина"},
            {"kind": "прочее", "start": "08:00", "end": "10:40",
             "reason": "наладка машины", "performer": "Вашурина"},
        ]}]}
    r = build_shift_reports_from_structured(day)[0]
    rows = {label: hours for label, hours, *_ in r.downtime_by_reason()}
    # Чинка 07:00–12:30 = 5,5 ч, из них 12:00–12:30 (0,5 ч) — обед, который
    # стоит отдельной строкой. Остаётся 5,0 ч. Наладка 08:00–10:40 целиком
    # лежит внутри этих минут, поэтому у станка её нет вовсе.
    assert abs(rows["чинка/ремонт"] - 5.0) < 0.02, rows
    assert "наладка/переналадка" not in rows, rows
    assert abs(r.cross_label_deduped - 2.67) < 0.02, r.cross_label_dedup_detail
    assert [lab for lab, _h in r.cross_label_dedup_detail] == \
        ["наладка/переналадка"], r.cross_label_dedup_detail
    assert r.machine_excess == 0.0, (r.machine_excess, r.machine_balance, rows)
    text = format_shift_report(r)
    assert "одни и те же минуты" in text, text
    print(f"✅ 151: задвоение {r.cross_label_deduped} ч снято со строк и "
          f"названо: {rows}")


def test_park_machines_without_data_are_zero_rows():
    """Станки парка, о которых в переписке нет ни слова, стоят в дне нулевыми
    строками (как в эталоне: 601, 14тр, 15тр), а не пропадают. Ничего не
    считается: без счётчика баланс не проверяется."""
    reports = build_shift_reports_from_structured(_day())
    full = fill_park_machines(reports, "27.08.2026")
    nums = [r.machine for r in full]
    assert nums[:len(reports)] == [r.machine for r in reports], nums
    for num in ("601", "14тр", "15тр", "121", "111", "251", "321"):
        assert num in nums, nums
    empty = [r for r in full if r.machine == "601"][0]
    assert empty.work_hours == 0.0 and empty.operator_hours == 0.0
    assert empty.balance_unknown, "у пустого станка баланс не проверяем"
    assert empty.machine_shortfall == 0.0 and empty.machine_excess == 0.0
    print(f"✅ парк целиком: {nums}")


def test_machine_lost_by_ai_is_flagged_not_silent():
    """Прогон (8): ст.98 (10 ч работы, оператор 11,82) молча исчез из отчёта и
    в Excel выглядел как «нет данных». Станок из переписки, по которому ИИ
    ничего не вернул, обязан остаться со своей пометкой."""
    day = _day()
    day["machines"] = [m for m in day["machines"] if m["machine"] != "98"]
    day["not_parsed"] = ["98"]
    reports = build_shift_reports_from_structured(day)
    lost = [r for r in reports if r.machine == "98"]
    assert lost, [r.machine for r in reports]
    assert lost[0].not_parsed and lost[0].warnings
    lost_note = [w for w in lost[0].warnings if "не вернул" in w]
    assert lost_note, lost[0].warnings
    full = fill_park_machines(reports, "27.08.2026")
    assert len([r for r in full if r.machine == "98"]) == 1
    assert not any(r.not_parsed for r in full if r.machine == "601")
    print(f"✅ потерянный станок помечен: {lost_note[0]}")


def test_absence_on_own_other_machine_is_not_shortfall():
    """Прогон (9): ст.321 дал «оператор −6 ч», хотя эти 6 ч человек отработал
    на ст.251, где он тоже штатный оператор, и они там посчитаны. Известное
    отсутствие — не недостаток времени."""
    day = {"date": "27.08.2026", "machines": [
        {"machine": "321", "operator": "Камбаров",
         "lines": [{"counter_start": 100, "counter_end": 130}],
         "events": [
             {"kind": "отсутствие", "start": "07:00", "end": "13:00",
              "reason": "ушёл работать на 251 машину", "performer": "Камбаров",
              "other_machine": "251"},
             {"kind": "прочее", "start": "13:00", "end": "18:15",
              "reason": "сошла шпуля, замена", "performer": "Камбаров"},
         ]},
        {"machine": "251", "operator": "Камбаров",
         "lines": [{"counter_start": 500, "counter_end": 3000}],
         "events": []},
    ]}
    r = {x.machine: x for x in build_shift_reports_from_structured(day)}["321"]
    assert r.full_absence_hours > 5.5, r.full_absence_hours
    assert r.operator_shortfall == 0.0, (r.operator_shortfall,
                                         r.operator_hours,
                                         r.full_absence_hours)
    print(f"✅ 321: отсутствие {r.full_absence_hours} ч на своём №251 не "
          f"считается недостатком (оператор {r.operator_hours} ч)")


def test_four_naladka_calls_sum_to_reference():
    """Эталон 27.08, ст.151: наладчик Пронкин — 2,13 ч. В переписке четыре
    вызова, каждый со своим «машина в работе»: 9:35→9:50, 10:57→11:56,
    12:50→13:10, 13:50→14:25 = 0,25+0,98+0,33+0,58. Расчёт обязан сложить их в
    одну строку привлечённого (и не объединить в один интервал)."""
    day = {"date": "27.08.2026", "machines": [{
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 726653, "counter_end": 730222}],
        "events": [
            {"kind": "наладка", "start": s, "end": e,
             "reason": "вызван наладчик, идут пропуска",
             "performer": "Пронкин"}
            for s, e in (("09:35", "09:50"), ("10:57", "11:56"),
                         ("12:50", "13:10"), ("13:50", "14:25"))
        ]}]}
    r = build_shift_reports_from_structured(day)[0]
    hours = [s.hours for s in r.attracted_all()
             if (s.performer or "").startswith("Пронкин")]
    assert len(hours) == 4, f"вызовы потерялись: {hours}"
    rows = [(text, h) for text, h in r.attracted_by_person()
            if text.startswith("Пронкин")]
    assert len(rows) == 1, rows
    assert abs(rows[0][1] - 2.13) < 0.03, rows
    stops = {label: h for label, h, *_ in r.downtime_by_reason()}
    assert abs(stops["наладка/переналадка"] - 2.13) < 0.03, stops
    print(f"✅ 151: четыре вызова наладчика = {rows[0][1]} ч (эталон 2,13)")


if __name__ == "__main__":
    test_98_shiftlong_parallel_not_summed()
    test_98_excess_is_shown_not_squeezed()
    test_151_helper_counted_once()
    test_mirrored_arrival_not_double_counted()
    test_overlapping_downtime_is_union_not_sum()
    test_cross_label_overlap_is_deduplicated_not_double_counted()
    test_park_machines_without_data_are_zero_rows()
    test_machine_lost_by_ai_is_flagged_not_silent()
    test_absence_on_own_other_machine_is_not_shortfall()
    test_four_naladka_calls_sum_to_reference()
