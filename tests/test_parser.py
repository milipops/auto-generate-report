"""Проверка производственного парсера на реальных примерах сообщений."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.production_parser import parse_production_message  # noqa: E402


SAMPLES = [
    """–98 станок
7ч.начало работы Общ.счетчик-21125950
С 9-10 до 10-00 подмена
оператор Елистратова
С 15-50 до 16-40 подмена""",

    """– 151
08:57- 7 срез 1400 узлов. Об. сч. 710704
09:00-10:00- Подмена. Абросимов-101 шт.;Арташина-100шт
11:00-Остановка машины
12:00- Запуск машины
!!!! Нет данных по окончании смены!!!!""",

    """– 251
08;46- начало работы Об.сч.: 865637
Ср. сч.:1362 из 2030
Завиты все шпули 502 шт.
Привязано 1261 узл.
Конец смены 20:00""",

    """– 121
18: 20- начало Общий счетчик 445120
Срезовой счетчик 34 из 304
подмена Иванова
пропуск данных по срезам""",

    """– 111
Остановка машины 14:00
Запуск машины 14:45
шпуля навито 3 из 3
отсутствует информация по счетчику""",
]


def _print_record(index, text):
    record = parse_production_message(text)
    print(f"\n===== Пример {index} =====")
    print(f"Станок:       {record.machine}")
    print(f"Начало смены: {record.shift_start}")
    print(f"Конец смены:  {record.shift_end}")
    print(f"Оператор:     {record.operator}")
    print(f"Общие счетчики:  {record.total_counters}")
    print(f"Срезовые:        {record.slice_counters}")
    print(f"Счетчики(дроби/значения):")
    for counter in record.counters:
        print(f"    {counter}")
    print(f"Подмены:")
    for sub in record.substitutions:
        print(f"    {sub}")
    print(f"Событий:      {len(record.events)}")
    for event in record.events:
        print(f"    [{event.category}] {event.start}-{event.end}: {event.text}")
    print(f"Предупреждения: {record.warnings}")
    return record


def main():
    total = 0
    for index, text in enumerate(SAMPLES, 1):
        record = _print_record(index, text)
        total += 1
        assert record.raw_text, "raw_text должен сохраняться"
    print(f"\nВсего проверено примеров: {total}")


if __name__ == "__main__":
    main()
