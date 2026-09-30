# -*- coding: utf-8 -*-
"""Проверка выгрузки сменного отчёта в Excel (двухлистовый файл).

Лист «Пояснение Д.М» — блоки по станкам (кто сколько работал и почему стоял),
лист «Д.М» — таблица «Книга1» для переноса в 1С. Главное, что тут сверяется:
числа в файле = расчёт Python, а формулы пользователя (N=M−L, O=N/T/60) дают
столько же. Формул в файле нет намеренно: значение должно быть видно в любом
просмотрщике и при импорте, а не только после пересчёта в Excel.

Раньше этот тест проверял трёхлистовый бланк с формулами и SUMIFS — тот формат
заменён, а сверка «Excel = Python» осталась. Регрессии по колонкам таблицы
(тех. остановки, подмены, пустые счётчики у стоявших станков) — в
tests/test_shift_2209_fixes.py.

Запуск (нужен только openpyxl):
    py -3.11 tests/test_excel_export.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl                                          # noqa: E402

from services import machine_rates                               # noqa: E402
from services.shift_report import build_shift_reports_from_structured   # noqa: E402
from services.excel_export import export_shift_reports                  # noqa: E402

SAMPLE = {"date": "19.08.2026", "machines": [
    {"machine": "98", "operator": "Елистратова", "brand": "Амита-18",
     "operator_shift_end": "19:00", "subs_count": 2, "subs_fio": "Елистратова",
     "lines": [
         {"assortment": "днпа 187х3-51,0-250,5 зел", "client": "СКЛАД",
          "order_no": "39", "panels": 1,
          "counter_start": 21136950, "counter_end": 21140500},
         {"assortment": "днпа 187х3-50,0-250,5 зел", "client": "Микаилов",
          "order_no": "315", "panels": 1,
          "counter_start": 21140500, "counter_end": 21147250}],
     "events": [
         {"start": "09:55", "end": "10:45", "kind": "подмена",
          "reason": "подмена шпулей"},
         {"start": "10:45", "end": "11:10", "kind": "наладка",
          "reason": "перезаправка, наладка"},
         {"start": "17:15", "end": "18:05", "kind": "подмена",
          "reason": "подмена"}]},
    {"machine": "251", "operator": "Гаврилов", "operator_shift_end": "19:00",
     "lines": [{"assortment": "днпа 120х2", "client": "СКЛАД", "order_no": "77",
                "counter_start": 228910, "counter_end": 230945}],
     "events": [
         {"start": "08:00", "end": "09:30", "kind": "навивка",
          "reason": "машина стоп, навиваю шпули"},
         {"start": "12:00", "end": "13:00", "kind": "отсутствие",
          "reason": "нет оператора, ушёл"}]},
]}


def _export(reports, date=None):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "otchet.xlsx")
    export_shift_reports(reports, path, date)
    return path, tmp


def _table(ws):
    """Строки листа-таблицы: заголовок во 2-й строке, данные с 3-й."""
    return [{c.column_letter: c.value for c in row
             if c.value not in (None, "")}
            for row in ws.iter_rows(min_row=3, max_row=ws.max_row)
            if any(c.value not in (None, "") for c in row)]


def test_workbook_layout():
    """Два листа: пояснение по станкам + таблица «Книга1»."""
    reports = build_shift_reports_from_structured(SAMPLE)
    path, tmp = _export(reports)
    try:
        wb = openpyxl.load_workbook(path)
        assert wb.sheetnames == ["Пояснение 19.08", "19.08"], wb.sheetnames
        ws = wb["19.08"]
        assert ws["D1"].value == "Отчет производства за смену"
        assert ws["F1"].value == "19.08.2026"
        assert ws["A2"].value == "Фамилия"
        assert ws["I2"].value == "подмены, ч"
        assert ws["T2"].value == "Отходов в минуту (T)"
        assert ws["U2"].value.startswith("Расхождение")
        assert ws.column_dimensions["D"].width > 10
        exp = wb["Пояснение 19.08"]
        assert exp["A1"].value == "19.08.2026"
        assert exp["A3"].value == "№ машины" and exp["B3"].value == 98
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("✅ Листы и шапки бланка на своих местах.")


def test_table_values_match_python_and_user_formulas():
    """Числа в таблице = расчёт Python и = арифметика формул пользователя."""
    reports = build_shift_reports_from_structured(SAMPLE)
    path, tmp = _export(reports)
    try:
        ws = openpyxl.load_workbook(path)["19.08"]
        rows = _table(ws)
        r98, r251 = reports
        assert rows[0]["B"] == 98 and rows[1].get("B") is None
        assert rows[2]["B"] == 251
        # Шапка станка (марка, подмены, T, расхождение) — один раз, не на
        # каждой строке ассортимента.
        assert rows[0]["C"] == "Амита-18" and "C" not in rows[1]
        assert rows[0]["T"] == 18 and "T" not in rows[1]
        for row, ln in zip(rows[:2], r98.lines):
            rate = 18
            assert not str(row["N"]).startswith("="), row["N"]
            # Выпуск — разница счётчиков (формула пользователя N=M−L).
            assert row["N"] == round(ln.counter_end - ln.counter_start), row["N"]
            # Время — выпуск / норма / 60 (формула O=N/T/60), в допуске округления.
            assert abs(row["O"] - row["N"] / rate / 60) < 0.01, row["O"]
        # Строка №251: те же 2035 узлов, но время по норме из колонки T этого
        # же листа. Норму №251 согласовали 7,4 (было 7,35), поэтому проверяем
        # арифметику бланка, а не заученные 4,61 ч.
        assert rows[2]["N"] == 2035
        assert rows[2]["T"] == machine_rates.get_rate("251"), rows[2]["T"]
        assert abs(rows[2]["O"] - rows[2]["N"] / rows[2]["T"] / 60) < 0.01, \
            rows[2]["O"]
        # Время по строке == время станка из расчёта Python.
        assert abs(sum(r["O"] for r in rows[:2]) - r98.work_hours) < 0.02
        assert rows[0]["L"] == 21136950 and rows[0]["M"] == 21140500
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("✅ Выпуск и время в таблице совпадают с расчётом и формулами бланка.")


def test_table_columns_are_plain_values():
    """В файле нет формул: значение видно без пересчёта (импорт в 1С, просмотрщик)."""
    reports = build_shift_reports_from_structured(SAMPLE)
    path, tmp = _export(reports)
    try:
        wb = openpyxl.load_workbook(path)
        for ws in wb:
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str):
                        assert not c.value.startswith("="), (ws.title,
                                                             c.coordinate,
                                                             c.value)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("✅ Формул в файле нет — только посчитанные значения.")


def test_machine_block_hours_match_python():
    """Блок станка: работа оператора/машины и строки простоев = расчёт."""
    reports = build_shift_reports_from_structured(SAMPLE)
    path, tmp = _export(reports)
    try:
        ws = openpyxl.load_workbook(path)["Пояснение 19.08"]
        # Строки блоков идут по порядку отчётов; у последнего станка блока
        # нет — только «нет данных».
        heads = [i for i in range(1, ws.max_row + 1)
                 if ws[f"A{i}"].value == "№ машины"]
        assert len(heads) == len(machine_rates.known_machines()), heads
        # 2 рабочих станка + остальные станки парка нулевыми строками
        assert ws[f"B{heads[0]}"].value == 98
        assert ws[f"B{heads[1]}"].value == 251
        r98 = reports[0]
        # 98 — первый блок: оператор 11,25 из 11,25 и машина 9,54 из 12.
        op_row, mach_row = heads[0] + 1, heads[0] + 6
        assert ws[f"A{op_row}"].value == "Работа оператора всего"
        assert ws[f"C{op_row}"].value == round(r98.operator_hours, 2)
        assert ws[f"D{op_row}"].value == "из 11,25 часа"
        assert ws[f"A{mach_row}"].value == "Работа машины всего"
        assert ws[f"C{mach_row}"].value == round(r98.work_hours, 2)
        assert ws[f"D{mach_row}"].value == "из 12 часов"
        # Простои расшифрованы по причинам; суммарно — учтённый простой.
        stop_rows = [(ws[f"A{i}"].value, str(ws[f"B{i}"].value))
                     for i in range(mach_row + 2, heads[1])]
        assert abs(sum(h for h, _ in stop_rows
                       if isinstance(h, (int, float)))
                   - r98.accounted_downtime) < 0.02, stop_rows
        reasons = " | ".join(t for _h, t in stop_rows)
        assert "подмена" in reasons and "обед" in reasons
        assert "наладка" in reasons
        # Станок без данных обязан остаться с пометкой, а не с выдуманными часами.
        no_data = heads[2]
        assert ws[f"E{no_data + 1}"].value == "нет данных / станок не работал"
        assert ws[f"C{no_data + 2}"].value == 0
        assert "нет данных" in str(ws[f"E{no_data + 2}"].value)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("✅ Блоки пояснения совпадают с расчётом, «нет данных» честно.")


if __name__ == "__main__":
    test_workbook_layout()
    test_table_values_match_python_and_user_formulas()
    test_table_columns_are_plain_values()
    test_machine_block_hours_match_python()
    print("\nВыгрузка в Excel проверена.")
