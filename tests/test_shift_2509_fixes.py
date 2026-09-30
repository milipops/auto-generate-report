# -*- coding: utf-8 -*-
"""Регрессии аудита отчёта за 25.09.2026 (бот собрал сам, сверка с текстом мастера).

Данные берём из зафиксированного снимка ИИ-разбора `tests/ai_raw_25_09_2026.json`,
а не из живого `data/shifts/`: разбор перезаписывается каждым платным прогоном и
формулирует причины по-новому, а проверять здесь нужно детерминированный слой —
правила арифметики, которые закодированы в коде (см. тест test_shift_2209_fixes).

1. Ст.111: машина стояла в наладке, и в те же минуты делалась чинка. Обе причины
   легли в простой отдельными строками, и лист показал 14,78 ч простоя за
   12-часовую смену. Слова пользователя: «машина была в наладке и в это время
   производилась чинка, из за этого вышло задвоение часов, надо исправить чтобы
   таких ошибок не повторялось в будущем». Станок в общих минутах стоит ОДИН раз.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl                                        # noqa: E402

from services.excel_export import export_shift_reports   # noqa: E402
from services.shift_report import (                     # noqa: E402
    MACHINE_SHIFT_HOURS,
    TOLERANCE_H,
    build_shift_reports_from_structured,
    format_shift_report,
)

DATE = "25.09.2026"


def _reports():
    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ai_raw_25_09_2026.json")
    with open(fixture, encoding="utf-8") as f:
        return build_shift_reports_from_structured(json.load(f))


def _r(num: str):
    for r in _reports():
        if str(r.machine) == num:
            return r
    raise AssertionError(f"нет станка {num} за {DATE}")


def _section_notes(title: str) -> dict:
    """Примечания (колонка E) строк с указанным заголовком на листе-пояснении."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "t.xlsx")
        export_shift_reports(_reports(), path, DATE)
        wb = openpyxl.load_workbook(path)
        ws = wb[wb.sheetnames[0]]
        notes, current = {}, None
        for row in ws.iter_rows(min_col=1, max_col=5):
            a = str(row[0].value or "").strip()
            if a == "№ машины":
                current = str(row[1].value or "").strip()
                notes.setdefault(current, "")
            elif a == title and current:
                notes[current] = str(row[4].value or "").strip()
        return notes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _machine_notes() -> dict:
    return _section_notes("Работа машины всего")


def _operator_notes() -> dict:
    return _section_notes("Работа оператора всего")


def test_111_setup_and_repair_share_minutes_once():
    """Ст.111 за 25.09: наладка и чинка в одних минутах — один простой, не два.

    Наладка 09:35–13:48 (длинная запись = состояние машины) забирает минуты,
    и внутри неё остаётся только та чинка, которая в эти минуты не уместилась:
    4,47 ч превращаются в 1,5 ч, а снятые 2,97 ч названы причиной и в чате, и в
    Excel. Часы оператора (чинка 3,53 ч из его собственных окон) не тронуты —
    человек это дело действительно делал.
    """
    r = _r("111")
    rows = {lab: h for lab, h, _a in r.downtime_by_reason()}
    assert abs(rows["наладка/переналадка"] - 5.88) < 0.02, rows
    assert abs(rows["чинка/ремонт"] - 1.5) < 0.02, rows
    total = round(sum(rows.values()), 2)
    assert total <= MACHINE_SHIFT_HOURS + TOLERANCE_H, (total, rows)
    assert abs(r.cross_label_deduped - 2.97) < 0.02, r.cross_label_dedup_detail
    assert r.cross_label_dedup_detail == [("чинка/ремонт", 2.97)], \
        r.cross_label_dedup_detail
    assert r.machine_excess == 0.0, (r.machine_balance, rows)
    assert r.machine_balance <= MACHINE_SHIFT_HOURS + TOLERANCE_H, \
        r.machine_balance
    # Работа по счётчику и часы оператора остались как их написал мастер.
    assert abs(r.work_hours - 3.53) < 0.02, r.work_hours
    # Добивки до нормы нет (приказ владельца от 28.09.2026): 3,68 + 4,43 = 8,11,
    # а не 11,25. Нехватку видно отдельной строкой расхождения.
    assert abs(r.operator_hours - 8.11) < 0.02, r.operator_hours_parts()
    assert abs(r.operator_shortfall - 3.14) < 0.02, r.operator_shortfall
    assert ("чинка/ремонт (13 раз)", 3.53) in r.operator_work_by_reason(), \
        r.operator_work_by_reason()
    text = format_shift_report(r)
    assert "одни и те же минуты" in text, text
    assert "2,97" in text, text
    print(f"✅ 111 за 25.09: простой {total} ч, задвоение {r.cross_label_deduped} ч "
          f"снято и названо")


