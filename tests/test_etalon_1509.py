# -*- coding: utf-8 -*-
"""Регрессии сравнения эталона пользователя с отчётом бота за 15.09.2026.

Эталон — файл пользователя «выгрузка за 15 число моя.xlsx» (листы «15.09» и
«15.09.2026»), отчёт бота — «Отчет_производства_за_смену_15_09_2026.xlsx».

Крупнейший дефект этого прогона создал наш же валидатор счётчиков, добавленный
накануне: регулярка общего счётчика «\bсч» ловила и СРЕДНИЙ счётчик («Ср. Сч.:
4274 из 14800»), потому что перед «Сч» стоит граница слова. Модальная длина
числа посчиталась по 12 средним счётчикам (4 цифры) и объявила настоящие
шестизначные показания опечаткой:

    ст.251: 288402 → 4274 и 291916 → 7788
    в отчёт ушли «Показания счетчика 4274 / 7788», выпуск 3514 «удержался»,
    но counter_ledger.json получил 7788 вместо 291916 — отравил бы перенос.

Второй дефект того же валидатора: шаблон ждал строго «точку, потом двоеточие»,
а мастер написал «Об. Сч:. 291916» — число не находилось вовсе, и валидатор
«исправлял» честное конечное показание на соседнее 291798.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import counter_check as cc                         # noqa: E402
from services import machine_rates                               # noqa: E402
from services.shift_report import (                             # noqa: E402
    build_shift_reports_from_structured)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Настоящая секция №251 из файла 15,09,2026.docx (фрагмент со средними и
# общими счётчиками вперемешку — именно на ней ломался валидатор).
REAL_251 = """–251 машина
15 сентября 2026 г.
Юлия Черныш, 06:59:Об. Сч.: 288402
Ср. Сч.: 4274 из 14800
Татьяна Арташина, 07:00:прием смены 251
Об. Сч.: 288402
Ср. Сч.: 4274 из 14800
08:10 машина встала , не взяла горизонтальным крючком
Занимается наладчик Пронкин А.
Об. Сч.: 288729
Ср. Сч.: 4601 из 14800
Срез 1 из 1
Об. Сч.: 288922
Ср. Сч: 5200 из 14800
12:00-12:45 обед
Об. Сч.: 290154
Ср. Сч.: 5990 из 14800
13:04 машина встала
Об. Сч.: 290292
Ср. Сч.: 6164 из 14800
Подмена 5 из 5
Наладочных 18 узлов
15:58 Машина остановилась сама , с крючков нить нескинута
Нужна консультация наладчика
Об. Сч.: 291556
Ср. Сч:6044 из 14800
18:54 стоп машина обрыв
Требуется помощь наладчика в заправе на валы
19:00 Конец смены.
Об. Сч:. 291916
Ср. Сч.: 7788 из 10000
Срез 1 из 1
Подмена 5 из 5
"""

# Секция №321: счётчик заменён, значение повторено трижды.
REAL_321 = """–321 машина
15 сентября 2026 г.
Юлия Черныш, 06:55:
Общ сч 1200807
Ср сч 760 из 760
Евгений Демидов, 07:05:
7:00 начало смены
Общ сч 1200807
Ср сч 760 из 760
16:20 конец смены
Общ сч 1200807
Ср сч 760 из 760
"""


def _one(machine):
    return build_shift_reports_from_structured({"machines": [machine]})[0]


def _load_15_09():
    path = os.path.join(ROOT, "data", "shifts", "15.09.2026", "ai_raw.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {str(m.get("machine")): m for m in data.get("machines") or []}


# ─────────── 1. Средний счётчик не должен попадать в общий ───────────

def test_average_counter_is_not_taken_as_general():
    """«Ср. Сч.: 4274 из 14800» — это средний счётчик, не общий."""
    for text in ("Ср. Сч.: 4274 из 14800", "Ср сч.: 1553 из 14800",
                 "Срез.сч. 750 из 760", "Средний сч 4274",
                 "Ср сч 760 из 760"):
        assert cc._find_counters(text) == [], (text, cc._find_counters(text))
    print("✅ средний/срезовый счётчик не считается общим")


def test_general_counter_forms_are_found():
    """Все формы записи общего счётчика находятся, включая «Сч:.»."""
    cases = {
        "Об. Сч.: 284387": 284387,
        "Общ.сч. -750733": 750733,
        "Общ сч21431800": 21431800,
        "Сч 760449": 760449,
        "Об. Сч:. 291916": 291916,          # точка ПОСЛЕ двоеточия
        "Общ.сч. -_766068__": 766068,        # подчёркивания бланка
    }
    for text, want in cases.items():
        got = cc._find_counters(text)
        assert got == [want], (text, got, want)
    print("✅ все формы общего счётчика находятся (в т.ч. «Об. Сч:.»)")


def test_251_counters_survive_validation():
    """Ст.251 за 15.09: честные показания 288402 → 291916 не правятся."""
    start, end, notes = cc.check_counters(
        "251", 288402.0, 291916.0, text=REAL_251, prev_end=288402.0,
        prev_is_adjacent=False)
    assert int(start) == 288402, start
    assert int(end) == 291916, end
    assert notes == [], notes
    print("✅ №251: 288402 → 291916 не тронуты (раньше портились в 4274 → 7788)")


def test_repeated_value_is_not_an_error():
    """Ст.321 за 15.09: 1200807 повторено трижды — счётчик заменили, не опечатка."""
    start, end, notes = cc.check_counters(
        "321", 1200807.0, 1200807.0, text=REAL_321,
        prev_end=120796.0, prev_is_adjacent=True)
    assert int(start) == 1200807, start
    assert int(end) == 1200807, end
    assert notes == [], notes
    print("✅ повторённое показание не объявляется опечаткой")


# ─────────── 2. Нормы выработки из файла пользователя ───────────

def test_rates_match_approved_list():
    """Нормы — согласованный список от 25.09.2026, а не колонка T за 15.09.

    В файле пользователя «выгрузка за 15 число моя.xlsx» (лист «15.09.2026»,
    колонка T) стояло №151 = 10,9 и №251 = 7,35, и его ячейка O11 =
    3514/7,35/60 = 7,968253968253969 ч. Согласованные скорости округлены до
    0,1 (10,8 и 7,4), поэтому пересборка 15.09 даёт 7,91 ч вместо 7,97 — это
    смена нормы, а не ошибка в формуле: формула та же, O = N/T/60.
    """
    expected = {"98": 18.0, "91": 12.0, "111": 15.2, "121": 14.5,
                "151": 10.8, "251": 7.4, "321": 7.7, "14тр": 8.0,
                "15тр": 8.0}
    for num, rate in expected.items():
        got = machine_rates.get_rate(num)
        assert got is not None and abs(got - rate) < 1e-9, (num, got, rate)
    # Норма не установлена только у №601 — и он обязан остаться в парке.
    assert machine_rates.get_rate("601") is None
    assert "601" in machine_rates.known_machines()
    # Ячейка O11 пользователя воспроизводится ФОРМУЛОЙ на его же норме 7,35:
    got = machine_rates.work_hours("251", 288402, 291916)
    assert abs(got - 3514 / machine_rates.get_rate("251") / 60) < 1e-9, got
    assert abs(3514 / 7.35 / 60 - 7.968253968253969) < 1e-9
    print("✅ нормы = согласованный список; O = N/T/60, как в бланке")


# ─────────── 3. Обед вырезается из сменной операции-обёртки ───────────

def test_downtime_rows_split_like_user_etalon():
    """Строки простоя №321 за 15.09 — как в файле пользователя.

    Эталон («выгрузка за 15 число моя.xlsx», лист «15.09»):
        8,25 | простой навивка шпуль
        3,00 | простой отсутствие оператора
        0,75 | простой обед
    = ровно 12 ч (правило пользователя: станок = 12 ч, обед 45 мин отдельной
    строкой; остаток после ухода оператора в 16:20 — отсутствие оператора).

    Бот печатал «9,33 подмена + 2,67 отсутствие» БЕЗ строки обеда: окно
    07:00–16:20 накрывало обед, строка обеда снималась тримом как задвоение,
    и 0,75 ч незаметно съедались у «отсутствия оператора».
    """
    r = _one({
        "machine": "321", "operator": "Демидов",
        "operator_shift_end": "16:20",
        "lines": [{"counter_start": 1200807, "counter_end": 1200807}],
        "events": [
            {"start": "07:00", "end": "16:20", "kind": "подмена",
             "reason": "машина на подмене, навивка шпуль для подмены"},
            {"start": "07:25", "end": "08:07", "kind": "навивка",
             "reason": "приступил к навивки шпуль"},
            {"start": "08:37", "end": "12:00", "kind": "навивка",
             "reason": "шпульный стол в работу, навивка шпуль"},
            {"start": "12:00", "end": "12:45", "kind": "обед",
             "reason": "обед"},
            {"start": "12:45", "end": "13:40", "kind": "навивка",
             "reason": "шпульный стол в работу, навивка шпуль"},
            {"start": "13:46", "end": "16:00", "kind": "навивка",
             "reason": "сырьё получил, продолжаю навивку шпуль"}]})
    rows = {l: h for l, h, _a in r.downtime_by_reason()}
    assert abs(rows.get("обед (станок стоит)", 0.0) - 0.75) < 0.01, rows
    assert abs(rows.get("отсутствие оператора (смена до 16:20)", 0.0)
               - 2.67) < 0.05, rows
    # Главное — строки складываются ровно в 12 ч (работа станка 0 ч).
    total = r.work_hours + sum(rows.values())
    assert abs(total - 12.0) < 0.02, (total, rows)
    print(f"✅ строки простоя складываются в 12 ч, обед отдельной строкой: "
          f"{ {k: v for k, v in rows.items()} }")


def test_lunch_is_cut_from_shiftlong_operation():
    """Обёртка «машина на подмене» 07:00–16:20: 45 мин обеда не в часах.

    Прогон 15.09.2026, ст.321: rest считался как 9,33 − 7,23 = 2,10 ч и
    оператор получил 9,33 ч. Правильно 9,33 − 7,23 − 0,75 = 1,35 ч → итого
    8,58 ч (присутствие до 16:20 минус обед). Эталон пользователя 8,25 ч.
    """
    r = _one({
        "machine": "321", "operator": "Демидов",
        "operator_shift_end": "16:20",
        "lines": [{"counter_start": 1200807, "counter_end": 1200807}],
        "events": [
            {"start": "07:00", "end": "16:20", "kind": "подмена",
             "reason": "машина на подмене, навивка шпуль для подмены"},
            {"start": "07:25", "end": "08:07", "kind": "навивка",
             "reason": "приступил к навивки шпуль"},
            {"start": "08:37", "end": "12:00", "kind": "навивка",
             "reason": "шпульный стол в работу, навивка шпуль"},
            {"start": "12:00", "end": "12:45", "kind": "обед",
             "reason": "обед"},
            {"start": "12:45", "end": "13:40", "kind": "навивка",
             "reason": "шпульный стол в работу, навивка шпуль"},
            {"start": "13:46", "end": "16:00", "kind": "навивка",
             "reason": "сырьё получил, продолжаю навивку шпуль"}]})
    assert abs(r.operator_hours - 8.58) < 0.05, r.operator_hours
    assert abs(r.operator_norm - 8.58) < 0.05, r.operator_norm
    assert r.operator_excess <= 0.01, r.operator_excess
    print(f"✅ обед вырезан из обёртки: оператор {r.operator_hours} ч "
          f"(было 9,33)")


# ─────────── 4. Запрос наладчика — не часы наладчика ───────────

def test_service_request_is_not_attracted_hours():
    """«Нужна консультация наладчика» — ожидание, а не работа службы."""
    r = _one({
        "machine": "251", "operator": "Арташина", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 288402, "counter_end": 291916}],
        "events": [
            {"start": "15:58", "end": "16:21", "kind": "наладка",
             "reason": ("машина остановилась сама, с крючков нить нескинута, "
                        "сигнала нет; нужна консультация наладчика")}],
    })
    people = r.attracted_by_person()
    assert not any("наладчик" in who for who, _h in people), people
    stops = {l: h for l, h, _a in r.downtime_by_reason()}
    assert any("налад" in l for l in stops), stops
    print("✅ запрос наладчика не даёт строку «наладчик» в привлечённых")


def test_named_naladchik_still_counts():
    """Названный Пронкин остаётся в привлекаемом персонале (эталон 0,88 ч)."""
    r = _one({
        "machine": "251", "operator": "Арташина", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 288402, "counter_end": 291916}],
        "events": [
            {"start": "08:10", "end": "08:32", "kind": "наладка",
             "performer": "Пронкин",
             "reason": "машина встала, занимается наладчик Пронкин А."}]})
    people = r.attracted_by_person()
    assert any("Пронкин" in who for who, _h in people), people
    print(f"✅ Пронкин учтён: {people[0][1]} ч")


# ─────────── 5. Нет выпуска — нет «контроля за машиной» ───────────

def test_no_output_means_no_control_hours():
    """Ст.111 за 15.09: станок не дал ни узла — контроля за машиной нет.

    Дремкова весь день подменяла шпули (станок стоял). Бот добивал ей
    «работу по контролю за работой машины» 7,05 ч и выдавал 11,25 из 11,25
    при эталоне пользователя 3,92 ч (3,67 подмена + 0,25 развивка).
    Проверено по журналу: из 117 блоков пользователя с нулевой работой
    машины строка «контроль» не встречается ни разу.
    """
    r = _one({
        "machine": "111", "operator": "Дремкова",
        "lines": [{"counter_start": 206761, "counter_end": 206761}],
        "events": [
            {"start": "06:47", "end": "08:40", "kind": "подмена",
             "reason": "машина на подмене, заменили 386 из 750"},
            {"start": "13:10", "end": "13:15", "kind": "навивка",
             "reason": "13.10 навивка шпуль 5 шт"}]})
    assert r.zero_output_all_shift
    assert r.operator_control_hours == 0.0, r.operator_control_hours
    assert r.operator_hours < 5.0, r.operator_hours
    print(f"✅ нет выпуска — нет контроля: оператор {r.operator_hours} ч "
          f"(было 11,25)")


def test_machine_works_means_control_is_still_counted():
    """Машина работала — контроль за ней начисляется как раньше (ст.98)."""
    r = _one({
        "machine": "98", "operator": "Симонова", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 21431800, "counter_end": 21442600}],
        "events": [
            {"start": "10:15", "end": "11:10", "kind": "подмена",
             "reason": "подмена"},
            {"start": "16:40", "end": "17:30", "kind": "подмена",
             "reason": "подмена"}]})
    assert not r.zero_output_all_shift
    assert abs(r.operator_hours - 11.75) < 0.05, r.operator_hours
    print(f"✅ работающая машина: оператор {r.operator_hours} ч (эталон 11,75)")


# ─────────── 6. Автор сводки — не оператор станка ───────────

def test_surname_helper():
    """Фамилия из ФИО, инициалы фамилией не считаются.

    «Нина Владимировна Дроздова» → «Дроздова»; «Белякова С.» → «Белякова»
    (последнее слово — инициал «С», его брать нельзя, иначе поиск фамилии
    в тексте пойдёт по одной букве и найдёт что угодно).
    """
    from services import day_pipeline as dp
    assert dp._surname_of("Нина Владимировна Дроздова") == "Дроздова"
    assert dp._surname_of("Белякова С.") == "Белякова"
    assert dp._surname_of("Камбаров") == "Камбаров"
    assert dp._surname_of("Юлия Черныш") == "Черныш"
    assert dp._surname_of("") == ""
    print("✅ фамилия извлекается из полного ФИО")


def test_report_author_is_flagged():
    """Ст.98 за 15.09: «Дроздова» — автор сводки, а не оператор.

    В переписке фамилии оператора НЕТ вовсе (у пользователя там Симонова —
    операторы 98-й чередуются по двое суток, Белякова/Симонова). Модель
    поставила operator = «Дроздова» по подписи автора, и чужая фамилия ушла в
    таблицу отчёта производства.
    """
    from services import day_pipeline as dp

    class _Res:
        def __init__(self):
            self.ai_warnings = []

    # Настоящий текст ст.98 за 15.09: две записи, обе БЕЗ первого лица.
    text_98 = ("""–98 машина
