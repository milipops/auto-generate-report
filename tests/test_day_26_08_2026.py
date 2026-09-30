# -*- coding: utf-8 -*-
"""Регресс на реальный день 26.08.2026 (фикстура tests/day_26_08_2026.json).

Ловит три ошибки ИИ-разбора, которые всплыли на живом прогоне:
  1) оператор всю смену на другом станке (321: Камбаров весь день на 251) НЕ
     должен давать ложные операторские часы на своём станке — только отсутствие;
  2) параллельные подмены помощников (Сенцов/Скворцов на 151) должны попадать
     в привлечённый персонал;
  3) оператор соседнего станка (Камбаров), пришедший на подмену, не должен
     задваиваться в привлечённых.
Числа сверены с ручным эталоном пользователя (лист 26.08).
"""
import json
import os

from services.shift_report import build_shift_reports_from_structured

_HERE = os.path.dirname(__file__)


def _load():
    with open(os.path.join(_HERE, "day_26_08_2026.json"), encoding="utf-8") as f:
        data = json.load(f)
    reps = {r.machine: r for r in build_shift_reports_from_structured(data)}
    return reps


def _attr(r):
    lst = r.attracted_all() if callable(getattr(r, "attracted_all", None)) \
        else r.attracted_all
    return [(p.performer, round(p.hours, 2)) for p in lst]


def _surnames(attr):
    return [str(p or "").split()[0] for p, _ in attr]


def test_machine_hours_by_counter():
    """Часы станка = дельта счётчика / норма / 60 — проверяем ФОРМУЛУ.

    Ожидаемые часы пересчитываем из справочника норм, а не хардкодим: нормы
    выработки участка уточняются со временем. За 26.08 станок 151 отработал
    9,8 ч при норме 10,4 (6119 узлов), а в актуальном файле пользователя
    «выгрузка за 15 число моя.xlsx» (лист 15.09.2026, колонка T) для №151
    стоит уже 10,9 — при ней те же 6119 узлов дают 9,36 ч. Тест следит за
    формулой и за тем, что часы не «взрываются», конкретное число берёт из
    machine_rates.json.
    """
    from services import machine_rates

    r = _load()
    for num in ("151", "251", "321", "98"):
        rep = r[num]
        rate = machine_rates.get_rate(num)
        assert rate, f"нет нормы для станка {num}"
        want = sum(
            (ln.counter_end - ln.counter_start) / rate / 60
            for ln in rep.lines
            if ln.counter_start is not None and ln.counter_end is not None
            and ln.counter_end > ln.counter_start)
        assert abs(rep.work_hours - want) < 0.02, (num, rep.work_hours, want)
        assert rep.work_hours <= 12.01, f"часы станции больше смены: {num}"
    print("✅ часы машины по счётчику: формула (дельта/норма/60) соблюдена, "
          "значения из справочника норм")


def test_321_operator_all_shift_elsewhere_no_false_hours():
    """Камбаров весь день на 251 → на 321 отсутствие, а не 11.5 ч работы."""
    r = _load()["321"]
    assert r.operator_hours < 3.0, ("ложные операторские часы на 321: "
                                    f"{r.operator_hours}")
    labels = [l for l, _, _ in r.downtime_by_reason()]
    assert any("работа на №251" in l for l in labels), labels
    print(f"✅ 321: оператор {r.operator_hours} ч, отсутствие «работа на №251»")


def test_151_attracted_includes_helpers_no_double():
    """Сенцов и Скворцов — в привлечённых; Камбаров ровно один раз."""
    r = _load()["151"]
    attr = _attr(r)
    surn = _surnames(attr)
    assert "Сенцов" in surn, attr
    assert "Скворцов" in surn, attr
    assert surn.count("Камбаров") == 1, ("Камбаров задвоен в привлечённых: "
                                         f"{attr}")
    print(f"✅ 151 привлечённые (без задвоения): {attr}")


def test_251_operator_hours_as_data_give():
    """251 за 26.08: часы оператора = его записи + время работающего станка.

    1,0 ч на №321 (правило 27.08: помощь на другом станке идёт в его часы) +
    6,93 ч, что станок 251 реально крутился = 7,93 ч. Остаток до нормы 11,25
    (3,32 ч) отчёт теперь показывает недостатком времени: приказ владельца от
    28.09.2026 — «не нужно их добивать до 11,25, если не хватает или переизбыток
    то оставляй как есть». В ручном листе мастера этот остаток был закрыт
    строкой «навивка/контроль» — её в переписке нет.
    """
    r = _load()["251"]
    assert abs(r.operator_hours - 7.93) < 0.02, r.operator_hours
    assert abs(r.operator_shortfall - 3.32) < 0.02, r.operator_shortfall
    print(f"✅ 251: оператор {r.operator_hours} ч, недостаток "
          f"{r.operator_shortfall} ч как есть")


def test_sheet2_excludes_naladchik_from_subs():
    """Лист-таблица: наладчик НЕ в подменах (эталон 151 = 1.84), наладка — в
    простое. Первый лист (attracted_all) наладчика сохраняет."""
    from services.excel_export import _subs_fio
    r = _load()["151"]
    assert abs(r.attracted_subs_total - 1.84) < 0.02, r.attracted_subs_total
    assert "Шалыминов" not in _subs_fio(r), _subs_fio(r)
    # наладчик по-прежнему виден в детальном первом листе
    detail = " ".join(x for x, _ in r.attracted_by_person())
    assert "Шалыминов" in detail, detail
    # его наладка присутствует в простоях машины
    labels = [l for l, _, _ in r.downtime_by_reason()]
    assert any("наладк" in l.lower() for l in labels), labels
    print("✅ лист-таблица 151: подмены 1.84 без наладчика; наладка в простое; "
          "в «Пояснении» наладчик есть")



if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nВсе проверки 26.08 пройдены.")
