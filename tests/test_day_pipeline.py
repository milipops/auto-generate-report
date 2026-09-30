"""Проверка ядра «выбрал дату → отчёт за эту смену» (services/day_pipeline.py).

Пользователь просил кнопку «загрузить данные за такое-то число»: данные из
чата копятся сами, а отчёт собирается по выбранной дате. Здесь проверяется
всё, что происходит без Telegram:

* какие смены накопились и в каком порядке их показывать кнопками;
* дата из кнопки/команды приводится к «ДД.ММ.ГГГГ», мусор отсекается;
* отчёт собирается ИМЕННО за выбранную смену — данные соседнего дня в запрос
  к ИИ не попадают (это главная опасность выбора даты);
* один вызов ИИ на отчёт;
* если ИИ выключен — не молчим, а отдаём упрощённую сводку.

Сети и pip-зависимостей здесь нет: ИИ подменён фикстурой.

Запуск: python tests/test_day_pipeline.py
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from services import day_pipeline, shift_store                       # noqa: E402

failures = []


def check(name, got, expected):
    ok = got == expected
    print(f"{'✅' if ok else '❌'} {name}: {got!r}"
          + ("" if ok else f" (ожидалось {expected!r})"))
    if not ok:
        failures.append(name)


def check_true(name, cond, hint=""):
    print(f"{'✅' if cond else '❌'} {name}" + ("" if cond else f" — {hint}"))
    if not cond:
        failures.append(name)


def test_normalize():
    print("\n=== Дата из кнопки и из команды ===")
    check("«20.08.2026»", day_pipeline.normalize_date("20.08.2026"),
          "20.08.2026")
    check("короткий год «20.8.26»", day_pipeline.normalize_date("20.8.26"),
          "20.08.2026")
    check("через слэш", day_pipeline.normalize_date("19/08/2026"),
          "19.08.2026")
    check("с пробелами", day_pipeline.normalize_date("  19.08.2026 "),
          "19.08.2026")
    check("не дата", day_pipeline.normalize_date("вчера"), None)
    # 32-го числа не бывает: иначе каталогом смены станет мусор из сообщения.
    check("несуществующая дата", day_pipeline.normalize_date("32.01.2026"),
          None)
    check("пусто", day_pipeline.normalize_date(""), None)


def test_days_and_report():
    print("\n=== Накопленные смены и отчёт за выбранную ===")
    tmp = tempfile.mkdtemp(prefix="pipeline_")
    shift_store.set_data_dir(tmp)
    try:
        shift_store.add_message(text="19.08.2026 станок 98 Об.сч. 21136950",
                                author="мастер",
                                now=datetime(2026, 8, 19, 8, 0))
        with open(os.path.join(HERE, "day_20_08_2026.txt"),
                  encoding="utf-8") as f:
            chunks = [c.strip() for c in f.read().split("===") if c.strip()]
        for chunk in chunks:
            shift_store.add_message(text=chunk, author="мастер",
                                    now=datetime(2026, 8, 20, 18, 0))

        days = day_pipeline.available_days()
        check("смены для кнопок: свежая сверху", [d.date for d in days],
              ["20.08.2026", "19.08.2026"])
        check("подпись на кнопке", days[1].button_label, "19.08 · 1 сообщ.")
        check_true("в строке списка видно объём данных",
                   "сообщ." in days[0].line and "фото" in days[0].line,
                   days[0].line)
        check("станки попали в подпись смены", days[1].machines, ["98"])

        check("дата по умолчанию — последняя смена",
              day_pipeline.resolve_date(None), "20.08.2026")
        check("дата из команды", day_pipeline.resolve_date("19/08/2026"),
              "19.08.2026")

        with open(os.path.join(HERE, "day_20_08_2026.json"),
                  encoding="utf-8") as f:
            structured = json.load(f)

        calls = []

        def fake_ai(text, images=None):
            calls.append((text, images or []))
            return json.loads(json.dumps(structured))

        result = day_pipeline.build_day("20.08.2026", extract=fake_ai,
                                        ai_enabled=True)
        check("вызовов ИИ на отчёт", len(calls), 1)
        check("отчёт собран", result.ok, True)
        check("дата отчёта", result.date, "20.08.2026")
        check("сообщений в работе", result.messages, len(chunks))
        ai_text = calls[0][0]
        check_true("в запрос ушла переписка выбранной смены",
                   "230695" in ai_text and "Запускайте 321" in ai_text)
        # Главное в выборе даты: соседний день не должен подмешаться.
        check_true("данные другой смены в запрос не попали",
                   "21136950" not in ai_text,
                   "в отчёт за 20.08 просочились данные 19.08")

        print("\n=== Смена, выбранная кнопкой, а не последняя ===")
        calls.clear()
        result19 = day_pipeline.build_day("19.08.2026", extract=fake_ai,
                                          ai_enabled=True)
        check("отчёт собран за 19-е", result19.date, "19.08.2026")
        check_true("в запрос ушла переписка 19-го",
                   "21136950" in calls[0][0]
                   and "Запускайте 321" not in calls[0][0])
        check("имя файла Excel", day_pipeline.xlsx_filename(result19.date),
              "Отчет производства за смену 19.08.2026.xlsx")

        print("\n=== Пустая дата и выключенный ИИ ===")
        empty = day_pipeline.build_day("01.01.2020", extract=fake_ai,
                                       ai_enabled=True)
        check("за дату без данных — пусто", empty.empty, True)
        check("отчёта нет", empty.ok, False)

        calls.clear()
        off = day_pipeline.build_day("20.08.2026", extract=fake_ai,
                                     ai_enabled=False)
        check("ИИ выключен — запросов нет", len(calls), 0)
        check_true("сказали, почему нет полного отчёта",
                   "AI_ENABLED" in off.note, off.note)
        check_true("упрощённая сводка всё равно есть", bool(off.simple),
                   "смена осталась совсем без ответа")
    finally:
        shift_store.set_data_dir(shift_store._DEFAULT_DIR)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_normalize()
    test_days_and_report()
    print("\n" + ("❌ ПРОВАЛЫ: " + ", ".join(failures) if failures
                  else "✅ Ядро «дата → отчёт» работает: смены копятся, отчёт "
                       "собирается за выбранное число"))
    sys.exit(1 if failures else 0)
