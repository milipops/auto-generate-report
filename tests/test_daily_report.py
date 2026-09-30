"""Проверка суточной агрегации на текстовом файле (без Yandex API).

Запуск:
    python tests/test_daily_report.py
    python tests/test_daily_report.py path/to/your_day.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.daily_report import build_daily_reports, format_daily_report

DEFAULT_FILE = os.path.join(os.path.dirname(__file__), "sample_day.txt")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    reports = build_daily_reports(text)
    print(f"Файл: {path}")
    print(f"Найдено станков: {len(reports)}\n")
    for report in reports:
        print("=" * 60)
        print(format_daily_report(report))
        print()

    # Простейшие проверки на примере станка 251
    by_machine = {r.machine: r for r in reports}
    if "251" in by_machine:
        r = by_machine["251"]
        assert r.general_counter == 228910, r.general_counter
        assert r.slice_counter == (221, 1060), r.slice_counter
        assert r.date == "19.08.2026", r.date
        assert any(e.category == "перерыв" for e in r.events), "нет обеда"
        assert any(e.start == "12:00" for e in r.events), "нет 12:00"
        print("✅ Проверки для станка 251 пройдены.")


if __name__ == "__main__":
    main()
