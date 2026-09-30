# -*- coding: utf-8 -*-
"""Регрессии по прогону 16.09.2026 — день, когда ввели ПЛАВАЮЩИЙ ОБЕД.

Эталон пользователя: «мои данные за 16.09.xlsx» (листы «16.09.2026», «16.09»),
исходник — «16,09,2026.docx».

Что чинили (каждое правило ниже проверяется тестом):
  • плавающий обед: станок на перерыв принимает коллега — строки «простой обед»
    НЕТ (ст.321/151/251), а где обед никто не принимал — есть (ст.98, 0,75);
  • ст.111: единственная запись про ОТХОДЫ не доказывает присутствие
    оператора — 9,52 ч «контроля» были выдуманы;
  • ст.321: филлер «наладка (весь день)» при 1% наладки в простоях;
  • ст.321: operator_shift_end «19:00» выдуман моделью — хвост 16:15–19:00
    обязан стать «отсутствием оператора»;
  • ст.321: события kind «работа» своего оператора выбрасывались — терялись
    приём сырья, перестановка телег, заполнение журнала;
  • ст.321: Петровский прикрыл станок на обед Демидова — это взаимный обмен,
    а не привлечённый персонал (в эталоне часы стоят у самого Демидова);
  • ст.251: длинное окно «7:00-15:39 подставляла шпулярник» (8,65 ч при работе
    5,73 ч) оставляется параллельным, но остаток до 12 ч объясняется той же
    причиной, а не валится в «причина не указана» и ложный флаг −1,29;
  • ст.151: малый остаток 0,39 ч при причине «обрыв нити» без времени идёт к
    этой причине — флаг «станок −0,39» убран;
  • лист «Остатки»: «Общий сч.21453550 7срезов» печатался как 214535507.
"""
import contextlib
import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import day_pipeline as dp                        # noqa: E402
from services import machine_rates                              # noqa: E402
from services import report_archive, shift_store                # noqa: E402
from services.shift_report import (                            # noqa: E402
    LUNCH_LABEL,
    NALADKA_LABEL,
    build_shift_reports_from_structured,
    classify_stop,
)
from services.remains_report import _NUM_RE                     # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "shifts", "16.09.2026")


@contextlib.contextmanager
def _writes_go_to_temp():
    """Прогон 16.09 идёт по настоящему платному пути `build_day`, а тот пишет
    `ai_raw.json` смены и `counter_ledger.json` в боевой `data/`. Без этого
    каждый запуск теста перезаписывал оплаченный разбор и журнал счётчиков
    выводом текущего кода (замечено 30.09.2026: у `data/shifts/16.09.2026/
    ai_raw.json` mtime совпал с прогоном базы). Разбор на вход берём из настоящих
    файлов, а всё, что положит на диск сборка, остаётся во временном каталоге.
    """
    keep = (shift_store.get_data_dir(), report_archive.get_data_dir())
    tmp = tempfile.mkdtemp(prefix="etalon1609_")
    shifts = os.path.join(tmp, "shifts")
    shutil.copytree(DATA_DIR, os.path.join(shifts, "16.09.2026"))
    ledger = os.path.join(os.path.dirname(DATA_DIR), "counter_ledger.json")
    if os.path.isfile(ledger):
        shutil.copy2(ledger, shifts)
    shift_store.set_data_dir(shifts)
    report_archive.set_data_dir(os.path.join(tmp, "reports"))
    try:
        yield
    finally:
        shift_store.set_data_dir(keep[0])
        report_archive.set_data_dir(keep[1])
        shutil.rmtree(tmp, ignore_errors=True)


def _day():
    """Реальный прогон 16.09.2026 из сохранённого ai_raw.json."""
    raw = json.load(open(os.path.join(DATA_DIR, "ai_raw.json"),
                         encoding="utf-8"))
    with _writes_go_to_temp():
        return dp.build_day("16.09.2026",
                            extract=lambda t, images=None: copy.deepcopy(raw),
                            ai_enabled=True)