def test_111_operator_rows_sum_to_the_total_without_padding():
    """Снятие задвоения не добавляет оператору минут, а строки складываются в итог.

    «Контроль за работой машины» — это время работающего станка, которого тут
    уже нет сверх названных окон (вся работа машины стоит в его же параллельных
    строках). Отдельной строкой-добивкой он быть перестал: приказ владельца от
    28.09.2026 — не добивать и не обрезать до нормы 11,25. Инвариант, который
    здесь нужен, — правило Р22: строки оператора обязаны дать его итог, и ни
    одна из них не вырастает за счёт освобождённых минут простоя.
    """
    r = _r("111")
    parts = dict(r.operator_hours_parts())
    assert abs(sum(parts.values()) - r.operator_hours) <= 0.01, (
        parts, r.operator_hours)
    assert r.operator_control_hours == 0.0, parts
    assert r.operator_explicit_hours == r.operator_hours, (
        r.operator_explicit_hours, r.operator_hours)
    assert r.operator_hours <= MACHINE_SHIFT_HOURS, r.operator_hours
    print(f"✅ 111: слагаемые оператора складываются в {r.operator_hours} ч "
          f"без строки-добивки")


def test_whole_shift_state_row_gives_the_person_no_hours():
    """Станки 121, 151, 601: окно 07:00–19:00 на стоявшей машине — состояние
    машины, а не смена живого человека.

    Прогон 25.09.2026 дал Пронкину 12 ч на 121 («работы инженерной службы по
    креплению кордленты») и 12 ч на 151 («в службе наладки») при нулевом
    выпуске обоих станков; с его же 6,63 ч на 111 выходило 30,63 ч за
    12-часовую смену. Теперь человек в такой строке стоит с нулём и пометкой
    «время не уточнено» (прецедент 01.09 — Каленюк), а 12 ч простоя машина
    сохраняет: она действительно стояла весь день.
    """
    day: dict = {}
    for r in _reports():
        for label, hours in r.attracted_by_person():
            who = label.split(" —")[0]
            day[who] = round(day.get(who, 0.0) + hours, 2)
    assert abs(day.get("Пронкин", 0.0) - 6.63) < 0.02, day
    assert all(h <= MACHINE_SHIFT_HOURS + TOLERANCE_H
               for h in day.values()), day
    for num in ("121", "151", "601"):
        rows = _r(num).attracted_by_person()
        assert rows, (num, rows)
        assert all(h < 0.01 for _l, h in rows), (num, rows)
        assert any("время не уточнено" in l for l, _h in rows), (num, rows)
    # Простой станка при этом полный: 12 ч на стоявшей машине.
    for num, lab in (("121", "инженерная служба"), ("151", "наладка"),
                     ("601", "инженерная служба")):
        rows = {l: h for l, h, _a in _r(num).downtime_by_reason()}
        assert len(rows) == 1, (num, rows)
        (only_lab, only_h), = rows.items()
        assert abs(only_h - MACHINE_SHIFT_HOURS) < 0.02, (num, rows)
        assert lab in only_lab, (num, only_lab)
    print(f"✅ часы людей за смену: {day}; 121/151/601 стоят с 12 ч простоя")


