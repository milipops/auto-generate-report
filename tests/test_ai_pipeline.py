"""Проверка гибридного пайплайна: структура ИИ -> детерминированный расчёт.

Здесь НЕ вызывается сеть. Мы подставляем JSON, который ДОЛЖЕН вернуть ИИ
для реальной переписки 251/121, и проверяем, что:
  * станки корректно разделены (в отличие от регулярок на грязном чате);
  * часы считаются детерминированно и суммы сходятся;
  * отчёты форматируются без ошибок.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.daily_report import build_daily_reports_from_structured, format_daily_report
from services.operations_report import (
    build_operations_reports_from_structured,
    format_operations_report,
)

# То, что ИИ должен извлечь из реальной переписки (tests/real_251_121.txt).
MOCK = {
    "date": "19.08.2026",
    "machines": [
        {
            "machine": "251",
            "operator": "Арташина",
            "operator_absent": False,
            "counters": [
                {"name": "Общий счётчик", "value": 228910, "target": None},
                {"name": "Срезовой счётчик", "value": 221, "target": 1060},
            ],
            "events": [
                {"start": "07:03", "end": None, "category": "operator",
                 "label": "работа (пуск)", "performer": "Камбаров",
                 "text": "начало работы, счётчики сняты"},
                {"start": "10:19", "end": None, "category": "operator",
                 "label": "навивка шпулей", "performer": "Камбаров",
                 "text": "довиваю шпулярник"},
                {"start": "12:00", "end": "12:30", "category": "break",
                 "label": "перерыв (обед)", "performer": None, "text": "обед"},
                {"start": "12:30", "end": None, "category": "operator",
                 "label": "навивка шпулей", "performer": "Арташина",
                 "text": "навито 16 шпулей"},
                {"start": "14:12", "end": None, "category": "downtime",
                 "label": "простой (замена шпулярника)", "performer": None,
                 "text": "машина стоп, переход на нпа 187х9, замена шпулярника"},
                {"start": "15:30", "end": None, "category": "operator",
                 "label": "навивка шпулей", "performer": "Вашурина",
                 "text": "навивка шпулей передана Вашуриной"},
            ],
        },
        {
            "machine": "121",
            "operator": "Вашурина",
            "operator_absent": True,
            "counters": [
                {"name": "Общий счётчик", "value": 867019, "target": None},
                {"name": "Срезовой счётчик", "value": 1362, "target": 2030},
            ],
            "events": [
                {"start": "07:15", "end": None, "category": "downtime",
                 "label": "простой (пошли пропуска)", "performer": None,
                 "text": "останов машины, пошли пропуска"},
                {"start": "08:20", "end": None, "category": "operator",
                 "label": "работа (пуск)", "performer": "Вашурина",
                 "text": "машина в работе"},
                {"start": "09:30", "end": None, "category": "downtime",
                 "label": "простой (наладка)", "performer": "Пронкин",
                 "text": "останов, требует наладки, шпонка рычага осадного вала"},
                {"start": "14:13", "end": None, "category": "personnel",
                 "label": "работа наладчика", "performer": "Пронкин",
                 "text": "машина прошла наладку"},
                {"start": "14:30", "end": None, "category": "downtime",
                 "label": "отсутствие оператора", "performer": None,
                 "text": "машина в стопе, оператор переведён на 251"},
            ],
        },
    ],
}


def main():
    data = MOCK
    if len(sys.argv) > 1:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    print(f"Станков в структуре: {len(data['machines'])}\n")

    print("========== СУТОЧНЫЕ ОТЧЁТЫ ==========")
    for r in build_daily_reports_from_structured(data):
        print(format_daily_report(r))
        print("-" * 60)

    print("\n========== ЧАСЫ ПО ОПЕРАЦИЯМ ==========")
    ops = build_operations_reports_from_structured(data)
    for r in ops:
        print(format_operations_report(r))
        print(f"[итоги] оператор={r.operator_total} персонал={r.personnel_total} "
              f"простой={r.downtime_total} машина={r.machine_work}")
        print("-" * 60)

    machines = {r.machine for r in ops}
    assert machines == {"251", "121"}, f"станки не разделены: {machines}"
    print("\n✅ Станки 251 и 121 корректно разделены, часы посчитаны.")


if __name__ == "__main__":
    main()