def _one(machine):
    return build_shift_reports_from_structured({"machines": [machine]})[0]


def _r(day, num):
    for r in day.reports:
        if str(r.machine) == num:
            return r
    raise AssertionError(f"в отчёте нет станка {num}: "
                         f"{[r.machine for r in day.reports]}")


# ─────────────── эталон пользователя за 16.09 ───────────────
# (оператор, машина, привлечённые) — лист «16.09» файла пользователя.
# Часы работы пересчитаны под согласованный с 25.09.2026 список норм: у №151
# в ручном файле 11,61 ч (норма 10,9), у №251 — 5,73 ч (норма 7,35); при
# нормах 10,8 и 7,4 те же выпуски (7596 и 2529 узлов) дают 11,72 и 5,70 ч.
ETALON = {
    "98":  (11.76, 10.10, 0.00),
    "321": (8.58, 0.00, 2.17),
    "151": (11.25, 11.72, 0.00),
    "251": (11.25, 5.70, 5.94),
    "111": (0.00, 0.00, 0.00),
}


def test_real_16_09_matches_user_etalon():
    """Все пять станков совпадают с файлом пользователя в пределах допуска."""
    day = _day()
    for num, (op, work, att) in ETALON.items():
        r = _r(day, num)
        assert abs(r.operator_hours - op) <= 0.6, (num, r.operator_hours, op)
        assert abs(r.work_hours - work) <= 0.1, (num, r.work_hours, work)
        assert abs(r.attracted_total - att) <= 0.5, (num, r.attracted_total, att)
    print("✅ 16.09: все пять станков сошлись с эталоном")


def test_machine_balance_is_twelve_hours():
    """Работа + строки простоя = 12 ч на каждом работающем станке."""
    day = _day()
    for num in ("98", "321", "151", "251"):
        r = _r(day, num)
        rows = sum(h for _l, h, _a in r.downtime_by_reason())
        total = r.work_hours + rows + (r.unexplained_downtime or 0.0)
        # ст.98: подмена идёт на «рабочей» стороне баланса — 12,5 как у
        # пользователя (10,1 + 1,66 + 0,75).
        assert abs(total - 12.0) < 0.1 or abs(total - 12.56) < 0.1, \
            (num, total, rows)
    print("✅ баланс 12 ч сошёлся по всем станкам")


# ─────────────── плавающий обед (главное правило дня) ───────────────

def test_floating_lunch_has_no_downtime_row():
    """Станок на обед принял коллега — строки «простой обед» нет."""
    r = _one({"machine": "151", "operator": "Петровский",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 766069, "counter_end": 773665}],
              "events": [
                  {"start": "11:45", "end": "12:30", "kind": "обед",
                   "performer": "Петровский",
                   "reason": "обеденный перерыв Петровского О. с 11:45 до "
                             "12:30, Демидов Е. принимает №151"}]})
    assert r.lunch_handed_over is True
    assert r.lunch_in_downtime == 0.0, r.lunch_in_downtime
    labels = [l for l, _h, _a in r.downtime_by_reason()]
    assert LUNCH_LABEL not in labels, labels
    print("✅ плавающий обед: строки «простой обед» нет")


def test_machine_kept_running_flag_works():
    """machine_kept_running=true от модели — тоже снимает строку обеда."""
    r = _one({"machine": "251", "operator": "Вашурина",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 291916, "counter_end": 294445}],
              "events": [
                  {"start": "12:45", "end": "13:30", "kind": "обед",
                   "machine_kept_running": True, "reason": "обед"}]})
    assert r.lunch_in_downtime == 0.0
    print("✅ флаг machine_kept_running снимает простой обеда")