def test_251_service_name_from_another_topic_becomes_a_role():
    """Ст.251: фамилию службы, которой нет в словах мастера, отчёт не печатает.

    Прогон 25.09.2026: запись «машина в наладке» 07:48–08:13 ушла с performer
    «Рыбин». В теме 251 этой фамилии нет; за весь день она встречается ОДИН раз
    — в выложенном в тему 14тр списке участников совещания 08.09 («в составе:
    Перов, Скрипачев, Рыбин, Каленюк, Черныш»). То есть модель достала имя из
    чужой темы и не из работы, а из протокола. Отчёт показал человека, который
    у станка не был.

    Часы (0,42) при этом настоящие: машина стояла. Поэтому строка остаётся и
    выходит под ролью «наладчик» — как в эталоне пользователя 14.09, ст.321
    («0,87 наладчик — наладка»), а бот говорит мастеру, кого он снял.

    Отдельно проверяем, что правило ВЗЯТО УЗКО: со всеми текстами меняется
    ровно один станок за день, а часы 251 остаются теми же (12 ч минус работа);
    подписи причин при этом дают окно переписки своими словами — см. ниже.
    """
    from services.day_pipeline import _attach_section_texts

    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ai_raw_25_09_2026.json")
    with open(fixture, encoding="utf-8") as f:
        data = json.load(f)
    plain = {str(r.machine): r
             for r in build_shift_reports_from_structured(data)}
    with open(fixture, encoding="utf-8") as f:
        data = json.load(f)
    _attach_section_texts(data, DATE)
    texts = {str(m.get("machine")): (m.get("section_text") or "")
             for m in data["machines"]}
    assert sum(1 for t in texts.values() if t) >= 8, texts
    with_text = {str(r.machine): r
                 for r in build_shift_reports_from_structured(data)}

    r = with_text["251"]
    names = [(s.performer, s.hours) for s in r.attracted_all()]
    assert not any("Рыбин" in (p or "") for p, _h in names), names
    assert ("наладчик", 0.42) in names, names
    # Гость из 321 никуда не делся: его имя мастер писал сам.
    assert any((p or "").startswith("Петровский") and h >= 0.2
               for p, h in names), names
    # Часы станка не шевельнулись: имя сняли, а работу оставили. Отличается
    # ровно одна строка — «чинка/ремонт» выросла на 0,30 ч (18 мин), потому что
    # текст темы даёт окну 12:50–13:08 («12:48 Идёт наладка» … «13:08 Чинка»)
    # имя мастера вместо «причина не указана» (задачи 80 и 110). Сумма часов
    # станка при этом та же: 12 ч минус работа.
    assert (r.work_hours, r.machine_downtime) == (plain["251"].work_hours,
                                                  plain["251"].machine_downtime)
    named = {l: h for l, h, _a in r.downtime_by_reason()}
    before = {l: h for l, h, _a in plain["251"].downtime_by_reason()}
    diff = {k: (before.get(k, 0.0), v) for k, v in named.items()
            if abs(v - before.get(k, 0.0)) > 0.005}
    diff.update({k: (v, named.get(k, 0.0)) for k, v in before.items()
                 if abs(v - named.get(k, 0.0)) > 0.005})
    assert diff == {"чинка/ремонт": (4.12, 4.42)}, diff
    # Часы оператора не шевельнулись от подписи «наладчика». Допуск 0,05 ч, а
    # не 0,01: окна «стоп/пуск» из текста темы (задача 80) дотягивают одну
    # чинку на полминуты, а раньше эту разницу прятала обрезка до нормы 11,25.
    assert abs(r.operator_hours - plain["251"].operator_hours) <= 0.05
    assert any("Рыбин" in w and "не назван" in w for w in r.warnings), \
        r.warnings
    moved = [num for num in plain
             if [(s.performer, s.hours) for s in plain[num].attracted_all()]
             != [(s.performer, s.hours) for s in with_text[num].attracted_all()]]
    assert moved == ["251"], moved
    print(f"✅ 251 за 25.09: «Рыбин» → «наладчик» 0,42 ч; за день задет "
          f"один станок")


