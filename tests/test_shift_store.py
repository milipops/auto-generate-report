"""Проверка накопителя смены: «кидаю данные → принято» → «отчёт за день».

Главный сценарий пользователя: сообщения приходят по одному весь день
(текст, фото, текст с фото), бот их только складывает и отвечает «принято»,
а один запрос к ИИ делается позже — по всей накопленной переписке сразу.
Здесь проверяется всё, что делается БЕЗ ИИ: определение даты смены, быстрый
разбор «что я увидел», накопление, склейка текста для ИИ и сброс.

Запуск: python tests/test_shift_store.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import shift_store                                   # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "day_20_08_2026.txt")

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


def test_detect_date():
    print("\n=== Дата смены из текста ===")
    now = datetime(2026, 8, 20, 17, 50)
    check("заголовок «20 августа 2026 г.»",
          shift_store.detect_shift_date("20 августа 2026 г.\n√251", now),
          "20.08.2026")
    check("«19.08.2026» в тексте",
          shift_store.detect_shift_date("Смена 19.08.2026, станок 121", now),
          "19.08.2026")
    check("короткий год «20.08.26»",
          shift_store.detect_shift_date("20.08.26 машина 98", now),
          "20.08.2026")
    check("без даты — сегодняшняя смена",
          shift_store.detect_shift_date("Общий счётчик 230695", now),
          "20.08.2026")
    check("ночное сообщение — прошедшая смена",
          shift_store.detect_shift_date("конец смены",
                                        datetime(2026, 8, 21, 1, 10)),
          "20.08.2026")
    # Время и счётчики не должны становиться датой.
    check("«15:19» — это время, не дата",
          shift_store.detect_shift_date("15:19 Подмену закончил", now),
          "20.08.2026")
    # Посторонние даты в теле («задание от 05.08.26», «остановлена до
    # 27.08.2026») НЕ должны перебивать заголовок смены. Берём самую частую
    # дату-заголовок (по одному на станок), а не первую попавшуюся.
    real = ("151\n26 августа 2026 г.\nсогласно заданию от 05.08.26\n"
            "251\n26 августа 2026 г.\nостановлена до 27.08.2026\n"
            "98\n26 августа 2026 г.")
    check("заголовок смены важнее посторонних дат в тексте",
          shift_store.detect_shift_date(real, datetime(2026, 8, 27, 10, 0)),
          "26.08.2026")
    # «машине»/«марка» не должны превращаться в май/март.
    check("«на 321 машине» — не дата (май)",
          shift_store.detect_shift_date("работа на 21 машине, счётчик 720534",
                                        now),
          "20.08.2026")


def test_scan():
    print("\n=== Быстрый разбор без ИИ ===")
    s = shift_store.scan_text("15:20 Запускайте 321")
    check("станок из «Запускайте 321»", s.machines, ["321"])
    check("метки времени", s.times, 1)

    s = shift_store.scan_text("√251\nОбщий счётчик - 230695\n"
                              "Срезовой счётчик 945из1060")
    check("станок из «√251»", s.machines, ["251"])
    check("показания счётчика", s.counters, 1)

    s = shift_store.scan_text("15:19 Подмену закончил(115 шпулей)\n"
                              "303 из 303 шпулей\nУстановлено 237 из 300")
    check("количества не считаются станками", s.machines, [])

    s = shift_store.scan_text("15:39 Камбаров 90шт Сотволдиев98шт "
                              "Олег Петровский115шт")
    check("«Сотволдиев98шт» — не станок 98", s.machines, [])

    s = shift_store.scan_text("Перешли на 121,251")
    check("список «121,251»", s.machines, ["121", "251"])


def test_accumulate():
    print("\n=== Накопление смены и отчёт по накопленному ===")
    tmp = tempfile.mkdtemp(prefix="shifts_")
    shift_store.set_data_dir(tmp)
    try:
        with open(FIXTURE, encoding="utf-8") as f:
            chunks = [c.strip() for c in f.read().split("===") if c.strip()]
        check_true("фикстура разбита на сообщения", len(chunks) >= 8,
                   f"получилось {len(chunks)}")

        now = datetime(2026, 8, 20, 18, 0)
        accepted = None
        for chunk in chunks:
            accepted = shift_store.add_message(text=chunk, author="мастер",
                                               now=now)
        check("дата смены определена по заголовку", accepted.date, "20.08.2026")
        check("накоплено сообщений", accepted.messages, len(chunks))

        # Сообщение только с фотографией — тоже принимается.
        accepted = shift_store.add_message(
            text="", photos=[b"\xff\xd8\xff\xd9"], author="Камбаров", now=now)
        check("фото учтено", accepted.photos, 1)
        check("фото попало в ту же смену", accepted.date, "20.08.2026")

        msgs, photos = shift_store.day_stats("20.08.2026")
        check("итог по смене", (msgs, photos), (len(chunks) + 1, 1))
        check("смена для кнопки «Отчёт за день»", shift_store.latest_day(),
              "20.08.2026")
        # В реальной переписке за 20.08 номер основной машины не назван ни
        # разу — упомянут только 321. Это не ошибка разбора, а повод
        # предупредить пользователя перед отчётом.
        check("станки в накопленном", shift_store.day_machines("20.08.2026"),
              ["321"])

        text = shift_store.day_text("20.08.2026")
        check_true("в тексте для ИИ есть дата и смена",
                   "20.08.2026" in text and "07:00" in text)
        check_true("сообщения не потерялись",
                   "230695" in text and "Запускайте 321" in text
                   and "65 катушек" in text)
        check_true("есть пометка о фотографии", "приложено фото: 1" in text)
        check_true("время получения сообщений не уходит в ИИ",
                   "received_at" not in text and "T18:00:00" not in text,
                   "модель может принять его за время события")

        images = shift_store.day_photos("20.08.2026")
        # Здесь картинка заведомо нечитаемая (4 байта), OCR текст с неё снять
        # не может — значит фото уходит в ИИ картинкой, чтобы не потерять
        # данные. Обычное фото с текстом идёт в ИИ текстом: tests/test_photo_ocr.py.
        check("нечитаемое фото отдаётся в ИИ", len(images), 1)
        check("media_type фото", images[0][0], "image/jpeg")

        # Перезапуск бота не должен терять накопленное.
        reread = shift_store.load_day("20.08.2026")
        check("данные читаются с диска", len(reread["messages"]),
              len(chunks) + 1)

        removed = shift_store.clear_day("20.08.2026")
        check("сброс смены", removed, len(chunks) + 1)
        check("после сброса пусто", shift_store.day_stats("20.08.2026"), (0, 0))
        check("после сброса нет смен", shift_store.latest_day(), None)
    finally:
        shift_store.set_data_dir(shift_store._DEFAULT_DIR)
        shutil.rmtree(tmp, ignore_errors=True)


def test_two_days():
    print("\n=== Две смены не смешиваются ===")
    tmp = tempfile.mkdtemp(prefix="shifts_")
    shift_store.set_data_dir(tmp)
    try:
        shift_store.add_message(text="19.08.2026 станок 98 Об.сч. 21136950",
                                now=datetime(2026, 8, 19, 8, 0))
        shift_store.add_message(text="20 августа 2026 г. √251 счётчик 230695",
                                now=datetime(2026, 8, 20, 8, 0))
        days = [d[0] for d in shift_store.list_days()]
        check("обе смены на диске", days, ["19.08.2026", "20.08.2026"])
        check("в отчёт пойдёт последняя", shift_store.latest_day(),
              "20.08.2026")
        check_true("данные 19-го не попали в 20-е",
                   "21136950" not in shift_store.day_text("20.08.2026"))
    finally:
        shift_store.set_data_dir(shift_store._DEFAULT_DIR)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_detect_date()
    test_scan()
    test_accumulate()
    test_two_days()
    print("\n" + ("❌ ПРОВАЛЫ: " + ", ".join(failures) if failures
                  else "✅ Накопитель смены работает как задумано"))
    sys.exit(1 if failures else 0)