def test_regular_lunch_keeps_its_row():
    """Обычный обед (никто не принимал станок) — строка 0,75 остаётся.

    Эталон 16.09, ст.98: «0,75 | простой обед оператора» — там плавающего
    обеда не было. И 01.09 ст.121: «машину приняла и пускаю» — это НЕ про
    обед, маркер обязан требовать слово «обед»/«перерыв» рядом.
    """
    r = _one({"machine": "98", "operator": "Елистратова",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 21442600, "counter_end": 21453550}],
              "events": []})
    assert r.lunch_in_downtime == 0.75, r.lunch_in_downtime
    r2 = _one({"machine": "251", "operator": "Вашурина",
               "operator_shift_end": "19:00",
               "lines": [{"counter_start": 1, "counter_end": 2}],
               "events": [
                   {"start": "12:00", "end": "12:45", "kind": "обед",
                    "reason": "согласно записи наладчика — машину приняла и "
                              "пускаю"},
                   {"start": "12:00", "end": "12:45", "kind": "обед",
                    "reason": "обед"}]})
    assert r2.lunch_handed_over is False
    assert r2.lunch_in_downtime == 0.75, r2.lunch_in_downtime
    print("✅ обычный обед строку сохраняет")


def test_lunch_window_belongs_to_own_operator():
    """В разделе два обеда — окно берётся по СВОЕМУ оператору.

    Ст.151 за 16.09 (оператор Петровский): первым шло событие «обеденный
    перерыв Демидова Е. 11:00–11:45», а перерыв Петровского 11:45–12:30.
    """
    r = _one({"machine": "151", "operator": "Петровский",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 766069, "counter_end": 773665}],
              "events": [
                  {"start": "11:00", "end": "11:45", "kind": "обед",
                   "performer": "Демидов",
                   "reason": "обеденный перерыв Демидова Е. с 11:00 до 11:45"},
                  {"start": "11:45", "end": "12:30", "kind": "обед",
                   "performer": "Петровский",
                   "reason": "обеденный перерыв Петровского О. с 11:45 до "
                             "12:30, Демидов Е. принимает №151"}]})
    assert r.lunch_window == (11 * 60 + 45, 12 * 60 + 30), r.lunch_window
    assert r.lunch_window_mine is True
    print("✅ окно обеда — своего оператора")


# ─────────────── ст.111: отходы не доказывают оператора ───────────────

def test_waste_note_does_not_create_operator():
    """Единственная запись про ОТХОДЫ — станок без оператора, 11,25 absence.

    Эталон 16.09, ст.111: «Работа оператора всего — отсутствие оператора»,
    машина 0 из 12, в листе-таблице «11,25 | отсутв опер». Бот начислял
    выдуманному человеку 9,52 ч «контроля за работой машины» и 0,75 обеда.
    """
    r = _one({"machine": "111", "operator": None, "first_info_time": "08:44",
              "lines": [{"assortment": "НПА 187х2"}],
              "events": [{"kind": "работа",
                          "reason": "отходы НПА 187х2 после развивки шпулей "
                                    "6,2 кг"}]})
    assert r.has_operator is False
    assert r.operator_hours == 0.0, r.operator_hours
    assert r.lunch_in_downtime == 0.0, r.lunch_in_downtime
    rows = r.downtime_by_reason()
    assert abs(rows[0][1] - 11.25) < 0.02, rows
    assert "отсутствие" in rows[0][0], rows
    print("✅ ст.111: отходы не создали оператора, 11,25 отсутствие")


def test_counters_still_prove_operator():
    """Показания счётчика по-прежнему доказывают, что станок кто-то вёл."""
    r = _one({"machine": "251", "operator": None,
              "lines": [{"counter_start": 100, "counter_end": 200}],
              "events": []})
    assert r.has_operator is True
    assert r.lunch_in_downtime == 0.75
    print("✅ счётчик доказывает присутствие человека")


# ─────────────── ст.321: филлер наладки, выдуманный конец смены ───────────────

