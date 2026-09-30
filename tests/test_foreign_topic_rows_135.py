# -*- coding: utf-8 -*-
"""Приказ владельца от 30.09.2026 (вопрос №135): строки оператора в чужих темах.

Дословно вопрос, заданный по сверке с его файлом `отчет за 29 число.xlsx`:
«часы оператора считать также по его строкам в чужих темах» — ответ: «брать
строки оператора можно в чужих темах», «вот это делай».

Почему часы не сходились. Смена 29.09.2026, Вашурина — оператор станка №151.
Утро 09:30–10:40 она описала в теме 251 («развивка шпулей после наладки
шпульного стола 24 шт», «перекладывание развитых шпуль из телеги», «взвешивание
отходов»), а 10:40–12:00, 14:20–16:40, 16:40–18:45 и 18:45–19:00 — в теме 121
(«установка и подвязка челноков со шпулями», «подвязка челноков», «нумерация
шпуль на 121 м», «культура производства, заполнение чата»). Ни одна из этих
минут в её часы не приходила: блок показывал 5,87 ч при смене, которую она
отработала целиком. Причина — обратный перенос брал только `attracted_own`
чужого станка, то есть его ПРОСТОЙ, а работа, записанная в чужой теме при
стоящем (или без счётчика) станке, лежит в `attracted`.

Что правило обязано соблюдать (проверено этим файлом):

* часы берутся ТОЛЬКО у того же человека, чей блок (`_same_person`);
* строки, already переложенные прямым переносом (`transferred`), обратно не
  берутся — иначе часы одного ухода посчитались бы дважды;
* окно переезжает вместе с часами, поэтому `_squeeze_time_overlaps` снимает
  минуты, которые один и тот же человек описал в двух темах;
* часы СТАНКА, его простой, выпуск и привлекаемый персонал не сдвигаются.

Запуск: py -3.11 tests/test_foreign_topic_rows_135.py
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))

import services.shift_report as sr                            # noqa: E402
from services.shift_report import (                           # noqa: E402
    MACHINE_SHIFT_HOURS, _fmt_h, _rows_from_foreign_topic, _same_person,
    build_shift_reports_from_structured)

RAW_29 = os.path.join(TESTS, "ai_raw_29_09_2026.json")


def _reports(path):
    with open(path, encoding="utf-8") as f:
        return {str(r.machine): r
                for r in build_shift_reports_from_structured(json.load(f))}


def _union_hours(rows):
    """Минуты, занятые строками, без задвоения пересечений."""
    ivs = sorted((s.start_min, s.end_min) for s in rows
                 if s.start_min is not None and s.end_min is not None)
    total, cur_end = 0, None
    for start, end in ivs:
        if cur_end is None or start >= cur_end:
            total += end - start
            cur_end = end
        elif end > cur_end:
            total += end - cur_end
            cur_end = end
    return round(total / 60, 2)


def _with_rule(on: bool):
    """Прогон всех сохранённых разборок с правилом или без него.

    Часы оператора — свойство, числа снимаются ПОКА действует режим: после
    возврата функции объекты «старого» прогона пересчитались бы новым правилом.
    """
    old = sr._rows_from_foreign_topic
    sr._rows_from_foreign_topic = (lambda other: []) if not on else old
    try:
        out = {}
        for path in sorted(glob.glob(os.path.join(TESTS, "ai_raw_*.json"))):
            for r in _reports(path).values():
                out[(os.path.basename(path), str(r.machine))] = {
                    "работы_станка": r.work_hours,
                    "простой": r.tech_stops_total,
                    "баланс": r.machine_balance,
                    "норма": r.operator_norm,
                    "привлечено": round(sum(s.hours for s in r.attracted_all()), 2),
                    "отсутствие": r.full_absence_hours,
                    "перенос": round(sum(s.hours for s in r.elsewhere_extra), 2),
                    "оператор": r.operator_hours,
                }
        return out
    finally:
        sr._rows_from_foreign_topic = old


# ——— Дословные строки мастера за 29.09.2026 ———

def test_foreign_topic_work_rows_reach_the_author_block():
    """Строки Вашурин из тем 121 и 251 лежат в её блоке (станок 151).

    До приказа №135 в блок попадали только остановки чужого станка, поэтому
    темы 121 и 251 — где она работала, а не чинила чужой простой — терялись.
    """
    r = _reports(RAW_29)["151"]
    labels = {s.label_override for s in r.elsewhere_extra}
    assert labels == {"работа на машине №111", "работа на машине №121",
                      "работа на машине №251"}, labels
    by_machine = {}
    for s in r.elsewhere_extra:
        by_machine[s.label_override[-3:]] = round(
            by_machine.get(s.label_override[-3:], 0) + s.hours, 2)
    assert by_machine["121"] == 5.99, by_machine
    # 1,17 а не 1,05 ч: «нумерация шпуль с 1.285–1.303» (0,12 ч) больше не
    # сливается с «перекладыванием развитых шпуль» той же длины (правка #148).
    assert by_machine["251"] == 1.17, by_machine
    # Дословные причины мастера — их видно в блоке, а не только в чужой теме.
    text = " ".join(s.reason for s in r.elsewhere_extra)
    for phrase in ("подвязка челноков", "нумерация шпуль на 121 м",
                   "развивка шпулей после наладки шпульного стола 24 шт",
                   "взвешивание отходов"):
        assert phrase in text, phrase
    print(f"✅ 29.09 ст.151: из чужих тем {by_machine}, часы оператора "
          f"{r.operator_hours} ч (было 5,87)")


def test_hours_only_grow_for_the_same_person():
    """Чужая строка не может прийти в блок: перенос только своего человека."""
    for path in sorted(glob.glob(os.path.join(TESTS, "ai_raw_*.json"))):
        for r in _reports(path).values():
            op = (r.operator or "").strip()
            if not op:
                assert not r.elsewhere_extra, (path, r.machine)
                continue
            for s in r.elsewhere_extra:
                assert _same_person(s.performer, op), (
                    path, r.machine, op, s.performer, s.reason)
    print("✅ ни одна чужая фамилия не перенесена в блок оператора")


def test_overlapping_windows_from_two_topics_count_once():
    """Одни и те же минуты, описанные в двух темах, считаются один раз.

    Чинка пропусков на 111 («22 пропуска, чинка полотна Вашурина, Гудкова»,
    13:22–15:59 = 2,62 ч) и подвязка челноков на 121 (14:20–16:40 = 2,33 ч)
    пересекаются на 99 мин. Без перенесённого окна `_squeeze_time_overlaps` не
    имел бы чего снимать, и обе строки сложились бы в 4,95 ч там, где человек
    физически был 3,3 ч.
    """
    r = _reports(RAW_29)["151"]
    rows = list(r.elsewhere_extra)
    chinka = [s for s in rows if "22 пропуска" in s.reason]
    podvyazka = [s for s in rows if s.reason.endswith("(подвязка челноков)")]
    assert len(chinka) == len(podvyazka) == 1, (chinka, podvyazka)
    overlap = round(chinka[0].hours + podvyazka[0].hours
                    - _union_hours([chinka[0], podvyazka[0]]), 2)
    assert 1.6 <= overlap <= 1.65, overlap

    own = [s for s in r.tech_stops if s.hours
           and "работа на машине" not in s.reason
           and "весь день" not in s.reason]
    assert round(sum(s.hours for s in own), 2) == 2.5, [(s.hours, s.reason)
                                                        for s in own]
    naive = round(sum(s.hours for s in rows) + sum(s.hours for s in own), 2)
    assert naive == 13.03, naive
    assert r.operator_hours == 10.68, r.operator_hours_parts()
    assert r.operator_hours <= naive - overlap + 0.01, (r.operator_hours, naive,
                                                        overlap)
    assert r.operator_hours <= _union_hours(own + rows) + 0.01, (
        r.operator_hours, _union_hours(own + rows))
    assert r.operator_hours <= MACHINE_SHIFT_HOURS, r.operator_hours
    parts = dict(r.operator_hours_parts())
    assert abs(sum(parts.values()) - r.operator_hours) <= 0.01, (parts,
                                                                 r.operator_hours)
    # В итоговом блоке та же подвязка стоит укороченной на общие минуты.
    final = [s for s in r.own_operator_stops() if "подвязка челноков)" in s.reason
             and "установка" not in s.reason]
    assert len(final) == 1 and abs(final[0].hours - 0.68) < 0.01, [
        (s.hours, s.reason) for s in final]
    print(f"✅ пересечение двух тем снято: названо {naive} ч, общих минут "
          f"{overlap} ч, итог {r.operator_hours} ч (подвязка 2,33 → "
          f"{final[0].hours})")


def test_state_row_without_time_gives_no_hours():
    """«ушла на 121 м (время не уточнено)» часов не переносит."""
    reports = _reports(RAW_29)
    rows = _rows_from_foreign_topic(reports["251"])
    assert not any("ушла на" in (s.reason or "") for s in rows), [
        s.reason for s in rows]
    assert all(s.hours > 0 for s in rows), [s.hours for s in rows]
    print(f"✅ строка-состояние без подтверждения времени не едет в блок "
          f"(из темы 251 перенесено {round(sum(s.hours for s in rows), 2)} ч)")


def test_directly_transferred_rows_are_not_taken_back():
    """Прямой перенос (уход оператора) не возвращается ему вторым ходом."""
    for path in sorted(glob.glob(os.path.join(TESTS, "ai_raw_*.json"))):
        for r in _reports(path).values():
            for s in _rows_from_foreign_topic(r):
                assert not s.transferred, (path, r.machine, s.reason)
    print("✅ rows с transferred=True из чужих тем не берутся")


# ——— Радиус правки: часы станка не двигаются ———

def test_rule_moves_only_operator_hours():
    """С правилом / без правила по всем разборкам: сдвигается только блок оператора.

    Замер (scratch/probe_foreign_135.py) на тех же данных: 1 станк-смена из 97,
    стабильные поля не шевельнулись ни в одном.
    """
    on, off = _with_rule(True), _with_rule(False)
    assert set(on) == set(off)
    STABLE = ("работы_станка", "простой", "баланс", "норма", "привлечено",
              "отсутствие")
    moved = []
    for key, row in on.items():
        before = off[key]
        for name in STABLE:
            assert row[name] == before[name], (key, name, before[name], row[name])
        if abs(row["оператор"] - before["оператор"]) > 0.01:
            moved.append((key, before["оператор"], row["оператор"]))
    assert moved == [(("ai_raw_29_09_2026.json", "151"), 2.5, 10.68)], moved
    # Прежним правилом (только простой чужого станка) у 151 было 5,87 ч —
    # темы 111 переносились, темы 121 и 251 терялись.
    print(f"✅ правило задевает только часы оператора: {moved}")


def test_padded_control_not_invented_for_a_machine_without_counter():
    """Станок без показаний счётчика не получает «контроль за работой машины».

    151 за 29.09: одно утреннее число 790782 без вечернего — время работы
    машины неизвестно. Как только её оператор получил часы из чужих тем, добор
    #140 начал рисовать 0,69 ч «контроля» машине, которая, возможно, стояла.
    """
    r = _reports(RAW_29)["151"]
    assert r.balance_unknown, r.balance_unknown
    assert r.operator_machine_led_hours == 0.0, r.operator_machine_led_hours
    assert r.operator_control_hours == 0.0, r.operator_control_hours
    assert not any("контроль" in lab for lab, _h in r.operator_hours_parts()), (
        r.operator_hours_parts())
    assert abs(r.operator_hours - r.operator_explicit_hours) < 0.02, (
        r.operator_hours, r.operator_explicit_hours)
    print(f"✅ 29.09 ст.151 без счётчика: контроль 0, итог = названные "
          f"{r.operator_hours} ч")


def test_chat_text_shows_the_transferred_rows_and_the_total():
    """Текст отчёта называет перенесённые строки и итог, а не молчит.

    До правки ветка «нет показаний счётчика» печатала «Сотрудник: операции при
    остановке 0 ч» — станок без счётчика считался станком без человека. У №151
    за 29.09 счётчика нет, а часы у его оператора есть (темы 111, 121, 251),
    поэтому в чате итог расходился с Excel: там блок 10,68 ч, тут — 0 ч.
    """
    from services.shift_report import format_shift_report
    r = _reports(RAW_29)["151"]
    text = format_shift_report(r)
    assert "Работа в чужих темах" in text, text[:2000]
    for phrase in ("работа на машине №121", "работа на машине №251",
                   "работа на машине №111"):
        assert phrase in text, phrase
    assert f"{_fmt_h(r.operator_hours)} из" in text, text[text.find("Сотрудник"):][:200]
    assert "операции при остановке 0 ч" not in text, text[text.find("Сотрудник"):][:200]
    print(f"✅ чат показывает перенос и итог {_fmt_h(r.operator_hours)} ч")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
    print("\nПроверено тестов: "
          f"{sum(1 for k in globals() if k.startswith('test_'))}.")
