"""Проверка подсчёта часов по операциям на текстовом файле (без Yandex API).

Запуск:
    python tests/test_operations_report.py
    python tests/test_operations_report.py path/to/day.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.operations_report import (
    build_operations_reports,
    format_operations_report,
)

DEFAULT_FILE = os.path.join(os.path.dirname(__file__), "sample_hours.txt")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    reports = build_operations_reports(text)
    print(f"Файл: {path}")
    print(f"Найдено станков: {len(reports)}\n")
    for r in reports:
        print("=" * 60)
        print(format_operations_report(r))
        print()
        print(f"[итоги] оператор={r.operator_total} персонал={r.personnel_total} "
              f"простой={r.downtime_total} машина={r.machine_work}")
        print()

    by_machine = {r.machine: r for r in reports}
    if "98" in by_machine:
        r = by_machine["98"]
        # машина работала = 12 - простой(2 ч) = 10 ч
        assert r.downtime_total == 2.0, r.downtime_total
        assert r.machine_work == 10.0, r.machine_work
        assert r.operator_total > 0, r.operator_total
        print("✅ Проверки для станка 98 пройдены.")


if __name__ == "__main__":
    main()