def test_naladka_all_shift_requires_dominant_naladka():
    """1% наладки в простоях — это НЕ «наладка весь день».

    Ст.321 за 16.09: наладка 0,10 ч из 9,78 ч простоев, а код добавлял
    филлер «наладка (весь день)» 2,22 ч, которого в эталоне нет.
    """
    r = _one({"machine": "321", "operator": "Демидов",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 1200807, "counter_end": 1200807}],
              "events": [
                  {"start": "07:53", "end": "07:57", "kind": "наладка",
                   "reason": "требуется наладка"},
                  {"start": "07:59", "end": "08:45", "kind": "подмена",
                   "reason": "подмена шпулей"},
                  {"start": "09:53", "end": "11:00", "kind": "навивка",
                   "reason": "приступил к навивке шпулей"},
                  {"start": "13:20", "end": "15:30", "kind": "подмена",
                   "reason": "подмена шпуль, Гузеев И.Е. установил 93 шпули",
                   "performer": "Гузеев"}]})
    assert r.naladka_all_shift is False, r.naladka_all_shift
    labels = [l for l, _h, _a in r.downtime_by_reason()]
    assert "наладка (весь день)" not in labels, labels
    print("✅ филлер «наладка весь день» не сработал на 1% наладки")


def test_naladka_all_shift_true_case_preserved():
    """Настоящая «наладка весь день» без оператора (26.08/02.09) сохранена."""
    r = _one({"machine": "111",
              "events": [{"kind": "наладка",
                          "reason": "весь рабочий день машина не работала, "
                                    "в очереди на наладку"}]})
    assert r.naladka_all_shift is True
    assert r.naladka_all_shift_no_operator is True
    print("✅ эталонные дни наладки не тронуты")


def test_unconfirmed_shift_end_yields_to_last_note():
    """«19:00» без подтверждения текстом уступает последней записи мастера.

    Ст.321 за 16.09: раздел заканчивается «16:00-16:15 заполнение журнала и
    чата», ни «конец смены», ни «19:00» в нём нет. Хвост 16:15–19:00 обязан
    стать «отсутствием оператора» (в эталоне 3,78 ч).
    """
    r = _one({"machine": "321", "operator": "Демидов",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 1200807, "counter_end": 1200807}],
              "events": [
                  {"start": "07:22", "end": "07:53", "kind": "навивка",
                   "reason": "приступил к навивке шпуль"},
                  {"start": "16:00", "end": "16:15", "kind": "работа",
                   "reason": "заполнение журнала и чата"}]})
    assert r.model_shift_end_unconfirmed is True
    assert r.closing_activity_end == 16 * 60 + 15
    assert abs(r._operator_end_span - 9.25) < 0.02, r._operator_end_span
    labels = {l: h for l, h, _a in r.downtime_by_reason()}
    assert any("отсутствие" in l for l in labels), labels
    assert abs(sum(labels.values()) - 12.0) < 0.02, labels
    print("✅ хвост после 16:15 стал отсутствием оператора")


def test_confirmed_shift_end_is_not_overridden():
    """Мастер написал «19:00 конец смены» — 19:00 остаётся, даже если журнал
    заполняли позже/раньше (ст.251 за 16.09: запись 18:45–19:00)."""
    r = _one({"machine": "251", "operator": "Вашурина",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 291916, "counter_end": 294445}],
              "events": [
                  {"start": "18:45", "end": "19:00", "kind": "работа",
                   "reason": "заполнение чата и журнала"}]})
    assert r.model_shift_end_unconfirmed is False
    assert r._operator_end_span == 12.0
    print("✅ подтверждённый конец смены не переопределяется")


# ─────────────── kind «работа» своего оператора ───────────────

