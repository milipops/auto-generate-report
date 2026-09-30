# -*- coding: utf-8 -*-
"""Намотка полотна на вал — простой станка, а не навивка шпулей.

Пользователь, 28.09.2026: «по 26 числу по 98 машине у неё была наладка, это
намоталась на вал, - тоже простой». Мастер Симонова пишет то же самое словами
в переписке: «10:00 остановила машину. Намотало полотно на вытяжной вал.
Работаю на 26 машине. 11:10 машина в работе».

Что было не так: у такой записи нет своей категории, а маркер навивки шпулей
(«намот») стоял в таблице последним, поэтому «намотало полотно на вытяжной вал»
становилось ярлыком «навивка/намотка шпулей». Навивка по правилу участка идёт
при РАБОТАЮЩЕЙ машине (её часовой запас навивают на ходу), и 1,17 ч останова
исчезали из простоя: в Excel у 98-й стояло 1,43 ч «замена шпулярника», а
названная мастером причина в файле не появлялась вовсе.

Разница смысловая, и она же — проверка на перебор: «намотала четыре челнока
ровно по счётчику» (ст.111 за 21.09) — это навивка, а «намотало полотно на
вытяжной вал» — останов. Поэтому новая категория требует предмет, вокруг
которого всё намоталось: вал, полотно или шнур.

Сырьё — оплаченный разбор 26.09.2026 по станку №98
(tests/ai_raw_26_09_2026_98namotka.json), а не живая переписка: проверять нужно
правило и арифметику, а не повторный прогон ИИ.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (                    # noqa: E402
    SHAFT_WRAP_LABEL,
    WINDING_LABEL,
    build_shift_reports_from_structured,
    classify_stop,
    machine_stopped_for,
)
from services.excel_export import _num_str, _stops_columns   # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "ai_raw_26_09_2026_98namotka.json")


def _r(num="98"):
    with open(FIXTURE, encoding="utf-8") as f:
        reports = build_shift_reports_from_structured(json.load(f))
    for r in reports:
        if str(r.machine) == num:
            return r
    raise AssertionError(f"нет станка {num} в фикстуре {FIXTURE}")


def test_wrap_on_shaft_is_its_own_category():
    """«Намотало полотно на вытяжной вал» — категория останова, не навивка."""
    reason = "намотало полотно на вытяжной вал"
    label = classify_stop(reason)[0]
    assert label == SHAFT_WRAP_LABEL, label
    assert machine_stopped_for(reason, label) is True
    assert label not in WINDING_LABEL
    print(f"✅ намотка на вал: {label}, станок стоит")


def test_bobbin_winding_without_a_shaft_stays_parallel():
    """Правило 25.09 не сломано: навивка запаса по-прежнему не простой."""
    for reason in ("навито 170 шпулей",
                   "4й намоточный шпиндель в работе и налаже, намотал четыре "
                   "челнока ровно по счётчику",
                   "навивка шпулей в течение смены"):
        label = classify_stop(reason)[0]
        assert label == WINDING_LABEL, (reason, label)
        assert machine_stopped_for(reason, label) is False, (reason, label)


def test_report_counts_the_wrap_window_as_downtime():
    """В отчёте 26.09 по 98-й этот час стоит простоем станка."""
    r = _r()
    rows = {lab: h for lab, h, _a in r.downtime_by_reason()}
    assert rows.get(SHAFT_WRAP_LABEL) == 1.17, rows
    stops = [s for s in r.tech_stops if s.stop_label == SHAFT_WRAP_LABEL]
    assert len(stops) == 1, stops
    assert stops[0].machine_stopped is True
    assert stops[0].hours == 1.17, stops[0].hours
    print(f"✅ простой 98-й: {rows}")


def test_excel_shows_the_wrap_hours_and_reason():
    """В Excel видно и часы, и причину словами мастера, а не «прочее»."""
    hours, reasons = _stops_columns(_r())
    # Одна строка простоя возвращается числом, несколько — строкой «1,17+0,42».
    cell = hours if isinstance(hours, str) else _num_str(hours)
    assert "1,17" in cell, hours
    assert SHAFT_WRAP_LABEL in reasons, reasons
    assert WINDING_LABEL not in reasons, reasons
    print(f"✅ Excel 98-й: {cell} | {reasons}")


def test_wrap_survives_a_drifted_parse():
    """Повторный прогон ИИ не должен прятать останов назад в параллельную работу.

    Оплаченный разбор 26.09 назвал эту запись «остановка», но на других сменах
    модель уже путалась: ту же строку мастера она возвращала как kind «навивка»
    или «работа» (ср. tests/ai_raw_24_09_2026_drift.json). Флаг параллельности
    ставится по kind, и без проверки причины 1,17 ч снова вышли бы из простоя.
    """
    with open(FIXTURE, encoding="utf-8") as f:
        base = json.load(f)
    for kind in ("навивка", "работа", "остановка"):
        raw = json.loads(json.dumps(base))
        ev = raw["machines"][0]["events"][0]
        assert "намотало полотно" in ev["reason"], ev
        ev["kind"] = kind
        r = build_shift_reports_from_structured(raw)[0]
        rows = {lab: h for lab, h, _a in r.downtime_by_reason()}
        assert rows.get(SHAFT_WRAP_LABEL) == 1.17, (kind, rows)