def test_service_name_said_by_the_master_survives_the_check():
    """Синтетика: фамилия в причине записи — имя своё, снимать нельзя.

    Иначе правило съело бы Пронкина на 111 за тот же 25.09 («проблему устранил
    наладчик Пронкин») и всех наладчиков, названных мастером в тексте.
    """
    machine = {"machine": "98", "operator": "Тестова",
               "operator_shift_end": "19:00",
               "section_text": "98 машина\nЕвгения Тестова, 09:00: стоп, "
                               "вызвал службу",
               "lines": [{"assortment": "187х3", "counter_start": 1000,
                          "counter_end": 4000}],
               "events": [{"start": "09:00", "end": "10:00", "kind": "наладка",
                           "reason": "машину остановил наладчик Пронкин",
                           "performer": "Пронкин"}]}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    names = [(s.performer, s.hours) for s in r.attracted_all()]
    assert any((p or "").startswith("Пронкин") and h >= 0.99 for p, h in names),\
        names
    assert not any("не назван" in w for w in r.warnings), r.warnings
    print(f"✅ имя из причины записи осталось: {names}")


def test_service_performer_with_initials_only_is_not_stripped():
    """Синтетика: «Р.» (Александр Р.) проверить по фамилии нельзя — имя не трогаем.

    Прогон 16.09.2026, ст.251: наладчик подписывает сообщения «Александр Р.», и
    по одной букве правило решало бы, что человека не было. Снять имя можно
    только по доказанному отсутствию.
    """
    machine = {"machine": "251", "operator": "Вашурина",
               "operator_shift_end": "19:00",
               "section_text": "251 машина\nАлександр Р., 07:25: обрыв устранён",
               "lines": [{"assortment": "187х3", "counter_start": 1000,
                          "counter_end": 4000}],
               "events": [{"start": "07:00", "end": "07:25", "kind": "наладка",
                           "reason": "обрыв нити, требуется помощь наладчика",
                           "performer": "Р."}]}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    names = [(s.performer, s.hours) for s in r.attracted_all()]
    assert any((p or "").strip() == "Р." for p, _h in names), names
    assert not any("не назван" in w for w in r.warnings), r.warnings
    print(f"✅ имя из одних инициалов осталось: {names}")


def test_service_name_absent_from_topic_and_reason_becomes_role():
    """Синтетика: имени нет ни в причине, ни в теме → роль, часы у станка."""
    machine = {"machine": "251", "operator": "Гудкова",
               "operator_shift_end": "19:00",
               "section_text": "251 машина\nОльга Гудкова, 07:48: машина в "
                               "наладке",
               "lines": [{"assortment": "187х3", "counter_start": 1000,
                          "counter_end": 1000}],
               "events": [{"start": "07:48", "end": "08:13", "kind": "наладка",
                           "reason": "машина в наладке",
                           "performer": "Рыбин"}]}
    r = build_shift_reports_from_structured({"machines": [machine]})[0]
    names = [(s.performer, s.hours) for s in r.attracted_all()]
    assert ("наладчик", 0.42) in names, names
    assert not any("Рыбин" in (p or "") for p, _h in names), names
    assert any("Рыбин" in w and "не назван" in w for w in r.warnings), \
        r.warnings
    print(f"✅ выдуманное имя стало ролью: {names}")


def test_excel_names_the_removed_minutes():
    """В Excel видно то же самое: снятые минуты и причина, откуда их убрали.

    Иначе лист показывает 8,28 ч простоя там, где мастер описал 11,25 ч, и
    человек решает, что бот потерял строку.
    """
    notes = _machine_notes()
    note = notes.get("111", "")
    assert "двумя причинами" in note, notes
    assert "повтор снят со строк" in note, note
    assert "чинка/ремонт 2,97 ч" in note, note
    print(f"✅ пояснение в Excel: {note}")