def test_own_work_events_reach_operator_hours():
    """События kind «работа» своего оператора идут в его часы.

    Ст.321 за 16.09: «приём сырья за шпульный стол», «перестановка телег»,
    «заполнение журнала и чата» выбрасывались целиком — Демидов потерял
    ~1,5 ч (6,76 вместо эталонных 8,58).
    """
    r = _one({"machine": "321", "operator": "Демидов",
              "operator_shift_end": "16:15",
              "lines": [{"counter_start": 1200807, "counter_end": 1200807}],
              "events": [
                  {"start": "07:05", "end": "07:15", "kind": "работа",
                   "reason": "приём сырья за шпульный стол",
                   "performer": "Демидов"},
                  {"start": "15:35", "end": "15:55", "kind": "работа",
                   "reason": "перестановка телег, сбор отходов",
                   "performer": "Демидов"},
                  {"start": "16:00", "end": "16:15", "kind": "работа",
                   "reason": "заполнение журнала и чата",
                   "performer": "Демидов"}]})
    assert abs(r.operator_hours - 0.75) < 0.05, r.operator_hours
    rows = r.operator_work_by_reason()
    descr = " ".join(d for d, _h in rows)
    assert "приём сырья" in descr, descr
    assert "заполнение журнала" in descr, descr
    # «прочее» не склеивает разные дела мастера в одну строку
    assert not any(d.startswith("прочее (") for d, _h in rows), rows
    assert "перестановка телег" in descr, descr
    print(f"✅ операции без простоя дошли до оператора: {r.operator_hours} ч")


# ─────────────── прикрытие на обед — не привлечённый персонал ───────────────

def test_lunch_cover_is_not_attracted():
    """Петровский прикрыл 321 на обед Демидова — взаимный обмен, 0 ч.

    Эталон 16.09, ст.321: в привлечённых только «2,17 Гузяев — подмена шпулей
    93 шт»; часы Петровского стоят в блоке Демидова его строкой «0,75 работа
    на машине №151».
    """
    r = _one({"machine": "321", "operator": "Демидов",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 1200807, "counter_end": 1200807}],
              "events": [
                  {"start": "11:00", "end": "11:45", "kind": "навивка",
                   "performer": "Петровский", "other_machine": "151",
                   "reason": "принял 321 на время обеда Демидова Е., навивка "
                             "шпулей"},
                  {"start": "13:20", "end": "15:30", "kind": "подмена",
                   "performer": "Гузеев",
                   "reason": "подмена шпуль, Гузеев И.Е. установил 93 шпули"}]})
    people = dict(r.attracted_by_person())
    assert not any("Петровский" in w for w in people), people
    assert any("Гузеев" in w for w in people), people
    # обычный уход оператора на чужой станок по-прежнему считается
    assert r.attracted_total > 2.0
    print(f"✅ прикрытие на обед не в привлечённых: {people}")


def test_other_machine_of_guest_is_not_our_absence():
    """other_machine у ЧУЖОГО исполнителя — это ОТКУДА он пришёл.

    Ст.321 за 16.09: {kind «навивка», performer «Петровский», other «151»}
    код трактовал как «Демидов ушёл на 151» и рисовал отсутствие 4,98 ч.
    """
    r = _one({"machine": "321", "operator": "Демидов",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 1200807, "counter_end": 1200807}],
              "events": [
                  {"start": "11:00", "end": "11:45", "kind": "навивка",
                   "performer": "Петровский", "other_machine": "151",
                   "reason": "принял 321 на время обеда Демидова Е., навивка "
                             "шпулей"}]})
    labels = [l for l, _h, _a in r.downtime_by_reason()]
    assert not any("работа на №151" in l for l in labels), labels
    print("✅ чужой other_machine не стал отсутствием нашего оператора")


# ─────────────── длинное окно и малый остаток ───────────────

