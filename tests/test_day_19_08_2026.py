"""Регрессия на реальном дне 19.08.2026 (станки 98, 321, 251, 151, 121, 111).

Проверяем три вещи:
  1. Числа по каждому станку не «уехали» после правок логики.
  2. Формулы Excel дают ТЕ ЖЕ числа, что и текстовый отчёт: пересчитываем
     SUM/SUMIFS сами, потому что LibreOffice в песочнице недоступна.
  3. Независимая сверка с сообщением мастера: станок 111 — «машина была
     в работе 28 минут», а формула по счётчику даёт 0,47 ч = 28 мин.

Запуск: python3 tests/test_day_19_08_2026.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook

from services.excel_export import (SHEET_MAIN, SHEET_STOPS, SHEET_TOTALS,
                                   UNEXPLAINED, export_shift_reports)
from services.shift_report import (LUNCH_FULL_H, MACHINE_SHIFT_HOURS,
                                   TOLERANCE_H,
                                   build_shift_reports_from_structured,
                                   format_shift_report)

DAY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "day_19_08_2026.json")

# Что должно получиться по каждому станку:
# (работа, простой с причиной, недостаток станка, время сотрудника,
#  норма сотрудника, недостаток сотрудника, часы на другом станке)
EXPECTED = {
    "98":  (6.25, 2.84, 2.91, 8.34, 11.25, 2.91, 0.0),
    "321": (0.17, 3.90, 7.93, 3.32, 11.25, 7.93, 0.0),
    "251": (4.02, 5.55, 2.43, 8.82, 11.25, 2.43, 0.0),
    "151": (0.00, 0.75, 0.00, 0.00, 11.25, 0.00, 0.0),   # баланс не считается
    "121": (1.74, 10.30, 0.00, 12.04, 11.25, 0.00, 4.5),
    "111": (0.48, 11.30, 0.00, 7.00, 7.75, 0.75, 3.0),
}


def close(a: float, b: float, eps: float = 0.02) -> bool:
    return abs(float(a) - float(b)) <= eps


def check_numbers(reports) -> None:
    assert len(reports) == 6, f"ожидалось 6 станков, получено {len(reports)}"
    for r in reports:
        exp = EXPECTED[str(r.machine)]
        got = (r.work_hours, r.accounted_downtime, r.machine_shortfall,
               r.operator_hours, r.operator_norm, r.operator_shortfall,
               r.operator_elsewhere_hours)
        for name, e, g in zip(("работа", "простой", "недостаток станка",
                               "сотрудник", "норма сотрудника",
                               "недостаток сотрудника", "другой станок"),
                              exp, got):
            assert close(e, g), (f"станок {r.machine}: {name} "
                                 f"ожидалось {e}, получено {g}")
    print("✅ Числа по всем шести станкам совпадают с эталоном.")

    by = {str(r.machine): r for r in reports}
    # Станки без данных не должны давать ложный недостаток.
    assert by["151"].balance_unknown, "станок 151 должен быть «нет данных»"
    assert by["151"].unexplained_downtime == 0, \
        "без показаний счётчика нельзя записывать 12 ч «без причины»"
    # Норма сотрудника — его смена минус обед 45 мин; часы на другом станке
    # из нормы НЕ вычитаются (для этого станка это отсутствие оператора).
    assert by["121"].operator_norm == 11.25, "норма 121 = 12 − 0,75"
    assert by["121"].operator_elsewhere_hours == 4.5, "121: 4,5 ч на другом"
    assert by["111"].operator_shift_span == 8.5, "смена Арташиной до 15:30"
    assert abs(by["111"].absence_after_shift - 3.5) < 0.01, \
        "после 15:30 станок стоит без оператора — 3,5 ч"
    assert by["111"].lunch_recorded, "обед 12:00–12:30 пришёл интервалом"
    assert by["111"].lunch_deducted == 0, "обед из часов сотрудника не вычитаем"
    assert abs(by["111"].parallel_extra - 0.95) < 0.01, \
        "навивка сверх работы станка обязана попасть и в простой, и в часы"
    print("✅ «Нет данных», короткая смена и часы на другом станке учтены.")

    # Независимая сверка: мастер написал «машина была в работе 28 минут».
    r111 = by["111"]
    hours = r111.lines[0].output / r111.rate / 60
    assert close(hours, 28 / 60, 0.01), (
        f"станок 111: по счётчику {hours:.2f} ч, "
        f"а мастер написал 28 минут")
    print("✅ Сверка с сообщением мастера: 432 узл. / 15,2 / 60 = 28 минут.")


def _sumifs(rows, value_idx, conds) -> float:
    total = 0.0
    for row in rows:
        if all(cond(row[i]) for i, cond in conds):
            total += float(row[value_idx] or 0)
    return round(total, 2)


def check_excel(reports) -> None:
    """Пересчитать формулы листа «Итоги смены» и сверить с отчётом."""
    path = os.path.join(tempfile.mkdtemp(), "otchet.xlsx")
    export_shift_reports(reports, path, date="19.08.2026")
    wb = load_workbook(path)
    assert wb.sheetnames == [SHEET_MAIN, SHEET_STOPS, SHEET_TOTALS]

    main = [[c.value for c in row] for row in
            wb[SHEET_MAIN].iter_rows(min_row=5, max_col=20)]
    stops = [[c.value for c in row] for row in
             wb[SHEET_STOPS].iter_rows(min_row=2, max_col=5)]

    for r in reports:
        m = str(r.machine)
        # Работа станка: O = (M − L) / T / 60 по всем строкам станка.
        rate = next((row[19] for row in main
                     if str(row[1]) == m and row[19]), None)
        work = 0.0
        for row in main:
            if str(row[1]) != m or row[11] is None or row[12] is None:
                continue
            work += (float(row[12]) - float(row[11])) / float(rate) / 60
        work = round(work, 2)
        assert close(work, r.work_hours), \
            f"станок {m}: Excel даёт работу {work}, отчёт {r.work_hours}"

        mine = [row for row in stops if str(row[0]) == m]
        not_unexplained = (1, lambda v: v != UNEXPLAINED)
        stop_h = _sumifs(mine, 3, [not_unexplained])
        op_h = _sumifs(mine, 3, [not_unexplained, (4, lambda v: v == "да")])
        elsewhere = _sumifs(mine, 3, [
            (1, lambda v: str(v).startswith("отсутствие оператора (ушёл"))])
        assert close(stop_h, r.accounted_downtime), \
            f"станок {m}: простой Excel {stop_h} ≠ {r.accounted_downtime}"
        # Операции оператора на листе простоев = его операции при остановке
        # плюс та часть параллельных работ, что не влезла в работу станка.
        assert close(op_h, r.operator_stop_hours + r.parallel_extra), \
            f"станок {m}: операции Excel {op_h} ≠ " \
            f"{r.operator_stop_hours + r.parallel_extra}"
        assert close(elsewhere, r.operator_elsewhere_hours), \
            f"станок {m}: другой станок Excel {elsewhere} ≠ " \
            f"{r.operator_elsewhere_hours}"

        balance = round(work + stop_h, 2)
        gap = round(MACHINE_SHIFT_HOURS - balance, 2)
        gap = gap if gap > TOLERANCE_H else 0.0
        # Часы сотрудника: работа станка + его операции + подмены на других
        # станках. Обед не вычитается — он в простое станка.
        operator = round(work + op_h + elsewhere, 2)
        norm = round(r.operator_shift_span - LUNCH_FULL_H, 2)
        op_gap = round(norm - operator, 2)
        op_gap = op_gap if op_gap > TOLERANCE_H else 0.0
        if r.balance_unknown:
            continue          # в этих клетках стоит «нет данных», не формула
        assert close(balance, r.machine_balance), f"станок {m}: баланс"
        assert close(gap, r.machine_shortfall), \
            f"станок {m}: недостаток станка Excel {gap} ≠ {r.machine_shortfall}"
        assert close(operator, r.operator_hours), \
            f"станок {m}: сотрудник Excel {operator} ≠ {r.operator_hours}"
        assert close(norm, r.operator_norm), f"станок {m}: норма сотрудника"
        assert close(op_gap, r.operator_shortfall), \
            f"станок {m}: недостаток сотрудника Excel {op_gap} ≠ " \
            f"{r.operator_shortfall}"
    print("✅ Формулы Excel дают те же числа, что и текстовый отчёт.")


def main() -> None:
    with open(DAY, encoding="utf-8") as fh:
        data = json.load(fh)
    reports = build_shift_reports_from_structured(data)
    if "-v" in sys.argv:
        for r in reports:
            print(format_shift_report(r))
            print("=" * 70)
    check_numbers(reports)
    check_excel(reports)
    print("\nДень 19.08.2026 проверен: 6 станков, текст и Excel сходятся.")


if __name__ == "__main__":
    main()
