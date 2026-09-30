# -*- coding: utf-8 -*-
"""Колонка P «Доп. работы (шт, кг)» — слова мастера о делах вне своей машины.

Приказ владельца 29.09.2026: ответ на вопрос №4 «какие поля бланка нужны и в
файле для 1С» — «да», то есть нужны «Доп. работы (шт, кг)», «фио на подменах»,
«Кол-во подмен», «Кол-во срезов». Три последних в выгрузке уже лежали; P была
пустой клеткой во всех файлах: `excel_export` печатал в неё «», хотя шапку
колонки носил.

Эталон — лист 27.09.2026 файла владельца (Книга1.xlsx), станок 98:
«замена шпуляриника в течении смены + 26 машина в подработке». Это ЕГО слова о
работе НЕ на своей машине (чужой номер или подработка). Часы при этом стоят
там же, где считались, — в блоке оператора: колонка описывает дело, а не
перекладывает время.

Поэтому правило читает строки блока оператора и берёт только дословные фразы
мастера. Пересказ нашего кода («работа по контролю за работой машины»,
«подмена шпулярника (станок стоит) (4 раз)», «отсутствие оператора (ушёл на
станок 321)») в колонку не идёт: это ярлыки событий, а не названная работа.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import onec_export                                # noqa: E402
from services.excel_export import export_shift_reports          # noqa: E402
from services.shift_report import (                             # noqa: E402
    build_shift_reports_from_structured,
    extra_works,
    extra_works_text,
)

FIXTURE_2609 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ai_raw_26_09_2026_98namotka.json")


class _Report:
    """Заглушка отчёта: только номер станка и строки блока оператора."""

    def __init__(self, machine, rows):
        self.machine = machine
        self._rows = rows

    def operator_work_by_reason(self):
        return list(self._rows)


def _p(machine, *rows):
    return extra_works(_Report(machine, [(t, 1.0) for t in rows]))


# ——— 1. что попадает в колонку ———

def test_foreign_machine_work_goes_into_p():
    """Чужой номер машины — это доп. работа, и дословно мастера."""
    assert _p("151", "работа на машине №251 (замена 64 шпуль)") == [
        "работа на машине №251 (замена 64 шпуль)"]
    assert _p("111", "Перешла в работу на 151 машину — заправка нити") == [
        "Перешла в работу на 151 машину — заправка нити"]
    # Номер не обязан быть в парке: «26» и «78» станки соседнего участка.
    assert _p("98", "Намотало полотно на вытяжной вал. Работаю на 26 машине")


def test_podrabotka_goes_into_p():
    """Слово «подработка» — доп. работа даже без номера."""
    assert _p("98", "подработка-подмена на 111 маш., заменено 125 шп") == [
        "подработка-подмена на 111 маш., заменено 125 шп"]


def test_beam_work_with_quantity_goes_into_p():
    """Дело на шпулярнике с количеством — те самые «шт» из шапки колонки."""
    assert _p("251", "подставляла шпулярник, 205 шт.") == [
        "подставляла шпулярник, 205 шт."]
    assert _p("321", "подмена шпулярника: оператор привязывал 115 бобин")


def test_bare_park_number_goes_into_p():
    """Голый номер станка из парка без слова «машина» — тоже чужая машина.

    Реальные строки архива: 25.09.2026 ст.321 и 15.09.2026 ст.251.
    """
    assert _p("321", "ушел на 251 смотреть ошибку, восстановил обрыв путем "
                     "подстановки шпули на 111")
    assert _p("251", "приглашена на 321 по вопросу навивки шпулей")
    assert _p("251", "вернулась с 321, машина все это время в работе")
    # Номер вне парка без слова «машина» — не станок (это количество).
    assert _p("321", "навил 200 из 250") == []

# ——— 2. что в колонку НЕ попадает ———

def test_control_row_stays_out_of_p():
    """Контроль за машиной — ярлык нашего кода, хоть и с шпулярником внутри."""
    assert _p("321", "работа по контролю за работой машины (в т.ч. довила 90 "
                     "шпуль, замена шпулярника в течение всей смены — время "
                     "не указано)") == []


def test_merged_labels_stay_out_of_p():
    """Склейка «(N раз)» — пересказ событий, а не названная работа."""
    assert _p("321", "подмена шпулярника (станок стоит) (4 раз)") == []
    assert _p("98", "навивка/намотка шпулей (2 раз)") == []
    assert _p("98", "подмена (заправка полотна) (2 раз)") == []


def test_absence_stays_out_of_p():
    """Отсутствие оператора — не работа ЭТОГО станка: его часы на том станке."""
    assert _p("251", "отсутствие оператора (ушёл на станок 321) (2 раз)") == []
    assert _p("251", "отсутствие оператора (ушёл на станок 321)") == []


def test_time_is_not_a_machine_number():
    """«7.00 машина в работе» — это время, а не станок №7."""
    assert _p("98", "7.00 машина в работе, развито 160 шпулей, "
                    "навито 160 шпулей") == []


def test_own_machine_number_is_not_extra_work():
    """Свой номер — пересказ состояния своей машины, а не чужая работа."""
    assert _p("151", "151м в работе") == []
    # Тот же текст на другом станке — уже чужой номер.
    assert _p("121", "культура производства около 121 м; 151м в работе")


def test_beam_defect_is_not_a_job():
    """«Сход нити со шпулярника» — причина дефекта, а не работа на нём."""
    assert _p("251", "выявлено несоответствие в провязе (причина — сход нити "
                     "со шпулярника), чинка полотна") == []


def test_counter_digits_are_not_machines():
    """Разрядный счётчик не прячет в себе станок: «21 601 000» — одно число."""
    assert _p("321", "общий счёт 21 601 000, срез 2 из 3") == []
    assert _p("251", "Общий счёт: 229 273 Средний счёт: 320 из 2 799") == []


def test_quantity_time_and_share_are_not_machines():
    """98 шпуль, 1,98 кг и «в 91.05» — количество, доля и время, не станки."""
    assert _p("321", "навила 98 шпуль") == []
    assert _p("321", "вес брака 1,98 кг") == []
    assert _p("321", "стоп в 91.05, пуск в 12:30") == []


# ——— 3. форма клетки ———

def test_rows_join_with_plus():
    """В файле владельца работы перечислены через « + » одной строкой."""
    r = _Report("98", [("замена шпулярника в течении смены", 4.8),
                       ("26 машина в подработке", 0.4)])
    assert extra_works_text(r) == ("замена шпулярника в течении смены"
                                   " + 26 машина в подработке")


def test_same_words_once():
    """Одна и та же фраза мастера не удваивается в клетке."""
    assert _p("98", "замена шпулярника, 26 машина в подработке",
              "замена шпулярника, 26 машина в подработке") == [
        "замена шпулярника, 26 машина в подработке"]


# ——— 4. эталонный разбор и файлы ———

def _report_2609():
    with open(FIXTURE_2609, encoding="utf-8") as f:
        reports = build_shift_reports_from_structured(json.load(f))
    for r in reports:
        if str(r.machine) == "98":
            return r
    raise AssertionError("нет станка 98 в фикстуре")


def test_etalon_words_of_real_shift():
    """26.09.2026 ст.98: колонка = слова мастера, часы блока не тронуты."""
    r = _report_2609()
    assert extra_works(r) == ["замена шпулярника, 26 машина в подработке"]
    rows = dict(r.operator_work_by_reason())
    assert rows["замена шпулярника, 26 машина в подработке"] == 8.23


DATA = {"date": "30.09.2026", "warnings": [], "machines": [
    {"machine": "98", "operator": "Симонова", "cuts": 7, "subs_count": 1,
     "operator_shift_end": "19:00",
     "lines": [{"assortment": "187х3-51-250,5 зел", "client": "СКЛАД",
                "counter_start": 21520000, "counter_end": 21530950}],
     "events": [
         {"start": "07:00", "end": "15:18", "kind": "работа",
          "reason": "замена шпулярника в течении смены / 26 машина в "
                    "подработке"},
         {"start": "15:18", "end": "19:00", "kind": "подмена",
          "reason": "подмена (заправка полотна)"}]},
    {"machine": "111", "operator": "Гудкова", "cuts": 2, "subs_count": 0,
     "operator_shift_end": "19:00",
     "lines": [{"assortment": "нпа 187х2", "counter_start": 217000,
                "counter_end": 217622}],
     "events": [{"start": "08:00", "end": "18:00", "kind": "работа",
                 "reason": "машина в работе"}]},
]}


def _reports():
    return build_shift_reports_from_structured(json.loads(json.dumps(DATA)))


def _table_rows(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        if ws["A2"].value == "Фамилия":
            break
    else:
        raise AssertionError("нет листа-таблицы «Книга1»")
    return [{c.column_letter: c.value for c in row
             if c.value not in (None, "")}
            for row in ws.iter_rows(min_row=3, max_row=ws.max_row)]


def test_excel_column_p_is_filled():
    """Клетка P стоит на первой строке станка; у станка без работ — пусто."""
    reports = _reports()
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "otchet.xlsx")
    export_shift_reports(reports, path, date="30.09.2026")
    rows = _table_rows(path)
    by_machine = {str(r.get("B")): r for r in rows if r.get("B")}
    assert by_machine["98"]["P"] == ("замена шпулярника в течении смены"
                                     " / 26 машина в подработке")
    assert "P" not in by_machine["111"]


def test_onec_json_has_extra_works_key():
    """В файл для 1С колонка уезжает списком слов мастера."""
    doc = onec_export.build_document(_reports(), date="30.09.2026")
    by_machine = {str(st["станок"]): st for st in doc["станки"]}
    assert by_machine["98"]["доп_работы"] == [
        "замена шпулярника в течении смены / 26 машина в подработке"]
    assert by_machine["111"]["доп_работы"] == []
    # Остальные три поля приказа №4 в выгрузке были и до правки.
    assert by_machine["98"]["кол_срезов"] == 7
    # «Кол-во подмен» не заполняется: приказ владельца 30.09.2026 — «не заполняй
    # колонку» (в его файле за 29.09 клетка пуста у всех десяти станков).
    assert by_machine["98"]["привлечённый_персонал"]["кол_подмен"] is None
    assert by_machine["98"]["привлечённый_персонал"]["фио_на_подменах"]


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("все проверки колонки P прошли")