def test_shiftlong_window_explains_residual():
    """«7:00-15:39 подставляла шкулярник» (8,65 ч при работе 5,73):
    окно параллельное, но остаток 12−5,73−3,58−0,65−0,75 объясняется той же
    причиной — флаг «станок −1,29» убран (эталон: «2,85 замена шпул»)."""
    r = _one({"machine": "251", "operator": "Вашурина",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 291916, "counter_end": 294445}],
              "events": [
                  {"start": "07:00", "end": "15:39", "kind": "остановка",
                   "reason": "подставляла шпулярник, 205 шт.",
                   "performer": "Вашурина"},
                  {"start": "07:00", "end": "07:25", "kind": "наладка",
                   "reason": "обрыв нити, требуется помощь наладчика",
                   "performer": "Р."},
                  {"start": "15:39", "end": "18:10", "kind": "остановка",
                   "reason": "полная замена шпулярника, Вашурина-101, "
                             "Петровский-101, Неделькин-101",
                   "performer": "Вашурина"}]})
    # Длинное окно стало параллельным, но остаток смены не потерялся:
    # он объяснён той же причиной и закрыл баланс до 12 ч.
    assert r.shiftlong_swap_label, r.shiftlong_swap_label
    assert r.named_residual_hours > 1.0, r.named_residual_hours
    assert r.named_residual_label == r.shiftlong_swap_label
    assert r.machine_shortfall == 0.0, r.machine_shortfall
    labels = {l: h for l, h, _a in r.downtime_by_reason()}
    assert abs(sum(labels.values()) + r.work_hours - 12.0) < 0.05, labels
    print(f"✅ остаток объяснён причиной: {r.named_residual_label}")


def test_small_residual_goes_to_named_reason():
    """Остаток ≤1 ч с единственной названной причиной — к этой причине.

    Ст.151 за 16.09: «Стоп. Обрыв нити верхней основы» без времени. Счётчик
    берётся из текущей нормы станка так, чтобы остаток до 12 ч был 0,39 ч —
    ровно как в тот день при норме 10,9 (7596 узлов = 11,61 ч). При
    согласованной 25.09.2026 норме 10,8 те же 7596 узлов дают 11,72 ч, остаток
    0,28 попадает в допуск ±20 мин, и относить к причине уже нечего (это
    проверяет второй блок ниже). В эталоне пользователя: «0,18 простой обрыв».
    """
    def _r151(work_hours: float):
        return _one({"machine": "151", "operator": "Петровский",
                     "operator_shift_end": "19:00",
                     "lines": [{"counter_start": 766069,
                                 "counter_end": round(766069 + work_hours
                                                      * machine_rates.get_rate(
                                                          "151") * 60)}],
                     "events": [
                         # Обед у 151-й был ПЛАВАЮЩИМ: станок принял Демидов,
                         # машина не останавливалась — строки «простой обед»
                         # нет, и остаток до 12 ч полностью объясняется обрывом
                         # нити.
                         {"start": "11:45", "end": "12:30", "kind": "обед",
                          "performer": "Петровский",
                          "reason": "обеденный перерыв Петровского О. с 11:45 "
                                    "до 12:30, Демидов Е. принимает №151"},
                         {"kind": "остановка", "start": None, "end": None,
                          "reason": "обрыв нити верхней основы"},
                         {"kind": "остановка", "start": None, "end": None,
                          "reason": "оборвало нить верхней основы, устранил"}]})

    # Остаток больше допуска (0,39 ч): он уходит к названной причине, а не в
    # «причина не указана», и станок не выглядит недоработавшим.
    r = _r151(11.61)
    assert abs(r.work_hours - 11.61) < 0.02, r.work_hours
    assert r.lunch_in_downtime == 0.0, r.lunch_in_downtime
    assert abs(r.named_residual_hours - 0.39) < 0.05, r.named_residual_hours
    assert r.named_residual_label == "обрыв нити", r.named_residual_label
    assert r.machine_shortfall == 0.0, r.machine_shortfall

    # Настоящий счётчик того дня (7596 узлов) при новой норме: остаток 0,28 ч
    # уже в допуске ±20 мин — флага нет и придумывать простой негде.
    real = _r151(11.7222)
    assert abs(real.work_hours - 11.72) < 0.02, real.work_hours
    assert real.named_residual_hours == 0.0, real.named_residual_hours
    assert real.machine_shortfall == 0.0, real.machine_shortfall
    print("✅ малый остаток ушёл к «обрыв нити»; в допуске — флага нет")