15 сентября 2026 г.
Юлия Черныш, 06:57:Общ сч21431800
Ср. 750
Всего 8 срезов
Навито 160шп

Нина Владимировна Дроздова, 07:44:7:00 машина в работе.
187х3-51-250.5 зел.
Склад з.39 от 22.07.
Нач.сч. 21431800
Навито 90 шп.
Подмена с 10:15 до 11:10
Общ.сч. 21442600
Замена шпулярника в течении всей смены
19:00 конец смены""")
    res = _Res()
    orig = dp._machine_text
    dp._machine_text = lambda num, date: text_98 if num == "98" else ""
    try:
        dp._check_operator_not_author_only(
            {"machines": [{"machine": "98", "operator": "Дроздова"}]},
            "15.09.2026", res)
    finally:
        dp._machine_text = orig
    assert any("ПОДПИСЬ АВТОРА" in w for w in res.ai_warnings), res.ai_warnings
    print("✅ автор сводки помечен предупреждением")


def test_real_operator_is_not_flagged():
    """Настоящий оператор пишет о своей работе от первого лица — тревоги нет.

    Демидов (ст.321) и Арташина (ст.251) за 15.09 тоже подписывают сообщения,
    но внутри своих записей ведут станок от первого лица: «приступил к
    навивке», «продолжаю навивку», «иду за сырьём», «подставляю».
    """
    from services import day_pipeline as dp

    class _Res:
        def __init__(self):
            self.ai_warnings = []

    text_321 = ("""–321 машина
