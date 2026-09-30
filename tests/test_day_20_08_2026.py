#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регрессия на реальной смене 20.08.2026 — все шесть станков участка.

Источник — переписка мастеров из «текст 20.docx» (сохранена в
tests/day_20_08_2026_all.txt), раскладка по станкам — в
tests/day_20_08_2026_all.json (это то, что должен вернуть ИИ).

Тест сторожит именно те вещи, которые на этом дне ломались:
  • «подмена 90 шп.» не должна превращаться в уход на станок 90;
  • день, где станок всю смену стоял (251), должен сходиться в 12 ч,
    а не давать «недостаток»;
  • станок без конечного показания счётчика (151, 98) — «нет данных»,
    а не ложный недостаток 12 ч;
  • причины, написанные без времени (321), не должны молча исчезать.

Запуск: python tests/test_day_20_08_2026.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (build_shift_reports_from_structured,   # noqa: E402
                                   format_shift_report)

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = os.path.join(HERE, "day_20_08_2026_all.json")

failures = []


def check(name, got, expected):
    ok = got == expected
    print(("✅ " if ok else "❌ ") + f"{name}: {got!r}"
          + ("" if ok else f" (ожидалось {expected!r})"))
    if not ok:
        failures.append(name)


def check_true(name, cond, hint=""):
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f" — {hint}"))
    if not cond:
        failures.append(name)


def main():
    with open(DAY, encoding="utf-8") as f:
        data = json.load(f)
    reports = {r.machine: r for r in build_shift_reports_from_structured(data)}
    check("станки в отчёте", sorted(reports), ["111", "121", "151", "251",
                                              "321", "98"])

    print("\n=== 111: чинка всю смену, работа только с 17:20 ===")
    r = reports["111"]
    check("выпуск, узл.", r.output_total, 1718.0)        # 195368 − 193650
    check("работа станка, ч", r.work_hours, 1.88)        # 1718 / 15,2 / 60
    check("простой с причиной, ч", r.accounted_downtime, 10.12)
    check("недостаток по станку", r.machine_shortfall, 0.0)
    check("часы на другом станке (121)", r.operator_elsewhere_hours, 1.08)

    print("\n=== 121: станок работал, оператор успевала и подмену ===")
    r = reports["121"]
    check("выпуск, узл.", r.output_total, 7163.0)
    check("работа станка, ч", r.work_hours, 8.23)
    check("подмена, ч", r.subs_hours, 3.2)
    check("баланс станка, ч", r.machine_balance, 12.0)
    check("недостаток по станку", r.machine_shortfall, 0.0)
    check("недостаток у оператора", r.operator_shortfall, 0.0)

    print("\n=== 151 и 98: нет конечного счётчика — «нет данных» ===")
    for m in ("151", "98"):
        r = reports[m]
        check_true(f"{m}: баланс не считаем", bool(r.balance_unknown))
        check(f"{m}: недостаток по станку", r.machine_shortfall, 0.0)
        check(f"{m}: недостаток у оператора", r.operator_shortfall, 0.0)
        check_true(f"{m}: предупреждение про счётчик",
                   any("счётчик" in w for w in r.warnings))

    print("\n=== 251: станок всю смену стоял, но смена сходится ===")
    r = reports["251"]
    check("выпуск, узл.", r.output_total, 0.0)
    check("работа станка, ч", r.work_hours, 0.0)
    check("подмен за смену", r.subs_count, 3)
    check("срезов", r.cuts, 15)
    check("баланс станка, ч", r.machine_balance, 12.0)
    check("недостаток по станку", r.machine_shortfall, 0.0)
    check("недостаток у оператора", r.operator_shortfall, 0.0)
    labels = [lbl for lbl, _h, _a in r.downtime_by_reason()]
    # Навивка и замена шпулярника — операции оператора; станок в это время
    # не работал, поэтому часы обязаны объяснять простой (одной строкой),
    # а сами формулировки мастера видны в блоке параллельных операций.
    parallel = [lbl for lbl, _h in r.parallel_by_reason()]
    for need in ("наладка/переналадка", "протяжка узлов",
                 "подмена (заправка полотна)",
                 "операции оператора при стоящем станке"):
        check_true(f"причина простоя «{need}»", need in labels, str(labels))
    for need in ("навивка/намотка шпулей",
                 "замена шпулярника / переход на ассортимент"):
        check_true(f"операция оператора «{need}»", need in parallel,
                   str(parallel))
    check_true("«подмена 90 шп.» не стала станком 90",
               all("станке 90" not in lbl for lbl in labels), str(labels))
    check("уходов на другие станки нет", r.operator_elsewhere_hours, 0.0)

    print("\n=== 321: оператор весь день на других станках ===")
    r = reports["321"]
    check("выпуск, узл.", r.output_total, 270.0)          # 112511 − 112241
    check("работа станка, ч", r.work_hours, 0.75)         # 270 / 6 / 60
    check("часы на 251 и 121, ч", r.operator_elsewhere_hours, 6.54)
    check("станки, куда уходил", sorted(r.operator_elsewhere_machines),
          ["121", "251"])
    check("недостаток по станку, ч", r.machine_shortfall, 3.96)
    check_true("простои без времени сохранены", bool(r.untimed_stops))
    text = format_shift_report(r)
    check_true("в отчёте есть раздел «простои без времени»",
               "Простои без времени" in text)
    check_true("недостаток объяснён простоями без времени",
               "мастер написал без времени" in text, text[-400:])
    check_true("крючок/наладчик попали в отчёт словами мастера",
               "крючок не взял петлю" in text)

    print("\n=== Excel по всем шести станкам ===")
    try:
        from services.excel_export import export_shift_reports
    except ImportError as e:
        check_true("openpyxl установлен", False, str(e))
    else:
        path = os.path.join(tempfile.mkdtemp(prefix="day20_"), "day20.xlsx")
        export_shift_reports(list(reports.values()), path, date="20.08.2026")
        check_true("файл создан", os.path.getsize(path) > 5000)
        from openpyxl import load_workbook
        wb = load_workbook(path)
        check("листы", wb.sheetnames, ["Отчет за смену", "Простои по причинам",
                                      "Итоги смены"])
        stops = wb["Простои по причинам"]
        col_b = [stops.cell(row=i, column=2).value
                 for i in range(2, stops.max_row + 1)]
        check_true("в Excel есть строка «простой без времени»",
                   "простой без времени" in col_b, str(col_b))
        col_a = [str(stops.cell(row=i, column=1).value)
                 for i in range(2, stops.max_row + 1)]
        check_true("в Excel нет выдуманного станка 90",
                   all("90" != a for a in col_a), str(col_a))

    print("\n" + ("❌ ПРОВАЛЫ: " + ", ".join(failures) if failures
                  else "✅ Смена 20.08.2026 по всем станкам считается верно"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
