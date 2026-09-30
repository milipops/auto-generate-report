# -*- coding: utf-8 -*-
"""Регрессии сравнения эталона пользователя с отчётом бота за 14.09.2026.

Эталон — лист «14.09» файла пользователя «Отчет_производства.xlsx» (журнал за
18.08–14.09), отчёт бота — «Отчет_производства_за_смену_14_09_2026.xlsx».
Сравнение вскрыло девять дефектов; каждый закрыт здесь отдельной проверкой.

Главное: КРУПНЕЙШИЕ расхождения были не ошибками модели, а ОШИБКАМИ МАСТЕРОВ
в показаниях счётчика, которые код принимал вслепую:

    №321: «Общ сч 1200807» вместо 120807 → дельта +1 080 054 узла
          → «работа машины 3000,15 ч из 12 часов» и «+2996,32 ч сверх 12 ч»
          → в эталоне пользователя 0,2 ч.
    №151: «Общ.сч. 7760351» вместо 760351 → дельта −6 994 283
          → работа 0 ч и «−11,25 ч до 12 ч»; в эталоне 8,7 ч.

Испорченные числа ещё и утекали в counter_ledger.json, отравляя перенос
начального показания на следующую смену.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import counter_check as cc                        # noqa: E402
from services import shift_store as ss                          # noqa: E402
from services.shift_report import (                             # noqa: E402
    STRETCH_LABEL, build_shift_reports_from_structured, classify_stop,
    machine_stopped_for)

# Настоящая секция №151 из файла 14,09,2026.docx: мастер написал «Общ.сч.
# 7760351» (лишняя 7), а дальше 12 раз — шестизначные показания.
REAL_151 = """–151 машина
14 сентября 2026 г.