def test_excel_shows_operator_hours_over_the_shift():
    """Переработка оператора видна НЕ только в чате, но и в Excel.

    Прогон 25.09.2026, ст.98: в чате бот писал «переработка 0,69 ч: станок по
    счётчику работал 10,28 ч — это время у оператора не урезаем», а в файле
    строка «Работа оператора всего» стояла с примечанием пустым: 11,94 ч при
    смене 11,25 ч без единого объяснения. Человек, смотрящий в Excel, имел
    право считать это ошибкой счёта.

    Часы при этом не режутся (правило 03.09.2026), и красным расхождением
    переработка, объяснённая счётчиком, не становится — лист обязан только
    сказать о ней словами. Станкам без переработки примечание не добавляем:
    иначе пояснение превращается в шум.
    """
    notes = _operator_notes()
    note = notes.get("98", "")
    assert "переработка 0,69 ч" in note, notes
    assert "11,25" in note, note
    assert "10,28" in note, note
    assert "не режем" in note, note
    r = _r("98")
    assert abs(r.operator_hours - 11.94) < 0.02, r.operator_hours_parts()
    assert r.excess_from_machine_time, "часы не должны резаться"
    for num in ("111", "251", "321"):
        assert "переработк" not in notes.get(num, ""), (num, notes)
    print(f"✅ переработка в Excel: {note}")


def test_321_absence_row_does_not_promise_hours_it_kept():
    """Ст.321: уход Петровского на 251 — его работа ЗДЕСЬ, а не часы чужого
    отчёта, и строка отсутствия не должна слать за ними на 251.

    Баг аудита 25.09: строка «отсутствие оператора (работа на №251, часы
    посчитаны там) — 0,7 ч» обещала, что часы лежат в отчёте 251. На 251
    оператором стоит Гудкова, и в её блоке этих минут нет — они уже стояли в
    блоке самого Петровского («работа на других станках 0,27»). Обещание
    остаётся честным только для ухода на станок, где человек — свой оператор.
    """
    r321 = _r("321")
    rows = [lab for lab, _h, _a in r321.downtime_by_reason()]
    away = [lab for lab in rows if "работа на №251" in lab]
    assert away, rows
    assert "часы посчитаны там" not in away[0], away
    # 10,39 + 0,27 = 10,66 ч названных работ, сверху 0,59 ч «контроля»: 0,17 ч
    # работы станка по счётчику и 0,42 ч добора остатка до нормы. Разрыв был
    # меньше 1,5 ч, поэтому добор применён по приказу владельца от 30.09.2026
    # (№140); большим он не закрывается никогда.
    assert abs(r321.operator_hours - 11.25) <= 0.02, r321.operator_hours_parts()
    assert ("работа на других станках", 0.27) in r321.operator_hours_parts(), \
        r321.operator_hours_parts()
    assert ("контроль за работой машины", 0.59) in r321.operator_hours_parts(), \
        r321.operator_hours_parts()
    # Часы не потерялись между отчётами: станок, куда приходили, видит гостя.
    r251 = _r("251")
    assert any((s.performer or "").startswith("Петровский") and s.hours >= 0.2
               for s in r251.attracted_all()), \
        [(s.performer, s.hours) for s in r251.attracted_all()]
    print(f"✅ 321: {away[0]}; 251 видит Петровского в привлекаемом персонале")


