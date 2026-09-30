# -*- coding: utf-8 -*-
"""Колонка «подмены, ч» — как её считает владелец в своём файле (27.09.2026).

Эталон — «Для отчета клода.xlsx» (лист 27.09.2026), который он свёл руками по
той же переписке. Четыре станка дали четыре разных прочтения одного слова
«подмена», и ни одно из них не выводилось из прежних правил:

1. ст.98 «12:20-13:10 подмена» — 0,83. Окно задевает обед (12:00–12:45) только
   краем, и часы мастера остаются целыми; раньше код вырезал 0,75 и печатал 0,42;
2. ст.321 «7:10-10:10 Скворцов на подмену шпуль» — 3,33, а пять записей «замена
   катушки на шпулярнике» у него лежат в технических остановках строкой
   «шпулярник». Сумма тех и других (4,88) в клетку не попадает;
3. ст.251 «Подмена на 251» 07:08 — «Подмену закончил. 64 шп.» 08:07 — 1,1 при
   том, что станок в эти минуты не стоял: подменщик работает ПАРАЛЛЕЛЬНО;
4. ст.111 — клетка ПУСТАЯ, хотя в теме есть «16:29 Стоп на подмену. Сошла шпуля
   №7» и сводка «Подмена 5 из 15». Это партия шпуль, а не человек у машины.

Отсюда правило: в колонку идёт окно, которое САМ мастер назвал подменой
(kind «подмена»), за вычетом тех, где подменой назван сход шпули или работа
наладки. Обед вырезаем только когда окно накрыло перерыв целиком. Цифра модели
в «subs_hours» основанием не служит: арифметику считает код.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (                     # noqa: E402
    build_shift_reports_from_structured,
    is_spool_drop_text,
    is_submen_window,
    submen_hours,
)
from services.excel_export import _subs_fio, _subs_hours_cell      # noqa: E402


def _rep(machine: str, operator: str, events, *, counter=None,
         subs_hours=None, subs_count=None):
    """Отчёт одного станка из синтетического разбора."""
    machine_dict = {"machine": machine, "operator": operator,
                    "date": "27.09.2026",
                    "operator_shift_end": "19:00",
                    "subs_hours": subs_hours, "subs_count": subs_count,
                    "events": events}
    if counter:
        machine_dict["lines"] = [{"assortment": "днпа 187х3",
                                  "counter_start": counter[0],
                                  "counter_end": counter[1]}]
    return build_shift_reports_from_structured(
        {"date": "27.09.2026", "machines": [machine_dict],
         "warnings": []})[0]


def _ev(start, end, kind, reason, performer=None, kept_running=None):
    return {"start": start, "end": end, "kind": kind, "reason": reason,
            "performer": performer, "other_machine": None,
            "machine_kept_running": kept_running}


def test_okno_nazvannoe_podmenoy_idet_v_kolonku():
    """Мастер назвал окно подменой — его часы и есть колонка I."""
    r = _rep("98", "Симонова",
             [_ev("12:20", "13:10", "подмена", "12:20-13:10 подмена")],
             counter=(21563900, 21574050))
    assert abs(submen_hours(r) - 0.83) < 0.01, submen_hours(r)


def test_obed_vyrezayetsya_tolko_kogda_okno_nakrylo_pereryv():
    """Окно, накрывшее обед целиком, теряет 0,75; задетое краем — нет.

    22.09.2026, ст.111: «10:01–15:01 подмена шпулей» → в его файле 4,25.
    27.09.2026, ст.98: «12:20-13:10 подмена» → 0,83, обед не вырезан.
    """
    whole = _rep("111", "Арташина",
                 [_ev("10:01", "15:01", "подмена", "подмена шпулей",
                      performer="Сеньков")],
                 counter=(228935, 234542))
    edge = _rep("98", "Симонова",
                [_ev("12:20", "13:10", "подмена", "12:20-13:10 подмена")],
                counter=(21563900, 21574050))
    assert abs(submen_hours(whole) - 4.25) < 0.01, submen_hours(whole)
    assert abs(submen_hours(edge) - 0.83) < 0.01, submen_hours(edge)


def test_okna_podmen_ne_summiruyutsya_po_ljudjam():
    """Час окна, а не сумма по людям: двое на одном окне дают одно окно."""
    r = _rep("321", "Демидов",
             [_ev("07:10", "10:10", "подмена", "Скворцов на подмену шпуль",
                  performer="Скворцов"),
              _ev("07:10", "10:10", "подмена", "Каленюк на подмену",
                  performer="Каленюк")],
             counter=(125370, 126174))
    assert abs(submen_hours(r) - 3.0) < 0.01, submen_hours(r)


def test_spul_drop_ne_podmena():
    """«Стоп на подмену. Сошла шпуля №7» — партия шпуль, не человек."""
    assert is_spool_drop_text("Стоп на подмену. Сошла шпуля №7. "
                              "Продолжаю навивку шпулей")
    assert is_spool_drop_text("стоп, бобина слетела со шпильки")
    assert not is_spool_drop_text("Скворцов на подмену шпуль")
    r = _rep("111", "Арташина",
             [_ev("16:29", "16:56", "подмена",
                  "Стоп на подмену. Сошла шпуля №7. Продолжаю навивку шпулей")],
             counter=(228935, 234542), subs_hours=1.37, subs_count=6)
    assert submen_hours(r) == 0.0, submen_hours(r)
    assert _subs_hours_cell(r) == "", _subs_hours_cell(r)


def test_kletka_ne_beret_cifru_ii():
    """Клетка = окно по правилу, а не часовой итог модели.

    Модель за смену пишет «подмены_часы» сама, и её цифра расходится с окнами:
    ст.321 за 27.09 — 4,88 против его 3,33. Правило считает submen_hours, оба
    выгрузчика читают его, поэтому клетка не может оказаться цифрой ИИ.
    """
    r = _rep("98", "Симонова",
             [_ev("12:20", "13:10", "подмена", "12:20-13:10 подмена")],
             counter=(21563900, 21574050), subs_hours=5.0, subs_count=6)
    assert abs(submen_hours(r) - 0.83) < 0.01, submen_hours(r)
    assert _subs_hours_cell(r) == submen_hours(r), _subs_hours_cell(r)


def test_naladka_narazmetku_podmenoy_ne_schitaem():
    """Окно наладки не становится подменой, даже если мастер назвал его так."""
    r = _rep("111", "Арташина",
             [_ev("09:05", "09:35", "подмена",
                  "Пропуск узлов после подмены, наладка",
                  performer="Шалыминов")],
             counter=(228935, 234542))
    stops = [s for s in r.tech_stops if s.start_min is not None]
    assert stops and not any(is_submen_window(s, r.operator) for s in stops)


def test_stoyashaya_mashina_svoego_operatora_etos_podmena():
    """Подмена своего оператора на вставшей машине — это колонка I (ст.98)."""
    r = _rep("98", "Симонова",
             [_ev("12:20", "13:10", "подмена", "подмена шпуль")],
             counter=(21563900, 21574050))
    stop = next(s for s in r.tech_stops if s.start_min == 740)
    assert is_submen_window(stop, r.operator)


def test_parallelnaya_podmena_chuzhogo_cheloveka_stanok_idet():
    """Чужой человек на подмене — часы идут, даже когда станок не стоит.

    27.09.2026, ст.251: «Подмена на 251» (07:08) — «Подмену закончил. 64 шп.»
    (08:07), Петровский. В его файле 1,1 ч.
    """
    r = _rep("251", "Гудкова",
             [_ev("07:08", "08:07", "подмена", "Подмена на 251",
                  performer="Петровский", kept_running=True)],
             counter=(303370, 304335))
    assert abs(submen_hours(r) - 0.98) < 0.01, submen_hours(r)


# ——— сверка с настоящим разбором 27.09.2026 (tests/ai_raw_27_09_2026.json) ———

def _real(machine: str):
    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ai_raw_27_09_2026.json")
    with open(fixture, encoding="utf-8") as f:
        data = json.load(f)
    for r in build_shift_reports_from_structured(data):
        if str(r.machine) == machine:
            return r
    raise AssertionError(f"нет станка {machine} за 27.09.2026")


def test_chetyre_stanka_27_09_kak_v_ego_fajle():
    """Его ручные клетки I по четырём станкам 27.09 воспроизведены.

    98 — 0,83; 321 — 3,33; 251 — 1,1; 111 — пусто. До правила клетка 98 была
    0,42 (вырезанный «призрачный» обед), 321 — 4,88 (шпулярник в подменах),
    251 — пустой, 111 — 1,37 (партия шпуль).
    """
    got = {m: _subs_hours_cell(_real(m)) for m in ("98", "111", "251", "321")}
    assert got["98"] == 0.83, got
    assert got["111"] == "", got
    assert abs(got["251"] - 1.12) < 0.03, got
    assert got["321"] == 3.33, got


def test_fio_podmen_sochinyaet_nazvannyh_ljudj():
    """Оператор в «фио на подменах» НЕ вытесняет названного подменщика.

    Клетка J собиралась из submen_names, а r.subs_fio использовался только
    «если список пуст». Как только часы окна посчитались (ст.251 за 27.09),
    в список попал оператор, и Петровский из клетки исчез.
    """
    real = _real("251")
    assert "Петровский" in _subs_fio(real), _subs_fio(real)
    synth = _rep("251", "Гудкова",
                 [_ev("07:08", "08:07", "подмена", "Подмена на 251",
                      performer=None, kept_running=True)],
                 counter=(303370, 304335))
    setattr(synth, "subs_fio", "Петровский")
    assert _subs_fio(synth) == "Гудкова Петровский", _subs_fio(synth)


def test_zapasnogo_puta_subs_hours_bolshe_net():
    """Ни один станок корпуса не получает часы в I, если правило не узнало окно.

    Пробег 29.09.2026 по всему корпусу (data/shifts/*/ai_raw.json и
    tests/ai_raw_*.json) дал ровно два таких места, и оба — не подмены:
    10.09.2026 ст.251 (9,42 ч из «работа стажёра: мониторинг навивки шпулей,
    стоп и пуск машин» при идущем станке) и 24.09.2026 ст.321 (0,35 ч «меняю на
    шпулярнике…» — оператор у своей машины). Запасной путь печатал то, что
    правило отвергло, поэтому он убран и возврата быть не должно.
    """
    def _cell(path, machine):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), path),
                  encoding="utf-8") as f:
            data = json.load(f)
        for r in build_shift_reports_from_structured(data):
            if str(r.machine) == machine:
                assert r.subs_window_hours <= 0.01, (path, machine,
                                                     r.subs_window_hours)
                return _subs_hours_cell(r), r.subs_hours
        raise AssertionError(f"нет станка {machine} в {path}")

    for path, machine in (("ai_raw_10_09_2026.json", "251"),
                          ("ai_raw_24_09_2026.json", "321"),
                          ("ai_raw_24_09_2026_drift.json", "321")):
        cell, ai_hours = _cell(path, machine)
        assert cell == "", (path, machine, cell, ai_hours)

