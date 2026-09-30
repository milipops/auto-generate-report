# -*- coding: utf-8 -*-
"""Excel и выгрузка для 1С обязаны показывать ОДИНАКОВЫЕ числа одного дня.

Правило проекта: «ИИ достаёт структуру, арифметику считает код». Когда одно и
то же правило живут в двух выгрузках по-своему, человек видит расхождение там,
где его нет, или наоборот — и начинает сверять файлы вместо данных.

Что проверяем по каждой машине зафиксированных разборов (без сети и без ИИ):

1. Строка «!!! РАСХОЖДЕНИЕ» — слово в слово одна и та же.
   Разница была: 1С не знала правила 03.09.2026 (переработка оператора,
   выросшая из времени работы станка по счётчику, расхождением НЕ считается) и
   пугала «+0,69 ч», а бланк в это время молчал.
2. «фио на подменах» — один и тот же список людей.
   Excel с 22.09.2026 перечисляет и своего оператора (ст.98 «Белякова»), а в
   файл для 1С уезжала пустая клетка при той же смене.
3. Часы подмен — то же ОКНО простоя станка, что и в колонке «подмены, ч»
   бланка, а не сумма часов по людям (трое на одном окне дают 17,46 ч при
   смене 12 ч).

Общее основание теперь одно: shift_report.submen_hours / submen_fio /
discrepancy_flags. Тест следит, чтобы никто снова не завёл второго.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import build_shift_reports_from_structured  # noqa: E402
from services import onec_export                                       # noqa: E402
from services.excel_export import (_discrepancy_text, _subs_fio,       # noqa: E402
                                   _subs_hours_cell)

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = ("ai_raw_10_09_2026.json", "ai_raw_21_09_2026.json",
            "ai_raw_22_09_2026.json", "ai_raw_24_09_2026.json")


def _reports():
    """Каждая машина каждого зафиксированного дня: (день, номер, отчёт)."""
    for name in FIXTURES:
        with open(os.path.join(HERE, name), encoding="utf-8") as f:
            day = name.replace("ai_raw_", "").replace(".json", "")
            for r in build_shift_reports_from_structured(json.load(f)):
                yield day, str(r.machine), r


def test_discrepancy_line_is_the_same_in_both_files():
    for day, num, r in _reports():
        excel = _discrepancy_text(r)
        onec = onec_export.machine_dict(r)["расхождение"]
        assert excel == onec, (day, num, excel, onec)
    print("✅ строка расхождения в Excel и в 1С совпадает")


def test_submen_names_are_the_same_in_both_files():
    for day, num, r in _reports():
        excel = _subs_fio(r)
        onec = onec_export.machine_dict(r)["привлечённый_персонал"]["фио_на_подменах"]
        assert excel == onec, (day, num, excel, onec)
    print("✅ «фио на подменах» в Excel и в 1С совпадает")


def test_submen_hours_are_the_machine_window_in_both_files():
    for day, num, r in _reports():
        excel = _subs_hours_cell(r)
        onec = onec_export.machine_dict(r)["привлечённый_персонал"]
        window = onec["подмены_окно_станка_часы"]
        if excel in ("", None):
            assert not window, (day, num, excel, window)
        else:
            assert float(excel) == window, (day, num, excel, window)
    print("✅ часы подмен — окно простоя станка в обеих выгрузках")


def test_attracted_person_hours_sum_is_not_larger_than_shift_per_person():
    """Сумма по людям остаётся отдельным числом и не подменяет окно станка.

    Оба числа нужны: «подмены_часы» — сколько отработали люди, «окно» — сколько
    стоял станок. путать их нельзя (прогон 22.09.2026, ст.151).
    """
    for day, num, r in _reports():
        onec = onec_export.machine_dict(r)["привлечённый_персонал"]
        window = onec["подмены_окно_станка_часы"] or 0.0
        assert window <= 12.0 + 0.01, (day, num, window)
    print("✅ окно подмен станка не больше смены")


if __name__ == "__main__":
    test_discrepancy_line_is_the_same_in_both_files()
    test_submen_names_are_the_same_in_both_files()
    test_submen_hours_are_the_machine_window_in_both_files()
    test_attracted_person_hours_sum_is_not_larger_than_shift_per_person()