Юлия Черныш, 06:46:начало смены:
Общ.сч. 7760351
Ср.сч. - 159 узлов протяжки со шпулярника
Инна Гудкова, 07:05:Доброе утро
Сч 760449
Ср сч 0 из 14500
Стоп машина, Срез узлов, Замена телег
Сч 760496, ср сч 47
Стоп машина, Обрыв нити
Сч 761424, ср сч 975
Устраняет наладчик
Стоп машина, чинка полотна после обрыва нити
Сч 761892, ср сч 1443
Обед 12:00-12:45
Сч 762354
Ср сч 1905
Обрыв нити
Сч 762744, ср сч 2295
Занимается наладчик
Стоп машина, Чинка полотна
Сч 763212, ср сч 2763
Обрыв нити
Сч 763546 ср сч 3097
Стоп машина, Чинка полотна
Сч 764019, ср сч 3570
Стоп машина, Обрыв нити
Сч 764864, ср сч 4415
Стоп машина, Чинка полотна
Сч 765337, ср сч 4888
19:00 Конец смены: Общ.сч. -_766068__ Ср.сч. - _5619_ узлов из _14500_
"""

# Секция №321: опечатка в КОНЕЧНОМ показании.
REAL_321 = """–321 машина
14 сентября 2026 г.
Юлия Черныш, 06:50:Об. Сч.: 120753
Ср. Сч. 707 из 760
Евгений Демидов, 07:09:7:00-7:05 приём смены
Об. Сч.: 120753
14:56 стоп машина , сошли 4 шпули
Общ.Сч. 120796
16:58 пуск машина
Стоп машина конец смены
Общ сч 1200807
Ср сч 760 из 760
"""


def _one(machine):
    return build_shift_reports_from_structured({"machines": [machine]})[0]


# ─────────────── 1. Опечатки в показаниях счётчика ───────────────

def test_extra_digit_in_start_counter_is_caught():
    """№151: «7760351» вместо 760351 — лишняя цифра ловится модой длины."""
    start, end, notes = cc.check_counters(
        "151", 7760351.0, 766068.0, text=REAL_151, prev_end=754129.0)
    # Восстановлено ТОЧНОЕ утреннее показание вычёркиванием лишней семёрки,
    # а не следующее измерение смены: 760351, а не 760449. Эталон
    # пользователя за 14.09 — 8,7 ч работы станка:
    #   (766068 − 760351) / 10,9 / 60 = 8,74 ч  ✓
    #   (766068 − 760449) / 10,9 / 60 = 8,59 ч  ✗
    assert int(start) == 760351, start
    assert int(end) == 766068, end
    assert notes and "вычёркиванием" in notes[0], notes
    print(f"✅ №151: 7760351 -> {int(start)} (точно, вычёркиванием цифры)")


def test_extra_digit_in_end_counter_is_caught():
    """№321: «1200807» вместо 120807 — лишняя цифра в конечном показании."""
    start, end, notes = cc.check_counters(
        "321", 120753.0, 1200807.0, text=REAL_321)
    assert int(start) == 120753, start
    # 120807 — восстановлено вычёркиванием лишнего нуля, а не последнее
    # промежуточное показание 120796: счётчик за смену растёт, и конечное
    # должно быть не меньше 120796.
    assert int(end) == 120807, end
    assert notes and "вычёркиванием" in notes[0], notes
    print(f"✅ №321: 1200807 -> {int(end)} (точно, вычёркиванием цифры)")


def test_work_hours_no_longer_explode():
    """Часы работы станка возвращаются в разумные рамки после проверки."""
    from services import machine_rates
    bad = machine_rates.work_hours("321", 120753, 1200807)
    good = machine_rates.work_hours("321", 120753, 120796)
    assert bad > 1000, bad            # было 3000,15 ч
    assert good < 1.0, good           # стало 0,12 ч — эталон пользователя 0,2 ч
    print(f"✅ №321: было {bad} ч -> стало {round(good, 2)} ч")


def test_clean_counters_are_not_touched():
    """Честные показания не правятся: №251 и №98 за 14.09."""
    for machine, start, end in (("251", 284387.0, 288402.0),
                                ("98", 21420800.0, 21431800.0)):
        s, e, notes = cc.check_counters(machine, start, end, text="")
        assert (s, e) == (start, end), (machine, s, e)
        assert notes == [], notes
    print("✅ честные показания не тронуты")


def test_far_ledger_entry_does_not_raise_alarm():
    """Запись журнала недельной давности тревогу не поднимает (prev_is_adjacent)."""
    _s, _e, notes = cc.check_counters("98", 21420800.0, 21431800.0, text="",
                                      prev_end=21387950.0,
                                      prev_is_adjacent=False)
    assert notes == [], notes
    # А вот соседняя смена — сверяется.
    _s, _e, notes = cc.check_counters("98", 99999999.0, 99999999.0, text="",
                                      prev_end=21387950.0,
                                      prev_is_adjacent=True)
    assert notes, "соседняя смена должна проверяться"
    print("✅ сверка с журналом только по соседним сменам")


def test_sections_are_split_when_header_says_machine():
    """Заголовок «–151 машина» — это раздел (в файле 14.09 именно так)."""
    secs = ss.split_sections(REAL_151 + REAL_321)
    assert [s.machine for s in secs] == ["151", "321"], [s.machine for s in secs]
    print("✅ «–151 машина» распознаётся как раздел")


# ─────────────── 2. Норма оператора: стажёр 8 ч ───────────────

def test_trainee_norm_is_eight_hours():
    """Ст.321 за 14.09: оператор — стажёр, норма 8 ч (эталон «8 из 8 часа»)."""
    r = _one({
        "machine": "321", "operator": "Демидов",
        "operator_norm_hours": 8, "operator_shift_end": "19:00",
        "lines": [{"counter_start": 120753, "counter_end": 120807}],
        "events": [{"start": "07:00", "end": "15:00", "kind": "остановка",
                    "reason": "сошли 2 шпули, замена сошедших шпулей"}]})
    assert abs(r.operator_norm - 8.0) < 0.01, r.operator_norm
    assert r.operator_excess <= 0.01, (r.operator_hours, r.operator_excess)
    print(f"✅ стажёр: {r.operator_hours} из {r.operator_norm} ч (без ложного превышения)")


def test_norm_defaults_when_not_named():
    """Норма не названа — считается как раньше (11,25 ч)."""
    r = _one({
        "machine": "151", "operator": "Гудкова", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 760449, "counter_end": 766068}],
        "events": []})
    assert abs(r.operator_norm - 11.25) < 0.01, r.operator_norm
    print("✅ норма по умолчанию 11,25 ч")


# ─────────────── 3. Полуинтервалы ───────────────

def test_half_interval_gets_the_next_start():
    """«start есть, end нет» достраивается от начала СЛЕДУЩЕГО события."""
    r = _one({
        "machine": "151", "operator": "Гудкова", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 760449, "counter_end": 766068}],
        "events": [
            {"start": "11:15", "end": None, "kind": "чинка",
             "reason": "стоп машина, чинка полотна после обрыва нити"},
            {"start": "12:00", "end": "12:45", "kind": "обед", "reason": "обед"}]})
    stops = {l: h for l, h, _a in r.downtime_by_reason()}
    assert abs(stops.get("чинка/ремонт", 0) - 0.75) < 0.05, stops
    print("✅ полуинтервал достроен до соседа (0,75 ч чинки)")


def test_chaining_does_not_invent_hours():
    """Достройка не выдумывает часы: событие без соседей остаётся без времени."""
    r = _one({
        "machine": "151", "operator": "Гудкова", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 760449, "counter_end": 766068}],
        "events": [
            {"start": "16:07", "end": None, "kind": "остановка",
             "reason": "стоп машина, обрыв нити"},
            {"start": None, "end": None, "kind": "чинка",
             "reason": "чинка полотна"}]})
    # 16:07 не должен растянуться до 19:00 — соседа со start нет. С 28.09.2026
    # (приказ владельца «до 11,25 не добивать») здесь ещё честнее: 8,67 ч из
    # данных мастера вместо 11,25, которые дорисовывал филлер.
    assert abs(r.operator_hours - 8.67) < 0.05, r.operator_hours
    print("✅ часы не выдуманы (оператор 8,67, а не 11,25 и не 14,46)")


# ─────────────── 4. Обед как отметка времени ───────────────

def test_after_lunch_is_not_a_break():
    """«После обеда машина в работе» — навивка, а не перерыв."""
    label, worked = classify_stop(
        "после обеда машина в работе, продолжаю навивку шпулей, об.сч 762354")
    assert label == "навивка/намотка шпулей", label
    assert worked is True
    print(f"✅ «после обеда …» = {label}")


def test_real_lunch_records_still_break():
    """Настоящие записи обеда читаются как перерыв."""
    for text in ("обед", "12:00-12:45 обед", "Обед"):
        label, worked = classify_stop(text)
        assert label == "перерыв", (text, label)
        assert worked is False, text
    print("✅ обычный обед остался перерывом")


# ─────────────── 5. Дотягивание ───────────────

def test_stretching_is_its_own_category():
    """«Сошли 2 шпули» → дотягивание, как в эталоне пользователя."""
    for text in ("сошли 2 шпули", "сошла шпуля", "замена сошедших шпулей",
                 "приступил к замене сошедших шпулей", "дотяжка узлов"):
        label, worked = classify_stop(text)
        assert label == STRETCH_LABEL, (text, label)
        assert worked is True, text
        assert machine_stopped_for(text, label), text
    print(f"✅ дотягивание — своя категория: {STRETCH_LABEL}")


def test_spool_sensor_naladka_is_not_stretching():
    """Наладка датчика схода шпули — наладка (эталон 21.08/26.08/04.09)."""
    for text in ("приёмка наладки датчика схода шпули с наладчиком Пронкиным",
                 "наладка (сошла шпуля, неоднократно; передал наладчику Шалыминову)"):
        label, _w = classify_stop(text)
        assert label == "наладка/переналадка", (text, label)
    print("✅ наладка датчика схода осталась наладкой")


def test_plain_spool_change_still_separate():
    """Замена шпуль БЕЗ слова «сошл» — прежняя категория."""
    for text in ("замена шпуль", "меняю шпули"):
        label, _w = classify_stop(text)
        assert label == "замена шпуль (станок стоит)", (text, label)
    print("✅ обычная замена шпуль не пострадала")


# ─────────────── 6. Безымянный наладчик ───────────────

def test_unnamed_service_counts_as_attracted():
    """Служба без фамилии — строка «наладчик» (эталон 14.09: 0,87 ч)."""
    r = _one({
        "machine": "321", "operator": "Демидов",
        "operator_norm_hours": 8, "operator_shift_end": "19:00",
        "lines": [{"counter_start": 120753, "counter_end": 120807}],
        "events": [{"start": "07:10", "end": "07:50", "kind": "наладка",
                    "performer": None,
                    "reason": "служба наладки вытащила шнур, который ушёл за вал"}]})
    people = r.attracted_by_person()
    assert r.attracted_total > 0.5, r.attracted_total
    assert any("наладчик" in who for who, _h in people), people
    print(f"✅ безымянная служба учтена: {people[0][0][:30]}… {people[0][1]} ч")


def test_named_service_keeps_its_name():
    """Названный наладчик остаётся с фамилией (эталон 21.08: Пронкин)."""
    r = _one({
        "machine": "251", "operator": "Арташина", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 284387, "counter_end": 288402}],
        "events": [{"start": "08:52", "end": "09:20", "kind": "наладка",
                    "performer": "Пронкин",
                    "reason": "приёмка наладки датчика схода шпули"}]})
    people = r.attracted_by_person()
    assert any("Пронкин" in who for who, _h in people), people
    stops = {l: h for l, h, _a in r.downtime_by_reason()}
    assert abs(stops.get("наладка/переналадка", 0) - 0.47) < 0.03, stops
    print("✅ Пронкин назван по фамилии, простой «наладка» 0,47 ч")


# ─────────────── 7. Итог по реальному разбору ───────────────

def test_real_1409_counters_validate_cleanly():
    """На настоящих данных 14.09 валидатор правит ровно два станка."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "data", "shifts", "14.09.2026", "day.json")
    if not os.path.isfile(path):
        print("⚠️ данных 14.09 нет — пропускаю")
        return
    with open(path, encoding="utf-8") as f:
        day = json.load(f)
    text = "\n".join(m.get("text") or "" for m in day["messages"])
    # (начало, конец) как их отдала модель → (ожидаемое начало, ожидаемый конец).
    # У №151 опечатка в НАЧАЛЬНОМ показании, у №321 — в КОНЕЧНОМ.
    cases = {
        "151": ((7760351.0, 766068.0), (760351, 766068)),
        "321": ((120753.0, 1200807.0), (120753, 120807)),
        "251": ((284387.0, 288402.0), (284387, 288402)),
        "98": ((21420800.0, 21431800.0), (21420800, 21431800)),
    }
    sections = ss.split_sections(text)
    for num, (given, want) in cases.items():
        # Текст только своего раздела: смешивать показания станков нельзя, у
        # ст.98 счётчик восьмизначный, а у ст.151 шестизначный — мода разъедется.
        sec = "\n".join(s.text for s in sections if s.machine == num)
        assert sec, f"раздел {num} не найден"
        prev = {"151": 754129.0, "321": 118526.0,
                "251": 288402.0, "98": 21431800.0}[num]
        s, e, _n = cc.check_counters(num, given[0], given[1], text=sec,
                                     prev_end=prev, prev_is_adjacent=False)
        assert (int(s), int(e)) == want, (num, s, e, want)
    print("✅ реальный прогон 14.09: оба испорченных показания восстановлены, "
          "честные не тронуты")


if __name__ == "__main__":
    for _name, fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(fn):
            fn()
    print("\nРегрессии сравнения с эталоном 14.09 проверены.")
