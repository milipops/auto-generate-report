#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка приёма переписки из Яндекс Мессенджера — БЕЗ доступа к API.

Смысл теста: болванка `services/yandex_source.py` должна быть готова к
подключению заранее, а не «когда-нибудь потом». Поэтому здесь прогоняется
весь путь на сохранённом JSON (tests/yandex_updates_sample.json):

    ответ API → normalize_updates → заголовки обсуждений → накопитель смены

Проверяется то, что сломается в первую очередь при подключении реального API:

* имена полей разные (text / body / message_text, автор строкой и объектом) —
  разбор обязан их переварить;
* станок и дата берутся из ЗАГОЛОВКА обсуждения, а не из текста рабочего;
* ответ рабочего со словами «данные по 121 машине» НЕ должен сойти за
  заголовок и потеряться;
* повтор сообщения при следующем опросе не должен задваивать смену;
* станок из заголовка, которого нет в справочнике норм (91), — предупреждение,
  а не молчание;
* ответ в неизвестную ветку не теряется, но помечается предупреждением;
* обращения к сети в тесте нет: `_api_get` обязан честно сказать «не
  подключено», а не молча вернуть пустоту.

Запуск: python tests/test_yandex_source.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from services import shift_store, yandex_source                      # noqa: E402

SAMPLE = os.path.join(HERE, "yandex_updates_sample.json")
NOW = datetime(2026, 8, 20, 19, 5, 0)

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
    print("=== 1. Заголовок обсуждения → станок и дата ===")
    check("«121 машина информация за смену 20.08.2026»",
          yandex_source.parse_thread_header(
              "121 машина информация за смену 20.08.2026", NOW),
          ("121", "20.08.2026"))
    check("«машина №14тр данные за 19.08.2026»",
          yandex_source.parse_thread_header(
              "машина №14тр данные за 19.08.2026", NOW),
          ("14тр", "19.08.2026"))
    check("заголовок без даты — текущая смена",
          yandex_source.parse_thread_header("251 машина информация за смену",
                                            NOW),
          ("251", "20.08.2026"))
    check_true("строка рабочего заголовком не считается",
               yandex_source.parse_thread_header(
                   "07:00 начало работы Об.сч.: 1096327", NOW) is None)
    check_true("«подмена на 121 м-148 шп.» не заголовок",
               yandex_source.parse_thread_header(
                   "12:30-13:35 подмена на 121 м-148 шп.", NOW) is None)

    print("\n=== 2. Разбор ответа API с разными именами полей ===")
    import json
    with open(SAMPLE, encoding="utf-8") as f:
        payload = json.load(f)
    msgs = yandex_source.normalize_updates(payload)
    check("сообщений в ответе", len(msgs), 10)
    by_id = {}
    for m in msgs:
        by_id.setdefault(m.message_id, m)
    check("автор объектом {display_name}", by_id["m100"].author, "Черныш Юлия")
    check("автор строкой", by_id["m101"].author, "Гудкова")
    check("текст в поле body",
          by_id["m101"].text.startswith("07:00 начало работы"), True)
    check("текст вложенным объектом", by_id["m102"].text, "12:00-12:45 обед")
    check("ссылка на ветку через reply_to_message_id",
          by_id["m102"].thread_id, "m100")
    check("вложение найдено", by_id["m102"].file_ids, ["f-77"])

    print("\n=== 3. Импорт в накопитель смены ===")
    tmp = tempfile.mkdtemp(prefix="yandex_")
    shift_store.set_data_dir(os.path.join(tmp, "shifts"))
    try:
        result = yandex_source.import_messages(msgs, now=NOW)
        check("заголовков найдено", result.headers, 3)
        # 10 сообщений: 3 заголовка (в накопитель не идут) + 1 повтор + 6 данных.
        check("принято сообщений", result.imported, 6)
        check("пропущено повторов", result.skipped_dupes, 1)
        check("смена одна", sorted(result.days), ["20.08.2026"])
        check("сообщений в смене", result.days["20.08.2026"], 6)

        day = shift_store.load_day("20.08.2026")["messages"]
        check("в накопителе столько же", len(day), 6)
        check_true("заголовок в накопитель не попал",
                   all("информация за смену" not in m["text"] for m in day),
                   str([m["text"][:40] for m in day]))
        check_true("ответ «данные по 121 машине» сохранён как данные",
                   any("18:50 конец смены" in m["text"] for m in day),
                   "сообщение приняли за заголовок и выбросили")
        machines = {m["text"][:20]: m["machine"] for m in day}
        check("станок проставлен по ветке 121",
              [m["machine"] for m in day
               if m["text"].startswith("07:00 начало работы Об.сч.: 1096327")],
              ["121"])
        check("станок проставлен по ветке 251",
              [m["machine"] for m in day
               if m["text"].startswith("08:20-10:15")], ["251"])
        check_true("ответ в неизвестную ветку сохранён без станка",
                   any(m["machine"] is None and "наладчика" in m["text"]
                       for m in day), str(machines))
        check("станки смены (сначала точные, из заголовков)",
              shift_store.day_machines("20.08.2026")[:3], ["121", "251", "91"])

        print("\n=== 4. Предупреждения вместо молчания ===")
        text = "\n".join(result.warnings)
        check_true("станок 91 не из справочника норм",
                   "91" in text and "machine_rates.json" in text, text)
        check_true("неизвестная ветка m999", "m999" in text, text)
        check_true("вложение не скачано (заглушка)",
                   "f-77" in text and "не скачано" in text, text)

        print("\n=== 5. Повторный опрос не задваивает смену ===")
        again = yandex_source.import_messages(
            yandex_source.normalize_updates(payload), now=NOW)
        check("принято при повторе", again.imported, 0)
        check("все узнаны как повторы", again.skipped_dupes, 7)
        check("в накопителе по-прежнему", len(
            shift_store.load_day("20.08.2026")["messages"]), 6)

        print("\n=== 6. Ветка помнится между опросами ===")
        state = yandex_source.load_state()
        check("карта обсуждений сохранена",
              state["threads"].get("m100"),
              {"machine": "121", "date": "20.08.2026"})
        # Ответ пришёл позже, заголовка в этой порции нет — станок обязан
        # найтись в сохранённом состоянии.
        late = yandex_source.normalize_updates([
            {"id": "m104", "thread_id": "m100", "from": "Гудкова",
             "text": "19:00 конец смены"}])
        res = yandex_source.import_messages(late, now=NOW)
        check("позднее сообщение принято", res.imported, 1)
        check("станок взят из сохранённой карты",
              [m["machine"] for m in shift_store.load_day("20.08.2026")
               ["messages"] if m["text"] == "19:00 конец смены"], ["121"])

        print("\n=== 7. Прогон отчёта по накопленному (без ИИ) ===")
        # Проверяем только то, что переписка легла в тот же формат, из
        # которого строится обычный отчёт: текст для ИИ собирается и станок
        # из заголовка в нём виден.
        prompt = shift_store.day_text("20.08.2026")
        check_true("в промпте есть пометка станка из заголовка",
                   "номер из заголовка темы" in prompt, prompt[:200])
        check_true("в промпте есть данные рабочих",
                   "1096327" in prompt and "навиваю шпули" in prompt)
        check_true("время получения сообщений в промпт не попало",
                   "received_at" not in prompt)

        print("\n=== 8. Сеть не трогаем: заглушки честно ругаются ===")
        try:
            yandex_source._api_get("/messages")
            check_true("_api_get должен бросать исключение", False)
        except yandex_source.YandexSourceError as e:
            # Три законных причины отказа, и все три — понятным текстом, а не
            # трассировкой: нет зависимостей, не заполнен .env, не подключён
            # сам запрос. Главное — в сеть модуль не полез.
            check_true("_api_get отказал понятным текстом",
                       any(s in str(e) for s in ("не настроен", "не подключён",
                                                 "Не читаются настройки")),
                       str(e))
        except Exception as e:                                  # noqa: BLE001
            check_true("_api_get не должен падать трассировкой", False, repr(e))
    finally:
        shift_store.set_data_dir(shift_store._DEFAULT_DIR)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("❌ ПРОВАЛЫ: " + ", ".join(failures) if failures
                  else "✅ Болванка приёма из Мессенджера готова: подставить "
                       "останется только два запроса к API"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