def test_321_absence_row_claims_only_the_hours_the_master_wrote():
    """Ст.321: «работа на №251» — только записанные минуты, остаток без причины.

    Баг аудита 26.09 (всплыл на платном прогоне 25.09): строка «отсутствие
    оператора» — заглушка, добивающая баланс станка до 12 ч, и вся её длина
    подписывалась уходом на 251. Мастер записал уход 17:51–18:06 (0,25 ч) плюс
    0,02 ч на 111, а в строке «работа на №251» стояло 0,7 ч (в платном разборе
    — 0,95 ч). Читатель получал сказаное, чего переписка не подтверждает.
    """
    r321 = _r("321")
    rows = {lab: h for lab, h, _a in r321.downtime_by_reason()}
    away = round(sum(h for lab, h in rows.items() if "работа на №" in lab), 2)
    assert abs(away - 0.27) <= 0.02, rows
    unexplained = round(sum(h for lab, h in rows.items()
                            if "причина не указана" in lab), 2)
    assert unexplained >= 0.4, rows
    # Цифры от подписи не уехали: тот же простой и тот же баланс смены.
    assert abs(sum(rows.values()) - 11.83) <= 0.05, rows
    assert abs(r321.machine_balance - 12.0) <= 0.02, r321.machine_balance
    print(f"✅ 321: «работа на №…» {away} ч, без причины {unexplained} ч, "
          f"простой {round(sum(rows.values()), 2)} ч")


def test_251_repair_verb_names_the_stop_not_thread_pull():
    """Ст.251: «продолжаю чинить и спускать узлы» — чинка, а не протяжка.

    Самый большой простой дня (2,92 ч) bot подписывал «протяжкой узлов», которой
    мастер не называл ни разу: маркер чинки знал только существительное «чинка»,
    а формы глагола («чинить», «чиню», «вычинена») проваливались к маркеру
    «узл». 0,15 ч «продолжаю вырезать и чинить полотно» ехали в «прочее».

    На 111 та же правка не должна ничего менять: «протяжка узлов после подмены,
    28 узлов протяжки» мастер назвал честно, и она осталась внутри наладки
    Пронкина (цифры проверяет test_111_setup_and_repair_share_minutes_once).
    """
    r = _r("251")
    rows = {lab: h for lab, h, _a in r.downtime_by_reason()}
    assert "протяжка узлов" not in rows, rows
    assert "прочее" not in rows, rows
    assert rows["чинка/ремонт"] >= 2.92 + 0.15, rows
    # 2,92 ч остались в простое станка один раз: сумма окон не вылезла за смену.
    assert sum(rows.values()) <= MACHINE_SHIFT_HOURS + TOLERANCE_H, rows
    # Часы оператора от подписи причины не изменились: 5,66 + 4,63 = 10,29 ч
    # названных работ. Сверху 0,96 ч добора до нормы 11,25 — приказ владельца от
    # 30.09.2026 (№140) велит закрывать разрыв до 1,5 ч строкой «контроль за
    # работой машины»; до 28.09.2026 такая добивка была запрещена и запрещена
    # и сейчас, когда разрыв больше 1,5 ч.
    assert abs(r.operator_explicit_hours - 10.29) <= 0.02, (
        r.operator_explicit_hours)
    assert abs(r.operator_hours - 11.25) <= 0.02, r.operator_hours_parts()
    work = dict(r.operator_work_by_reason())
    assert abs(work.get("чинка/ремонт (6 раз)", 0.0) - 4.11) < 0.02, work
    print(f"✅ 251 за 25.09: чинка {rows['чинка/ремонт']} ч, протяжки нет")


def test_251_denied_spool_fall_is_the_machines_error():
    """Ст.251 17:49–19:00: «все шпули на месте, ни одна не сошла» — НЕ сход.

    Отрицание читалось как утверждение, и 1,18 ч встали строкой «дотягивание
    (подстановка сошедших шпулей)» — работой, которой не было. Мастер назвал
    причину другим словом: «ошибка не скидывается». Эта же ошибка названа и в
    чужой теме дня (Петровский: «на 251 ошибка мне не понятна»).
    """
    r = _r("251")
    rows = {lab: h for lab, h, _a in r.downtime_by_reason()}
    assert abs(rows.get("ошибка машины", 0.0) - 1.18) < 0.02, rows
    assert "дотягивание (подстановка сошедших шпулей)" not in rows, rows
    assert "замена шпуль (станок стоит)" not in rows, rows
    assert "ошибка" in format_shift_report(r), format_shift_report(r)[:400]
    print(f"✅ 251 за 25.09: 1,18 ч названы ошибкой машины, схода нет")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nПроверено тестов: {len(fns)}.")
