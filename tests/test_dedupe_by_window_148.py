# -*- coding: utf-8 -*-
"""Повтор работы одного человека: нужны те же минуты или то же названное дело.

Дефект найден при сверке с ручным файлом пользователя за 29.09.2026
(`отчет за 29 число.xlsx`, лист «29.09», блок станка 251). Прежнее правило
`_dedupe_people` считало повтором совпадение ФАМИЛИИ И ДЛИТЕЛЬНОСТИ (±0,02 ч),
не глядя, в какое время человек стоял. Два разных дела одного человека одной
длины сливались в одно, и часы второго дела пропадали:

    0,13 ч | Вашурина | 10:10–10:18 | перекладывание развитых шпуль из телеги
    0,12 ч | Вашурина | 10:18–10:25 | нумерация шпуль с 1.285-1.303

В его файле обе строки стоят отдельно (0,67 + 0,13 + 0,12 + 0,25 = 1,17 ч
привлекаемого персонала 251), у нас было 1,05 ч. Те же минуты Вашурин —
оператор станка 151, поэтому по приказу №135 («брать строки оператора можно в
чужих темах») пропавшие 0,12 ч не доезжали и в её собственный блок: 10,56 ч
вместо 10,68.

Чего правило терять НЕ вправе: пересказ одного дела с двух станков, где время
часто указано по-разному или не указано вовсе (за 27.08 Камбаров на ст.151 стоял
5 + 2,5 ч при эталонных 2,5). Поэтому повтором остаётся и пара строк с ОДНОЙ И
ТОЙ ЖЕ дословной причиной, даже если окна не пересеклись, и строка без окна той
же фамилии и длины.

Итог: повтор = фамилия + длительность + (та же формулировка дела ИЛИ
пересекающиеся окна).

Запуск: py -3.11 tests/test_dedupe_by_window_148.py
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
    TechStop, _dedupe_people,
    build_shift_reports_from_structured)


def _row(hours, reason, performer, start=None, end=None, **kw):
    return TechStop(hours, reason, performer=performer,
                    start_min=start, end_min=end, **kw)


def _t(hh, mm):
    return hh * 60 + mm


def _reports(path):
    with open(path, encoding="utf-8") as f:
        return {str(r.machine): r
                for r in build_shift_reports_from_structured(json.load(f))}


def test_two_different_tasks_of_one_length_both_survive():
    """Дословные строки мастера 29.09 ст.251: 0,13 и 0,12 ч — оба дела живые."""
    rows = [_row(0.67, "развивка шпулей после наладки шпульного стола 24 шт",
                 "Вашурина", _t(9, 30), _t(10, 10)),
            _row(0.13, "перекладывание развитых шпуль из телеги",
                 "Вашурина", _t(10, 10), _t(10, 18)),
            _row(0.12, "нумерация шпуль с 1.285-1.303",
                 "Вашурина", _t(10, 18), _t(10, 25)),
            _row(0.25, "взвешивание отходов", "Вашурина",
                 _t(10, 25), _t(10, 40))]
    kept = _dedupe_people(rows)
    assert [s.hours for s in kept] == [0.67, 0.13, 0.12, 0.25], kept
    assert round(sum(s.hours for s in kept), 2) == 1.17, kept
    print(f"✅ оба дела Вашурин одной длины учтены: "
          f"{sum(s.hours for s in kept):.2f} ч, {len(kept)} строк")


def test_same_window_same_person_is_still_one_event():
    """Одно дело, описанное дважды в пересекающихся минутах, не становится двумя."""
    kept = _dedupe_people([
        _row(2.5, "замена шпулярника, 58 бабин", "Камбаров",
             _t(9, 0), _t(11, 30)),
        _row(2.5, "Камбаров ушёл на подмену шпулярника на 151", "Камбаров",
             _t(9, 5), _t(11, 35)),
    ])
    assert len(kept) == 1, kept
    print("✅ пересказ одного дела в пересекающемся окне учтён один раз")


def test_one_event_retold_at_a_wrong_time_is_still_one_event():
    """Эталон 27.08 ст.151: дословно то же дело в другом окне — 2,5 ч, не 5.

    Формулировка причины сильнее несовпавшего времени: мастер пересказывает
    один и тот же шпулярник из двух тем и второе окно переносит на вечер.
    """
    kept = _dedupe_people([
        _row(2.5, "замена шпулярника, 58 бабин", "Камбаров",
             _t(7, 0), _t(9, 30)),
        _row(2.5, "замена шпулярника, 58 бабин", "Камбаров",
             _t(16, 30), _t(19, 0)),
    ])
    assert len(kept) == 1, kept
    print("✅ то же дело, названное дословно одинаково, учтено один раз")


def test_row_without_window_is_still_merged():
    """Эталон 27.08: время в пересказе не названо — слияние остаётся.

    Без окна нельзя утверждать, что это другое время, поэтому old-поведение
    сохраняем: фамилия + длительность. Иначе Камбаров на ст.151 снова встал бы
    на 5 + 2,5 ч при эталонных 2,5.
    """
    kept = _dedupe_people([
        _row(2.5, "замена шпулярника, 58 бабин", "Камбаров",
             _t(9, 0), _t(11, 30)),
        _row(2.5, "Камбаров — ушёл на подмену шпулярника на 151", "Камбаров"),
    ])
    assert len(kept) == 1, kept
    print("✅ строка без окна остаётся повтором (эталон 27.08 не задвоен)")


def test_naladka_calls_of_one_length_at_different_time_stay_separate():
    """Исключение про вызовы наладчика работает и с проверкой по окну."""
    kept = _dedupe_people([
        _row(0.5, "наладка, вызов", "Ефремов", _t(8, 0), _t(8, 30),
             is_naladka=True),
        _row(0.5, "наладка, повторный вызов", "Ефремов", _t(14, 0), _t(14, 30),
             is_naladka=True),
    ])
    assert len(kept) == 2, kept
    print("✅ два вызова наладчика одной длины — два события")


# ——— Реальная смена 29.09.2026 против файла пользователя ———

def test_real_2909_attracted_staff_251_now_matches_his_rows():
    """Привлекаемый персонал 251: 1,17 ч — как четыре строки в его файле."""
    rep = _reports(os.path.join(TESTS, "ai_raw_29_09_2026.json"))["251"]
    rows = {s.reason: round(s.hours, 2) for s in rep.attracted_all()
            if s.hours}
    assert rows.get("перекладывание развитых шпуль из телеги") == 0.13, rows
    assert rows.get("нумерация шпуль с 1.285-1.303") == 0.12, rows
    assert abs(rep.attracted_total - 1.17) < 0.01, (
        rep.attracted_total, rep.attracted_all())
    print(f"✅ 29.09 ст.251: привлекаемый персонал "
          f"{sr._fmt_h(rep.attracted_total)} ч; у владельца 1,27 ч — остаток "
          f"0,10 ч это строка Каленюк без окна, её часы бот не придумывает")


def test_real_2909_operator_151_gets_the_lost_twelve_minutes():
    """Те же 0,12 ч возвращаются в блок оператора 151 по приказу №135."""
    rep = _reports(os.path.join(TESTS, "ai_raw_29_09_2026.json"))["151"]
    assert any("нумерация шпуль" in (s.reason or "")
               for s in rep.elsewhere_extra), rep.elsewhere_extra
    assert abs(rep.operator_hours - 10.68) < 0.01, rep.operator_hours_parts()
    print(f"✅ 29.09 ст.151: Вашурина {sr._fmt_h(rep.operator_hours)} ч "
          f"(было 10,56 — 0,12 ч съедала дедупликация)")


def test_rule_moves_only_people_hours_on_all_saved_replays():
    """Радиус: часы СТАНКА не сдвигаются нигде, задеты два блока людей.

    Замер по всем сохранённым разборкам: с прежним правилом и с новым.
    Совпадают work_hours, tech_stops_total, machine_balance, обе разницы
    оператора; расходятся только привлекаемый персонал 251 и часы оператора 151
    за 29.09.2026.
    """
    new = sr._dedupe_people

    def _by_len(stops):
        res = []
        for s in stops:
            dup = False
            for kept in res:
                if abs(kept.hours - s.hours) < 0.02 and \
                        sr._same_person(kept.performer, s.performer):
                    if (kept.is_naladka and s.is_naladka
                            and kept.start_min is not None
                            and s.start_min is not None
                            and abs(kept.start_min - s.start_min) > 1):
                        continue
                    dup = True
                    break
            if not dup:
                res.append(s)
        return res

    STABLE = ("work_hours", "tech_stops_total", "machine_balance",
              "operator_shortfall", "operator_excess")
    LIQUID = ("attracted_total", "operator_hours", "operator_explicit_hours")

    def _numbers(fn):
        """Прогон всех сохранённых разборок; числа снимаются ПОКА действует fn."""
        sr._dedupe_people = fn
        try:
            out = {}
            for path in sorted(glob.glob(os.path.join(TESTS, "ai_raw_*.json"))):
                for r in _reports(path).values():
                    out[(os.path.basename(path), str(r.machine))] = {
                        n: getattr(r, n) for n in STABLE + LIQUID}
            return out
        finally:
            sr._dedupe_people = new

    before, after = _numbers(_by_len), _numbers(new)
    moved = []
    for key, row in after.items():
        old = before[key]
        for n in STABLE:
            assert row[n] == old[n], (key, n, row[n], old[n])
        changed = [n for n in LIQUID if abs(row[n] - old[n]) > 0.01]
        if changed:
            moved.append((key, sorted(changed)))
    assert sorted(moved) == [
        (("ai_raw_29_09_2026.json", "151"),
         ["operator_explicit_hours", "operator_hours"]),
        (("ai_raw_29_09_2026.json", "251"), ["attracted_total"]),
    ], moved
    print(f"✅ радиус {len(after)} станко-смен: сдвинулось {len(moved)} — "
          f"только строки людей, часы станка не тронуты нигде")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
    print("\nПроверено тестов: "
          f"{sum(1 for k in globals() if k.startswith('test_'))}.")
