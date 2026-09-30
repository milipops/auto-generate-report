# -*- coding: utf-8 -*-
"""Приказ владельца от 30.09.2026 (вопрос №140): добор остатка часов оператора.

Дословно вопрос, заданный по сверке с его файлом `отчет за 29 число.xlsx`:
«остаток часов оператора до твоей 11,25 добирать строкой „контроль за работой
машины“?» — ответ: «это да но если расхождение не прям большое (до 1-1,5 часа)».

То есть правило от 28.09.2026 («не нужно их добивать до 11,25 … оставляй как
есть») остаётся в силе для БОЛЬШОГО разрыва, а небольшой — до OPERATOR_PAD_MAX_H
— закрывается строкой «контроль за работой машины». Причина, почему раньше часы
не сходились: у 111 за 29.09 в его файле 11,25 ч, а бот без добора давал 10,69;
у 321 — 11,25 против 10,13.

Чего добор делать НЕ вправе (проверено на всех 12 сохранённых разборках,
scratch/probe_op_pad_140.py):

* придумывать часы, которых человек физически не был у станка — потолок
  присутствия (operator_shift_span) и вычет часов на другом СВОЁМ станке
  (full_absence) считаются до добора;
* закрывать простой станка, который весь день не работал: «контроль за работой
  машины», которой стояла, — выдуманное время (прогон 15.09.2026, ст.111:
  Дремкова подменяла шпули, бот добивал 7,05 ч и выдавал 11,25 при эталоне
  3,92 ч);
* сдвигать часы СТАНКА, выпуск, отходы, простой и привлекаемый персонал —
  меняется только блок часов оператора.

Запуск: py -3.11 tests/test_operator_pad_3009.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import machine_rates                            # noqa: E402
from services.shift_report import (                           # noqa: E402
    MACHINE_SHIFT_HOURS, OPERATOR_PAD_MAX_H,
    TOLERANCE_H, build_shift_reports_from_structured, format_shift_report)


def _nodes(hours, machine="98"):
    return round(hours * machine_rates.get_rate(machine) * 60)


def _one(machine, hours, events, **extra):
    day = {"date": "30.09.2026", "machines": [dict(
        {"machine": machine, "operator": "Тестова",
         "operator_shift_end": "19:00",
         "lines": [{"counter_start": 0,
                    "counter_end": _nodes(hours, machine)}],
         "events": events}, **extra)]}
    return build_shift_reports_from_structured(day)[0]


def _repair(start, end):
    return [{"kind": "чинка", "start": start, "end": end,
             "reason": "чинка полотна после обрыва нити",
             "performer": "Тестова"}]


def _control(r):
    """Часы строки «контроль за работой машины» в разбивке итога."""
    hits = [h for lab, h, *_ in r.operator_hours_parts() if "контроль" in lab]
    return hits[-1] if hits else 0.0


# ——— Граница добора ———

def test_small_gap_is_padded_to_norm():
    """Разрыв 1,25 ч (меньше 1,5) добирается — как 111 за 29.09 у владельца."""
    r = _one("98", 9.0, _repair("09:00", "10:00"))
    base = r.operator_explicit_hours + r.operator_machine_led_hours
    gap = round(r.operator_norm - base, 2)
    assert abs(base - 10.0) < 0.02, r.operator_hours_parts()
    assert 0.02 < gap <= OPERATOR_PAD_MAX_H, (base, gap, r.operator_norm)
    assert abs(r.operator_hours - r.operator_norm) < 0.02, r.operator_hours_parts()
    assert _control(r) > r.operator_machine_led_hours, (
        _control(r), r.operator_machine_led_hours)
    assert r.operator_shortfall == 0.0, r.operator_shortfall
    print(f"✅ разрыв {gap} ч добран: {base} → {r.operator_hours} ч "
          f"(контроль {_control(r)} ч при работе машины {r.work_hours} ч)")


def test_gap_over_the_limit_is_left_as_is():
    """Разрыв больше 1,5 ч не добираем: это приказ 28.09, он в силе."""
    r = _one("98", 9.0, [])
    base = r.operator_explicit_hours + r.operator_machine_led_hours
    gap = round(r.operator_norm - base, 2)
    assert gap > OPERATOR_PAD_MAX_H, (base, gap)
    assert abs(r.operator_hours - base) < 0.02, r.operator_hours_parts()
    assert _control(r) <= r.operator_machine_led_hours + 0.01, (
        _control(r), r.operator_machine_led_hours)
    assert r.operator_shortfall > TOLERANCE_H, r.operator_shortfall
    text = format_shift_report(r)
    assert "недостаток" in text.lower(), text[:800]
    print(f"✅ разрыв {gap} ч > {OPERATOR_PAD_MAX_H}: часы остались "
          f"{r.operator_hours}, недостаток назван словами")


def test_gap_inside_tolerance_needs_no_pad():
    """То, что и так в допуске ±20 мин, добором не трогать."""
    r = _one("98", 10.0, _repair("09:00", "10:00"))
    base = r.operator_explicit_hours + r.operator_machine_led_hours
    assert r.operator_norm - base <= TOLERANCE_H, (base, r.operator_norm)
    assert abs(r.operator_hours - base) < 0.02, r.operator_hours_parts()
    assert _control(r) <= r.operator_machine_led_hours + 0.01
    assert r.operator_shortfall == 0.0
    print(f"✅ разрыв в допуске: контроль = работа машины "
          f"{r.operator_machine_led_hours} ч, итог {r.operator_hours} ч")


# ——— Потолки, которые добор не снимает ———

def test_pad_never_exceeds_presence():
    """Часов, которых человек у станка не был, добор не ставит."""
    r = _one("98", 9.0, [], operator_shift_end="15:00")
    assert abs(r.operator_shift_span - 8.0) < 0.01, r.operator_shift_span
    assert r.operator_hours <= r.operator_shift_span + 0.01, (
        r.operator_hours, r.operator_shift_span)
    # Присутствие 8 ч, станок работал 9 ч — добираать нечего, потолок уже снимает.
    assert _control(r) <= r.operator_shift_span + 0.01, _control(r)
    print(f"✅ присутствие {r.operator_shift_span} ч: оператор "
          f"{r.operator_hours} ч, контроль {_control(r)} ч")


def test_pad_leaves_hours_of_a_standing_machine_alone():
    """Станок весь день стоял — «контроля» нет, даже если разрыв маленький.

    Дословный случай прогона 15.09.2026, ст.111 (Дремкова): весь день навивала
    шпули при нулевом выпуске, бот добивал 7,05 ч «контроля» и выдавал 11,25 из
    11,25 при эталоне пользователя 3,92 ч.
    """
    r = _one("321", 0.0, [
        {"kind": "навивка", "start": "07:00", "end": "12:40",
         "reason": "машина на подмене, навиваю шпули",
         "performer": "Тестова"},
        {"kind": "остановка", "start": "14:04", "end": "19:00",
         "reason": "готова к пропуску узлов, пуска не было",
         "performer": "Тестова"}])
    assert r.zero_output_all_shift, "станок без выпуска — признак обязателен"
    assert _control(r) == 0.0, r.operator_hours_parts()
    assert abs(r.operator_hours - r.operator_explicit_hours) < 0.02, (
        r.operator_hours, r.operator_explicit_hours)
    assert r.operator_hours != MACHINE_SHIFT_HOURS, r.operator_hours
    print(f"✅ стоявший станок: названо {r.operator_explicit_hours} ч, "
          f"контроль 0, итог {r.operator_hours} ч")


def test_pad_does_not_move_machine_hours_or_attracted_staff():
    """Добор касается только блока оператора: станок, простой, привлечённые целы.

    Замер делается двумя прогонами реальной смены 29.09.2026 — с границей 1,5 ч
    и с границей 0 ч (то есть до приказа). Совпадают все часы, кроме часов
    оператора; у 111 и 321 добор срабатывает, у остальных — нет.
    """
    import services.shift_report as sr
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "ai_raw_29_09_2026.json")

    # Обязаны совпасть во всех станках: это часы станка и его блоковые счётчики.
    STABLE = ("work_hours", "tech_stops_total", "machine_balance",
              "operator_norm", "operator_explicit_hours",
              "operator_machine_led_hours", "full_absence_hours")
    # Меняются по новому правилу: только строки часов оператора.
    LIQUID = ("operator_control_hours", "operator_hours",
              "operator_shortfall", "operator_excess")
    ALL_FIELDS = STABLE + LIQUID

    def _numbers(pad_max):
        """Все сравниваемые числа режима, снятые ПОКА действует его граница.

        Часы оператора — свойство, оно считается в момент обращения, поэтому
        цифры нельзя оставлять на потом: после возврата границы 1,5 ч объекты
        «старого» режима пересчитались бы новым правилом и разница пропала бы.
        """
        # Сборка правит входной словарь на ходу, поэтому для каждого режима
        # нужен отдельный, только что прочитанный экземпляр данных.
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        old_limit = sr.OPERATOR_PAD_MAX_H
        sr.OPERATOR_PAD_MAX_H = pad_max
        try:
            out = {}
            for r in build_shift_reports_from_structured(raw):
                row = {name: getattr(r, name) for name in ALL_FIELDS}
                row["привлечено"] = round(sum(s.hours
                                               for s in r.attracted_all()), 2)
                out[str(r.machine)] = row
            return out
        finally:
            sr.OPERATOR_PAD_MAX_H = old_limit

    new, old = _numbers(OPERATOR_PAD_MAX_H), _numbers(0.0)
    padded = []
    for num, row in new.items():
        before = old[num]
        for name in STABLE:
            assert row[name] == before[name], (num, name, row[name],
                                               before[name])
        if abs(row["operator_hours"] - before["operator_hours"]) > 0.01:
            padded.append((num, before["operator_hours"],
                           row["operator_hours"]))
    assert sorted(padded) == [("111", 10.45, 11.25), ("321", 10.43, 11.25)], padded
    print(f"✅ 29.09: часы станка и привлекаемый персонал не сдвинулись, добор "
          f"только у {', '.join(n for n, _b, _a in padded)}")


# ——— Файлы владельца: эталон 29.09.2026 ———

def test_etalon_2909_operators_now_match_owner_file():
    """111 и 321 за 29.09: в его файле 11,25 ч — и бот даёт 11,25.

    Эталон: `отчет за 29 число.xlsx`, лист «29.09» (Гудкова — машина 111,
    Петровский — машина 321). До добора было 10,45 и 10,43 ч.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "ai_raw_29_09_2026.json")
    with open(path, encoding="utf-8") as f:
        reports = {str(r.machine): r for r in
                   build_shift_reports_from_structured(json.load(f))}
    for num in ("111", "321"):
        r = reports[num]
        base = r.operator_explicit_hours + r.operator_machine_led_hours
        gap = round(r.operator_norm - base, 2)
        assert 0.02 < gap <= OPERATOR_PAD_MAX_H, (num, base, gap)
        assert abs(r.operator_hours - 11.25) < 0.02, (num, r.operator_hours_parts())
        assert r.operator_shortfall == 0.0, num
        print(f"✅ 29.09 ст.{num}: {base} + добор {gap} ч = "
              f"{r.operator_hours} ч (эталон владельца 11,25)")


def test_rows_still_sum_to_the_total_after_pad():
    """Правило Р22: после добора слагаемые блока оператора дают его итог."""
    cases = [_one("98", 5.0, _repair("09:00", "10:00") + _repair("11:00", "12:00")),
             _one("98", 3.0, _repair("09:00", "10:00")),
             _one("98", 9.0, [], operator_shift_end="15:00")]
    for r in cases:
        parts = dict(r.operator_hours_parts())
        assert parts, r.operator
        assert abs(sum(parts.values()) - r.operator_hours) <= 0.01, (
            parts, r.operator_hours)
    print("✅ после добора строки складываются в итог")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
    print("\nПроверено тестов: "
          f"{sum(1 for k in globals() if k.startswith('test_'))}.")