def test_big_residual_stays_unexplained():
    """БОЛЬШОЙ остаток без времени остаётся необъяснённым (эталон 02.09:
    10,95 ч и подпись «время не указано у причин: …»)."""
    r = _one({"machine": "321", "operator": "Петровский",
              "operator_shift_end": "19:00",
              "lines": [{"counter_start": 115329, "counter_end": 115423}],
              "events": [
                  {"kind": "остановка", "start": None, "end": None,
                   "reason": "Подстановка 13 бобин на шпулярник",
                   "performer": "Петровский"},
                  {"kind": "чинка", "start": None, "end": None,
                   "reason": "Чинка кромки полотна", "performer": "Петровский"}]})
    assert r.named_residual_hours == 0.0, r.named_residual_hours
    assert r.unexplained_downtime > 5.0, r.unexplained_downtime
    print("✅ большой остаток честно необъяснён")


def test_thread_break_category():
    """«обрыв нити» — своя категория простоя, а не «прочее»."""
    assert classify_stop("обрыв нити верхней основы")[0] == "обрыв нити"
    assert classify_stop("Стоп. Обрыв нити")[0] == "обрыв нити"
    # «без обрывов и дыр» — замечание о состоянии, не обрыв
    assert classify_stop("полотна чистые, без обрывов и дыр")[0] != "обрыв нити"
    # наладчик важнее обрыва: «обрыв, занимается Пронкин» — наладка
    assert classify_stop("машина в службе наладки, обрыв, занимается "
                         "Пронкин")[0] == NALADKA_LABEL
    print("✅ категория «обрыв нити» работает")


# ─────────────── лист «Остатки»: склейка счётчика ───────────────

def test_counter_not_glued_to_next_value():
    """«Общий сч.21453550 7срезов» → 21453550, а не 214535507."""
    assert _NUM_RE.search("Общий сч.21453550 7срезов").group(0) == "21453550"
    # разделители тысяч сохраняются
    assert _NUM_RE.search("Общ.сч. - 12 345").group(0) == "12 345"
    assert _NUM_RE.search("Сч 21 453 550").group(0) == "21 453 550"
    print("✅ счётчик не склеивается со следующим значением")


# ─────────────── предупреждение про автора сводки ───────────────

def test_author_only_warning_still_works():
    """Проверка «автор сводки ≠ оператор» (15.09) на 16.09 не сломана."""
    day = _day()
    r = _r(day, "98")
    # Елистратова в тексте 98-й пишет от первого лица — тревоги быть не должно
    assert not any("ПОДПИСЬ АВТОРА" in w for w in day.ai_warnings), \
        day.ai_warnings
    assert r.operator == "Елистратова"
    print("✅ автор сводки не перепутан с оператором")


def test_no_false_warning_when_surname_is_in_global_block():
    """Фамилия названа в ОБЩЕМ блоке — ложного «не упоминается» нет.

    Ст.151 за 16.09: раздел подписан «Олег, 07:02» (только имя), а фамилия
    «Петровский О» стоит в блоке «Данные из чата» про плавающий обед — и тот
    блок при выгрузке лежит внутри секции ст.98. Поиск только по своей секции
    печатал «№151: оператор «Петровский» в переписке не упоминается ни разу».
    """
    day = _day()
    assert not any("не упоминается" in w for w in day.ai_warnings), \
        day.ai_warnings
    r = _r(day, "151")
    assert r.operator == "Петровский"
    print("✅ фамилия из общего блока не дала ложной тревоги")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print("\nВСЕ ПРОВЕРКИ 16.09 ПРОЙДЕНЫ")