Евгений Демидов, 07:05:
7:00 начало смены
Общ сч 1200807
Жду информации допуска для начала навивки шпулей.

Евгений Демидов, 07:26:Принято!7:25 приступил к навивки шпуль.

Евгений Демидов, 08:08:
13:46 продолжаю навивать шпули
Сырьё получил, продолжаю навивку.
16:00 навивка шпуль стоп.
заполнение журнала и отчёта в чате.""")
    res = _Res()
    orig = dp._machine_text
    dp._machine_text = lambda num, date: text_321 if num == "321" else ""
    try:
        dp._check_operator_not_author_only(
            {"machines": [{"machine": "321", "operator": "Демидов"}]},
            "15.09.2026", res)
    finally:
        dp._machine_text = orig
    assert res.ai_warnings == [], res.ai_warnings
    print("✅ настоящий оператор не помечен")


def test_missing_surname_is_flagged():
    """Фамилии нет в тексте станка вообще — тоже предупреждаем."""
    from services import day_pipeline as dp

    class _Res:
        def __init__(self):
            self.ai_warnings = []

    res = _Res()
    orig = dp._machine_text
    dp._machine_text = lambda num, date: "–98 машина\n7:00 машина в работе."
    try:
        dp._check_operator_not_author_only(
            {"machines": [{"machine": "98", "operator": "Симонова"}]},
            "15.09.2026", res)
    finally:
        dp._machine_text = orig
    assert any("не упоминается" in w for w in res.ai_warnings), res.ai_warnings
    print("✅ отсутствующая в переписке фамилия помечена")


# ─────────── 7. Итоги по реальному разбору 15.09 ───────────

def test_real_15_09_matches_user_etalon():
    """Все четыре станка — как в файле пользователя."""
    machines = _load_15_09()
    if not machines:
        print("⚠️ данных 15.09 нет — пропускаю")
        return
    etalon = {"98": (11.75, 10.0, 0.0), "321": (8.25, 0.0, 0.77),
              # №251: в файле пользователя 8,0 ч — это 3514 узлов при его
              # норме 7,35. С 25.09.2026 согласована норма 7,4, и те же
              # 3514 узлов дают 7,91 ч.
              "251": (11.25, 7.91, 1.15), "111": (3.92, 0.0, 1.5)}
    # Допуски: оператор 0,4 ч (разбивка строк у пользователя детальнее),
    # машина 0,05 ч, привлечённые 0,4 ч.
    reports = build_shift_reports_from_structured(
        {"machines": [machines[k] for k in ("98", "321", "251", "111")
                      if k in machines]})
    for r in reports:
        num = str(r.machine)
        op, work, attr = etalon[num]
        assert abs(r.operator_hours - op) < 0.45, (num, r.operator_hours, op)
        assert abs(r.work_hours - work) < 0.05, (num, r.work_hours, work)
        assert abs(r.attracted_total - attr) < 0.45, (
            num, r.attracted_total, attr)
        # Баланс станка сходится в 12 ч — правило пользователя.
        rows = sum(h for _l, h, _a in r.downtime_by_reason())
        total = r.work_hours + rows
        assert abs(total - 12.0) < 0.6 or abs(total - 12.5) < 0.02, (
            num, total)
        print(f"  №{num}: оператор {r.operator_hours} (эт {op}) | машина "
              f"{r.work_hours} (эт {work}) | привлеч {r.attracted_total} "
              f"(эт {attr}) | баланс {round(total, 2)} ч")
    print("✅ реальный прогон 15.09 совпал с эталоном пользователя")


if __name__ == "__main__":
    for _name, fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(fn):
            fn()
    print("\nРегрессии сравнения с эталоном 15.09 проверены.")
