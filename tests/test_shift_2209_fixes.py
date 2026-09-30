# -*- coding: utf-8 -*-
"""Регрессии прогона 21–22.09.2026: таблица «ДД.ММ» против ручного файла.

Эталон пользователя — «Книга1.xlsx» (листы «22.09.2026» и «22.09»), который он
свёл руками по переписке. Разбирали 6 расхождений; каждое ниже закреплено
тестом, потому что все они рождались из правдоподобных, но неверных прочтений
утреннего блока мастера:

1. Станок стоял всю смену (ст.121/601/14тр/15тр/321), а в таблицу ложились
   «Срез 3 из 3», счётчик 888020 и «12 подмен» из утренней сводки — вчерашнее
   состояние станка выдавалось за результат смены;
2. Колонка «Технические остановки» у стоявшего весь день станка была либо
   пустой, либо 12 ч — в ручном файле там 11,25 (смена минус обед 0,75);
3. В «Технические остановки» попадали обед и подмены — их же видно в своих
   колонках («подмены, ч», строка «простой обед» в «Пояснении»), а в эталоне
   ст.98 с обедами и двумя подменами стоит с пустой колонкой;
4. «подмены, ч» считались суммой по людям: ст.151 за 22.09 давала 16,43 ч
   подмен при смене 12 ч, потому что на одном окне стояли Сеньков и Клинов;
5. ИИ добавлял строку ассортимента без счётчиков (план перехода) — в таблицу
   падал второй ряд без станка, клиента и времени;
6. В «фио на подменах» попадали не-подмены (ст.251: «лола п.» — мастер, которая
   написала распоряжение), а сама оператор, делавшая подмену, исчезала.
7. Метке модели kind «подмена» верилось больше, чем тексту мастера:
   вытягивание полотна из-под вала руками наладчика становилось подменой;
8. чинка полотна с словом «подмена» в том же предложении уходила из «чинка» в
   «подмены, ч» (ст.321: 1,68 ч против 5,95 ч чинки в ручном файле);
9. сводка «5 подмена из 6» внутри причины простоя читалась как операция этого
   окна, и часы среза стояли в колонке подмен.

Данные берём из сохранённых ai_raw.json (детерминированный слой, без запросов
к ИИ) — так тест проверяет ровно то, что видит человек в Excel.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl                                        # noqa: E402

from services.shift_report import (                     # noqa: E402
    MACHINE_SHIFT_HOURS,
    TOLERANCE_H,
    _balance_gap_line,
    build_shift_reports_from_structured,
    classify_stop,
    creel_change_now,
    format_shift_report,
    is_real_submen,
    is_lunch_label,
    is_submen_label,
)
from services.excel_export import (                     # noqa: E402
    _result_columns,
    _stops_columns,
    _subs_fio,
    _subs_hours_cell,
    export_shift_reports,
)

COLS = "ABCDEFGHIJKLMNOPQRSTU"


def _reports(date: str):
    """Снимок ИИ-разбора из tests/, а не из живого data/shifts.

    Живой `ai_raw.json` перезаписывается КАЖДЫМ платным прогоном (21.09 и 22.09
    пересобирали 24.09 дважды), и тест стал падать не потому, что сломался код, а
    потому, что модель в новый раз формулировала причину другими словами. Здесь
    зафиксирован ровно тот разбор, по которому сверялись с ручным файлом, —
    проверка стала про детерминированный слой, как и задумано.
    """
    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ai_raw_" + date.replace(".", "_") + ".json")
    with open(fixture, encoding="utf-8") as f:
        data = json.load(f)
    return build_shift_reports_from_structured(data)


def _r(date: str, num: str):
    for r in _reports(date):
        if str(r.machine) == num:
            return r
    raise AssertionError(f"нет станка {num} за {date}")


def _table_rows(date: str) -> dict:
    """Строки листа-таблицы из настоящего файла: {№ машины: {колонка: значение}}.

    Ряды продолжения (вторая строка станка) идут под первым и собираются в
    список — так проверяется и «призрак» без счётчика (п.5).
    """
    reports = _reports(date)
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "t.xlsx")
        export_shift_reports(reports, path, date)
        wb = openpyxl.load_workbook(path)
        ws = wb[wb.sheetnames[1]]
        rows, current = {}, None
        for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
            cells = {c.column_letter: c.value for c in row
                     if c.value not in (None, "")}
            if not cells:
                continue
            machine = str(cells.get("B", "")).strip() if "B" in cells else None
            if machine:
                current = machine
                rows.setdefault(current, []).append(cells)
            elif current:
                rows[current].append(cells)
        return rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _machine_notes(date: str) -> dict:
    """Примечания (колонка E) строки «Работа машины всего» на листе-пояснении.

    {№ машины: текст примечания} — '' там, где пояснения нет.
    """
    reports = _reports(date)
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "t.xlsx")
        export_shift_reports(reports, path, date)
        wb = openpyxl.load_workbook(path)
        ws = wb[wb.sheetnames[0]]
        notes, current = {}, None
        for row in ws.iter_rows(min_col=1, max_col=5):
            a = str(row[0].value or "").strip()
            if a == "№ машины":
                current = str(row[1].value or "").strip()
                notes.setdefault(current, "")
            elif a == "Работа машины всего" and current:
                notes[current] = str(row[4].value or "").strip()
        return notes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────── 1. стоящий станок: ни срезов, ни счётчиков ───────────
def test_all_shift_stops_produce_nothing():
    """У станции, стоявшей всю смену, результата нет — и быть не должно.

    Строка этого теста когда-то склеилась с комментарием-разделителем в одну
    строку (def уехал в хвост комментария), и файл импортировался без ошибки:
    тело просто доставалось функции _machine_notes выше. Проверка пункта 1
    реестра 22.09 молчала, пока не вернули отступ.
    """
    for num in ("121", "601", "14тр", "15тр"):
        r = _r("22.09.2026", num)
        assert r.nothing_produced, f"ст.{num} 22.09 должен быть «ничего не выпущено»"
    assert _r("21.09.2026", "321").nothing_produced
    # А станки, которые всё-таки вязали, не должны попасть под правило.
    for date, num in (("22.09.2026", "98"), ("22.09.2026", "151"),
                      ("22.09.2026", "321"), ("21.09.2026", "98")):
        assert not _r(date, num).nothing_produced, f"ст.{num} {date} работал"


def test_stopped_machine_row_has_no_counters():
    """К/L/M/S у стоявшей машины пустые — вчерашние цифры не результат смены."""
    rows = _table_rows("22.09.2026")
    for num in ("121", "601", "14тр", "15тр"):
        cells = rows[num][0]
        for col in ("K", "L", "M", "S"):
            assert col not in cells, f"ст.{num}: колонка {col} = {cells[col]!r}"
    # Рабочая машина обязана остаться с числами.
    c98 = rows["98"][0]
    assert c98["L"] == 21509000 and c98["M"] == 21520000, c98
    assert c98["K"] == 7, c98


def test_morning_summary_not_in_text_report():
    """В «Пояснении» тоже нет «Срезов: 3» и «Подмены: 12» у стоявшей машины."""
    from services.shift_report import format_shift_report
    text = format_shift_report(_r("22.09.2026", "121"))
    assert "Срезов" not in text, text
    assert "Подмены" not in text, text


# ─────── 2+3. тех. остановки: 11,25 для стоявших, без обеда/подмен ───────

def test_tech_stops_column_1125_for_stopped_machines():
    rows = _table_rows("22.09.2026")
    for num in ("121", "601", "14тр", "15тр"):
        assert rows[num][0]["Q"] == 11.25, (num, rows[num][0].get("Q"))
    # «Инженерная служба» — только там, где её написал мастер в заголовке темы:
    # «601 машина(инж. служба до 25.09)», «14т машина(в стопе до 15.10.2026г.
    # инж. служба)», 121 — в тексте про инженерную службу. Тема 15т звучит как
    # «15т машина(в стопе до 15.10.2026 г.)», без ссылки на службу, и там
    # причина — наладка по решениям совещания. Первый разбор ИИ это путал и
    # переносил службу с 14тр на 15тр; проверка на всех четырёх станках одной
    # строкой закрепляла выдумку, а не факт.
    for num in ("121", "601", "14тр"):
        assert rows[num][0]["R"] == "инженерная служба", (num, rows[num][0].get("R"))
    assert rows["15тр"][0]["R"] == "наладка/переналадка", rows["15тр"][0].get("R")
    # 22.09 ст.251 и 21.09 ст.321 — стояли всю смену по своей причине.
    assert _table_rows("22.09.2026")["251"][0]["Q"] == 11.25
    assert _table_rows("21.09.2026")["321"][0]["Q"] == 11.25


def test_lunch_and_submen_leave_tech_stops_column():
    """Ст.98 (подмены + обед) — колонка тех. остановок пустая, как в эталоне."""
    r = _r("22.09.2026", "98")
    assert _stops_columns(r) == ("", ""), _stops_columns(r)
    # Раз метки распознаются — иначе правило молча перестанет работать.
    assert is_lunch_label("обед (станок стоит)")
    assert is_submen_label("подмена (заправка полотна)")
    assert is_submen_label("замена шпулярника (станок стоит)")
    assert not is_submen_label("чинка/ремонт")
    assert not is_lunch_label("чинка/ремонт")


def test_tech_stops_keep_real_breakdown():
    """Чинка и наладка остаются суммой-строкой — правило 11,25 их не съедает."""
    r = _r("22.09.2026", "321")
    time_, reason = _stops_columns(r)
    assert "+" in str(time_) and "чинка" in reason, (time_, reason)
    assert "обед" not in reason and "одмен" not in reason, reason


# ────────── 4. «подмены, ч» — окно простоя станка, не сумма ──────────

def test_subs_window_is_a_window_not_a_sum_of_people():
    r = _r("22.09.2026", "151")
    assert r.attracted_subs_total > MACHINE_SHIFT_HOURS, r.attracted_subs_total
    h = r.subs_window_hours
    assert h <= MACHINE_SHIFT_HOURS, h
    # В ручном файле пользователя 8,66 ч; расхождение 0,24 ч — окно
    # «14:16–16:43 подвязывание нитей со шпулярника» засчитано целиком.
    assert abs(h - 8.9) < 0.05, h
    assert abs(_subs_hours_cell(r) - 8.9) < 0.05


def test_incidental_creel_mention_is_not_a_submen():
    """Окно подмены считает глагол замены, а не слово «шпулярник» в тексте.

    Когда подмену разрешили находить по словам мастера (без kind «подмена»), на
    22.09 ст.151 в «подмены, ч» переехали 1,87 ч работы с остатками: «сортировка
    ченарышей ассортимента ПА187×3 со шпулярника» и «уборка площадки … после
    замены шпулярника». Шпулярник в обеих строках назван, но меняют его не в это
    время — вторая вообще о том, что УЖЕ сделали.
    """
    assert creel_change_now("меняю на шпулярнике с малым количеством шнура")
    assert creel_change_now("коробки с ченарышами после замены шпулярника "
                            "установлены у стены") is False
    assert creel_change_now("сортировка ченарышей со шпулярника, вес нити 490 гр") is False


def test_three_people_one_window_give_one_window():
    """Синтетика: трое на одном окне = одно окно (ст.151/22.09 в миниатюре)."""
    machine = {"machine": "98", "operator": "Тестова",
               "operator_shift_end": "19:00",
               "lines": [{"assortment": "187х3", "counter_start": 1000,
                          "counter_end": 4000}],
               "events": [
                   {"start": "10:00", "end": "12:00", "kind": "подмена",
                    "reason": "подмена шпулей", "performer": "Сеньков"},
                   {"start": "10:00", "end": "12:00", "kind": "подмена",
                    "reason": "подмена шпулей", "performer": "Клинов"},
                   {"start": "10:00", "end": "12:00", "kind": "подмена",
                    "reason": "подмена шпулей", "performer": "Петров"},
               ]}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    assert abs(r.subs_window_hours - 2.0) < 0.01, r.subs_window_hours
    assert r.attracted_subs_total > 5.9, r.attracted_subs_total


def test_lunch_inside_submen_window_is_not_double_counted():
    """Подмена через обед: 07:45–13:00 = 4,5 ч, а не 5,25 ч."""
    machine = {"machine": "98", "operator": "Тестова",
               "operator_shift_end": "19:00",
               "lines": [{"assortment": "187х3", "counter_start": 1000,
                          "counter_end": 4000}],
               "events": [{"start": "07:45", "end": "13:00",
                           "kind": "подмена", "reason": "подмена шпулярника"}]}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    assert abs(r.subs_window_hours - 4.5) < 0.01, r.subs_window_hours


# ─────── 5. строка-«план» без счётчиков не ложится в таблицу ───────

def test_uncountered_plan_line_is_dropped_with_warning():
    machine = {"machine": "151", "operator": "Арташина",
               "operator_shift_end": "19:00",
               "lines": [
                   {"assortment": "днпэмн 1,2-16,5-151,5", "client": "КАСИМОВ",
                    "order_no": "291", "counter_start": 790570,
                    "counter_end": 790782},
                   {"assortment": "187х3-51-250,5", "order_no": "18.09.2026"},
               ],
               "events": []}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    assert len(r.lines) == 1, [ln.assortment for ln in r.lines]
    assert r.lines[0].counter_end == 790782
    assert any("убрана" in w for w in r.warnings), r.warnings


def test_all_uncountered_lines_stay():
    """Если счётчиков нет ни у одной строки — убирать нечего, карточки живы."""
    machine = {"machine": "121", "operator": None,
               "lines": [{"assortment": "днпа 187х3-36,0", "client": "Керимов"},
                         {"assortment": "днпа 187х3-25,0"}],
               "events": []}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    assert len(r.lines) == 2, [ln.assortment for ln in r.lines]


def test_zero_output_boundary_line_is_dropped():
    """Строка с ОДИНАКОВЫМИ показаниями — граница перехода, а не выпуск.

    Прогон 22.09.2026 при пересборке 24.09: ИИ положил к ст.151 вторую строку
    «187х3-51-250,5 … 790782 → 790782». Показания есть, поэтому старое правило
    («нет счётчиков — убрать») её пропустил, и в таблицу лёг ряд с 0 узлов и
    0 времени рядом с настоящей строкой на 212 узлов.
    """
    machine = {"machine": "151", "operator": "Арташина",
               "operator_shift_end": "19:00",
               "lines": [
                   {"assortment": "днпэмн 1,2-16,5-151,5", "client": "КАСИМОВ",
                    "counter_start": 790570, "counter_end": 790782},
                   {"assortment": "187х3-51-250,5, заправочный размер 52,5",
                    "order_no": "см-3 №151 от 18.09.2026",
                    "counter_start": 790782, "counter_end": 790782},
               ],
               "events": []}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    assert [ln.assortment for ln in r.lines] == ["днпэмн 1,2-16,5-151,5"], r.lines
    assert any("не сдвинулся" in w for w in r.warnings), r.warnings
    # Весь станок без движения — убирать нечего, строки остаются.
    still = {"machine": "121", "operator": None,
             "lines": [{"assortment": "днпа 187х3-36,0", "counter_start": 100,
                        "counter_end": 100},
                       {"assortment": "днпа 187х3-25,0", "counter_start": 200,
                        "counter_end": 200}],
             "events": []}
    r2 = build_shift_reports_from_structured({"machines": [still]})[0]
    assert len(r2.lines) == 2, [ln.assortment for ln in r2.lines]


def test_151_2209_table_has_single_row():
    rows = _table_rows("22.09.2026")
    assert len(rows["151"]) == 1, rows["151"]


# ─────── 6. «фио на подменах»: свои — да, посторонние — нет ───────

def test_foreman_order_is_not_a_submen():
    """Ст.251 22.09: распоряжение мастера — не подмена (в эталоне пусто)."""
    r = _r("22.09.2026", "251")
    assert "лола" not in _subs_fio(r).lower(), _subs_fio(r)
    rows = _table_rows("22.09.2026")
    assert "J" not in rows["251"][0], rows["251"][0]


def test_own_operator_who_did_submen_is_listed():
    """Как в ручном файле: оператор, менявшая шпули сама, стоит первой."""
    assert _subs_fio(_r("22.09.2026", "98")) == "Белякова"
    assert _subs_fio(_r("22.09.2026", "111")) == "Гудкова Сеньков Клинов"
    assert _subs_fio(_r("22.09.2026", "151")) == "Арташина Сеньков Клинов"
    # Стоявшая без оператора машина не «подменяет» саму себя.
    assert _subs_fio(_r("21.09.2026", "321")) == "Клинов"


def test_operator_absent_all_shift_is_never_on_submen():
    """Прогул не становится подменой, даже если ИИ принёс на станок чужое окно.

    Прогон 21.09.2026 (пересборка 24.09, 21:18): ИИ перенёс на стоявший всю
    смену 321 окно подмены из переписки по 151 («Клинов с 11:33 на подмене
    шпулярника №151») — и колонка «фио на подменах» показала «Демидов Клинов»
    при том, что Демидов отсутствовал 07:00–19:00. Признаком «своего» окна
    часы быть не могут: у отсутствующего оператора своё окно всегда нулевое.
    """
    machine = {"machine": "321", "operator": "Демидов",
               "operator_shift_end": "19:00",
               "lines": [{"assortment": "187х3", "counter_start": 1000,
                          "counter_end": 1000}],
               "events": [
                   {"start": "07:00", "end": "19:00", "kind": "отсутствие",
                    "reason": "прогул, машина стояла всю смену",
                    "performer": "Демидов"},
                   {"start": "07:43", "end": "11:33", "kind": "подмена",
                    "reason": "подмена шпуль (по данным 151 машины)",
                    "performer": "Клинов"},
               ]}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    assert r.operator_absent_all_shift, r.tech_stops
    assert r.subs_window_hours > 0.01, "окно подмены в тесте должно быть"
    assert _subs_fio(r) == "Клинов", _subs_fio(r)


# ─────── 7. подмена узнаётся по тексту причины, а не по метке модели ───────
#
# Три находки прогона 22.09.2026, из-за которых колонки «подмены, ч» и
# «фио на подменах» расходились с ручным файлом:

def test_naladchik_from_repair_is_not_a_submen():
    """Ст.111 22.09: «полотно вытянули, приподняв вал» — не подмена.

    Модель вернула это ТРЕМЯ событиями kind «подмена» (Пронкин, Сеньков,
    Клинов, 16:15–16:22). Из-за них наладчик Пронкин попадал в «фио на
    подменах», а часы подмен вырастали с эталонных 4,25 до 4,37.
    """
    assert not is_real_submen("полотно вытянули, приподняв вал")
    assert is_real_submen("подмена шпулей, 10:01 начинаю подмену")
    assert is_real_submen("замена шпулярника — 114 шт")
    r = _r("22.09.2026", "111")
    assert _subs_fio(r) == "Гудкова Сеньков Клинов", _subs_fio(r)
    assert abs(_subs_hours_cell(r) - 4.25) < 0.01, _subs_hours_cell(r)


def test_repair_outranks_submen_label():
    """Чинка полотна сильнее слова «подмена» в той же записи (правило 03.09).

    Ст.321 22.09: «чина чинка полотна по краю 3 полотна, машина в стопе; …
    5 подмена из 6» давала 1,68 ч в «подмены, ч»; в ручном файле пользователя
    эти часы стоят в «чинка» (у него чинка 5,95 ч).
    """
    reason = ("чина чинка полотна по краю 3 полотна, машина в стопе; 14:05 "
              "стоп чинка; 14:10 подготовка вала на 2 срез из 3, 5 подмена из 6")
    assert classify_stop(reason)[0] == "чинка/ремонт", classify_stop(reason)
    r = _r("22.09.2026", "321")
    q_hours, q_reasons = _stops_columns(r)
    assert "чинка/ремонт" in q_reasons, (q_hours, q_reasons)
    assert float(q_hours.split("+")[0].replace(",", ".")) >= 5.0, q_hours
    cell = _subs_hours_cell(r)
    assert cell == "" or cell <= 0.5, cell


def test_submen_counter_recap_is_not_an_operation():
    """«5 подмена из 6» в причине — сводка за смену, а не операция окна.

    Ст.321 22.09: «09:32 стоп машина, срез (1 срез из 3, 5 подмена из 6)»
    получала ярлык подмены. «Срез» при этом НЕ зачищаем: «довязан 2 срез из 3»
    (ст.251 за 21.09) — это сам срез.
    """
    assert (classify_stop("стоп машина, срез (1 срез из 3, 5 подмена из 6)")[0]
            == "срезы")
    assert (classify_stop("довязан 2 срез из 3, 1350 узл., общ.сч 298764")[0]
            == "срезы")
    assert classify_stop("стоп, срез узлов от подмены")[0] == "срезы"


def test_subs_hours_and_names_agree():
    """Часы в «подмены, ч» бывают только вместе с оператором в «фио».

    Разъезжались на ст.251 за 14.09.2026: колонка I показывала 1,13 ч, а J
    оставалась пустой — часы считались по окну простоя станка, а оператор
    допускался по другому признаку (r.subs_hours).

    Само это окно правило подменой не признало: мастер написал «замена катушек на
    шпулярнике», а не «подмена», и подменщика у машины не называл. С 29.09.2026
    такая строка не исчезает из бланка — её часы остаются в «Технических
    остановках» словами мастера, а клетки I и J остаются пустыми.
    """
    machine = {"machine": "251", "operator": "Арташина",
               "operator_shift_end": "19:00",
               "lines": [{"assortment": "днпа 187х9", "counter_start": 300000,
                          "counter_end": 300250}],
               "events": [{"start": "15:05", "end": "16:05",
                           "kind": "остановка",
                           "reason": "машина в стопе, замена катушек на "
                                     "шпулярнике №6 и №7"}]}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    assert _subs_hours_cell(r) == "", _subs_hours_cell(r)
    assert "Арташина" not in _subs_fio(r), _subs_fio(r)
    hours, reason = _stops_columns(r)
    assert hours and hours != "", (hours, reason)
    assert "шпулярник" in reason, reason


def test_parallel_work_at_stopped_machine_becomes_downtime():
    """Ст.111 за 22.09: «параллельная» навивка при стоящем станке = простой.

    Счётчик даёт 217649 → 217980 = 331 узел = 0,39 ч работы, а мастер описал
    07:00–09:44 «продолжаю навивку шпулей» и 15:17–18:45 «развивка шпулей».
    До правки эти часы оставались «параллельными» (станок ЯКОБЫ крутился), дыра
    в балансе была ~5 ч и колонка U кричала «РАСХОЖДЕНИЕ: станок −5 ч до 12 ч»,
    хотя часы в переписке названы. Продвигать берём ровно столько, сколько не
    объяснил ни один механизм, — поэтому недобор обязан закрыться, а перебора
    сверх смены появиться не должно.
    """
    r = _r("22.09.2026", "111")
    rows = {lab: h for lab, h, *_ in r.downtime_by_reason()}
    assert rows.get("навивка/намотка шпулей", 0) >= 2.5, rows
    assert r.unexplained_downtime <= 0.5, (r.unexplained_downtime, rows)
    assert r.machine_balance <= MACHINE_SHIFT_HOURS + 0.34, r.machine_balance


def test_promotion_never_pushes_out_the_named_residual():
    """Ст.98 за 10.09: остаток 0,4 ч уже подписан причиной — трогать нельзя.

    Работа 8,52 ч + названные простои 2,33 ч + обед 0,75 ч = 11,6 ч, и остаток
    0,4 ч named_residual_hours уже относит на причину длинного окна «замена
    шпулярника в течении всей смены». Дыры в балансе нет, поэтому продвижению
    «параллельных» окон брать там нечего. Первое наивное правило снимало 0,08 ч,
    именованный остаток съезжал ниже допуска и исчезал — отчёт показывал 11,69 ч
    вместо 12,0.
    """
    r = _r("10.09.2026", "98")
    rows = {lab: h for lab, h, *_ in r.downtime_by_reason()}
    residual = [h for lab, h in rows.items() if "замена шпулярника" in lab]
    assert residual and abs(residual[0] - 0.4) < 0.02, rows
    assert abs(r.machine_balance - MACHINE_SHIFT_HOURS) < 0.02, r.machine_balance


def test_doubled_downtime_hours_are_removed_and_named():
    """Ст.151 за 22.09: строк простоя больше, чем смена, быть не может.

    Мастер описал одни и те же минуты несколькими причинами: «ожидание»
    07:07–11:00 поверх «замены шпулярника» 07:45–13:00, наладка внутри того же
    окна. Раньше это только объяснялось: станок «не перерабатывал» сверх 12 ч
    в цифре баланса, а лист печатал 12,37 ч простоя за 12-часовую смену (в
    сборке дня через pipeline — все 14,78 ч) и человек не понимал, где часы
    задвоены. С 25.09.2026 (ст.111, «машина была в наладке и в это время
    производилась чинка») задвоение снимается с самих строк, а снятое
    называется каждой причиной.
    """
    r = _r("22.09.2026", "151")
    rows = r.downtime_by_reason()
    total = round(sum(h for _l, h, _a in rows), 2)
    assert total <= MACHINE_SHIFT_HOURS + TOLERANCE_H, (total, rows)
    assert abs(r.cross_label_deduped - 4.5) < 0.02, r.cross_label_dedup_detail
    assert r.machine_excess == 0.0, r.machine_excess
    # Обед вернулся в свой обычный размер: минуты перестали занимать twice.
    lunch = {lab: h for lab, h, *_ in rows if "обед" in lab}
    assert lunch == {"обед (станок стоит)": 0.75}, rows
    text = format_shift_report(r)
    assert "одни и те же минуты" in text, text
    assert "двумя разными причинами" in text, text
    for lab, _h in r.cross_label_dedup_detail:
        assert lab in text, (lab, text)          # названа каждая причина
    print(f"✅ 151 за 22.09: простой {total} ч, снято {r.cross_label_deduped} ч "
          f"({r.cross_label_dedup_detail})")


def test_excel_carries_the_same_note():
    """То же самое человек видит в Excel, а не только на странице."""
    notes = _machine_notes("22.09.2026")
    assert "двумя причинами" in notes.get("151", ""), notes
    assert "повтор снят со строк" in notes.get("151", ""), notes
    assert "двумя причинами" in notes.get("321", ""), notes
    assert notes.get("111", "") == "", notes       # у остального пояснения нет


def test_balance_note_keeps_the_20_minute_tolerance():
    """«В пределах допуска» — только про ±20 мин, а не про любую разницу.

    На ст.98 за 21.09 баланс 12,70 ч, и слайд печатал «расхождение 42 мин — в
    пределах допуска» при допуске ±20 мин: проверка на превышение сознательно
    не считает обед, а надпись про допуск считала. Теперь либо названа причина
    разницы, либо честное «вне допуска».
    """
    from types import SimpleNamespace
    nothing = SimpleNamespace(lunch_in_balance=0.0)
    assert "в пределах допуска ±20 мин" in _balance_gap_line(nothing, 12.25)
    assert "вне допуска" in _balance_gap_line(nothing, 12.75)
    lunch = SimpleNamespace(lunch_in_balance=0.75)
    line = _balance_gap_line(lunch, 12.7)
    assert "обед станка (45 мин)" in line and "переработка" in line, line
    assert "вне допуска" not in line, line


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"✅ {fn.__name__}")
    print(f"\nПроверено тестов: {len(fns)}.")
