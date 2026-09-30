# -*- coding: utf-8 -*-
"""Смена 24.09.2026: параллельные операции не имеют права ставить станок в стоп.

Правило участка (подтверждено пользователем 25.09.2026): если мастер НЕ написал
про останов, машина идёт, а все её дела — навивка и развивка шпулей, замена
шпулярника, чинка «в течение всей смены» — делаются параллельно. Время работы
станка берётся из счётчика, поэтому простой, наложенный на него, — это прямое
задвоение смены.

Что бот делал не так (отчёт за 24.09, машины 98 и 91):

1. Ст.98: «Развито и навито 110 шп», «...250 шп», «...140 шп» — классификатор не
   знал таких форм (маркеры были «навив/намот/шпул/завив») и давал «прочее», а
   нераспознанная причина по умолчанию считалась остановом. Итог: 10,33 ч
   «простоя» при 10,28 ч работы по счётчику — баланс 22,28 ч из 12 ч.
2. Ст.91: «в течении всей смены замена шпулярника и чинка» — слово «чинка»
   перекрывало любой признак параллельности (правило А), и станок стоял 11,33 ч,
   хотя в переписке «7:00 машина в работе», «13:10 пуск машины», «14:10 пуск
   машины», а счётчик дал 7,22 ч работы.
3. Следствие п.2 для часов оператора: строка на всю смену при подсчёте человека
   теряла признак параллельности (объединённая строка не несла флаг), поэтому её
   часы не вычитались из «контроля за работой машины» — 14,69 ч при смене 12 ч.
4. В Excel у 91-го колонка «Технические остановки» показывала 0,25 ч вместо 3,03:
   маркер «шпулярник» относил строку «замена шпулярника и чинка в течении всей
   смены» к подменам, а подмены в эту колонку не пишутся (так в ручном файле
   пользователя за 22.09). Текст отчёта про те же 2,78 ч говорил прямо.

Правило А остаётся в силе для чинки с НАЗВАННЫМ временем («10:36 чинка, 10:39
чинка завершена»): такой останов по-прежнему идёт в простой станка. Разводит их
физика: окно «на всю смену» плюс работа по счётчику не влезают в 12 ч.

Данные — сохранённый разбор tests/ai_raw_24_09_2026.json (детерминированный слой,
без запросов к ИИ). Второй прогон того же дня (платный, 25.09.2026) сохранён как
tests/ai_raw_24_09_2026_drift.json: модель вернула те же сообщения другими окнами
и без части строк, и раздел 7 проверяет, что правила держат отчёт и на нём.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (                    # noqa: E402
    BOBBIN_LABEL,
    LUNCH_LABEL,
    MACHINE_SHIFT_HOURS,
    OTHER_LABEL,
    TOLERANCE_H,
    WINDING_LABEL,
    TechStop,
    build_shift_reports_from_structured,
    classify_stop,
    is_real_submen,
    is_submen_label,
    machine_stopped_for,
    submen_hours,
)
from services.excel_export import (_result_columns, _stops_columns,   # noqa: E402
                                   _subs_hours_cell)


def _reports(fixture="ai_raw_24_09_2026.json"):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fixture)
    with open(path, encoding="utf-8") as f:
        return build_shift_reports_from_structured(json.load(f))


def _r(num, fixture="ai_raw_24_09_2026.json"):
    for r in _reports(fixture):
        if str(r.machine) == num:
            return r
    raise AssertionError(f"нет станка {num} за 24.09.2026 ({fixture})")


def _downtime(r):
    return {label: hours for label, hours, _active in r.downtime_by_reason()}


# ——— 1. навивка короткими формами ———

def test_wound_bobbins_shorthand_is_winding_not_other():
    """«Развито и навито 110 шп» — навивка, и станок в это время идёт."""
    for text in ("развито и навито 110 шп", "Навито 200 шп. 19:00 конец смены",
                 "Развито 160 шпулей"):
        label, _active = classify_stop(text)
        assert label == WINDING_LABEL, (text, label)
        assert machine_stopped_for(text, label) is False, text


def test_98_winding_is_not_machine_downtime():
    """Ст.98 за 24.09: 10,28 ч работы по счётчику, в простое только подмены."""
    r = _r("98")
    assert r.work_hours == 10.28, r.work_hours
    rows = _downtime(r)
    assert OTHER_LABEL not in rows, rows
    assert sorted(rows) == sorted(["подмена (заправка полотна)", LUNCH_LABEL]), rows
    assert rows["подмена (заправка полотна)"] == 1.67, rows
    assert r.machine_excess == 0.0, (r.machine_balance, r.machine_excess)


def test_98_winding_rows_stay_in_operators_own_work():
    """Часы навивки не потерялись: они в блоке оператора, при идущей машине."""
    r = _r("98")
    winding = [s for s in r.tech_stops
               if (s.reason or "").lower().startswith("развито")]
    assert len(winding) == 3, [(s.reason, s.hours) for s in winding]
    assert all(not s.machine_stopped for s in winding), winding
    assert all(s.label == WINDING_LABEL for s in winding), [s.label for s in winding]
    assert r.parallel_hours > 10.0, r.parallel_hours


# ——— 2. чинка «в течение всей смены» ———

def test_91_shift_long_repair_does_not_stop_the_machine():
    """Ст.91 за 24.09: фон на всю смену не может быть 11,33 ч простоя."""
    r = _r("91")
    rows = _downtime(r)
    assert "чинка/ремонт" not in rows, rows
    assert r.work_hours == 7.22, r.work_hours
    assert abs(r.machine_balance - MACHINE_SHIFT_HOURS) <= TOLERANCE_H, r.machine_balance
    # Названные остановки на месте: подмена и снятие/установка валов.
    assert rows["подмена (заправка полотна)"] == 1.0, rows


def test_91_operator_hours_fit_the_shift():
    """Часы оператора за 91-й: строка на всю смену не складывается с контролем.

    8,47 ч = 7,97 названных операций + 0,5 ч у работающего станка. Добивки до
    нормы 11,25 нет (приказ владельца от 28.09.2026), поэтому нехватку видно
    как «недостаток», а не как ровную норму в бланке.
    """
    r = _r("91")
    assert r.operator_hours == 8.47, (r.operator_hours,
                                      r.operator_explicit_hours,
                                      r.operator_control_hours)
    assert abs(r.operator_hours - (r.operator_explicit_hours
                                   + r.operator_control_hours)) < 0.01
    assert r.operator_excess == 0.0, r.operator_excess


def test_timed_repair_still_stops_the_machine():
    """Правило А не сломано: чинка с названным временем — простой станка."""
    short = TechStop(0.5, "чинка полотна", start_min=636, end_min=666)
    assert short.machine_stopped is True
    assert machine_stopped_for("10:36 чинка, 10:39 чинка завершена") is True


def test_shift_long_repair_row_is_marked_parallel():
    """Флаг окна «на всю смену» перевешивает слово «чинка» в причине."""
    long_row = TechStop(12.0, "замена шпулярника и чинка в течении всей смены",
                        is_parallel=True, shiftlong_parallel=True,
                        start_min=420, end_min=1140)
    assert long_row.machine_stopped is False


# ——— 3. род работы от ИИ важнее нераспознанного текста ———

def _synthetic(events):
    data = {"date": "24.09.2026", "warnings": [], "machines": [{
        "machine": "98", "operator": "Елистратова", "cuts": 1,
        "lines": [{"assortment": "187×3", "client": "СКЛАД", "order_no": "49",
                   "counter_start": 21530600, "counter_end": 21541700}],
        "events": events}]}
    return build_shift_reports_from_structured(data)[0]


def test_winding_kind_survives_unfamiliar_wording():
    """Модель назвала род работы «навивка» — ярлык и параллельность берём из него."""
    r = _synthetic([{"start": "08:00", "end": "10:00", "kind": "навивка",
                     "reason": "поднесла короба", "performer": None,
                     "other_machine": None}])
    assert classify_stop("поднесла короба")[0] == OTHER_LABEL
    row = r.tech_stops[0]
    assert row.label == WINDING_LABEL, row.label
    assert row.machine_stopped is False


def test_winding_kind_with_stop_word_is_still_downtime():
    """Но если мастер написал «стоп» — останов на месте, род работы его не отменяет."""
    r = _synthetic([{"start": "10:00", "end": "11:00", "kind": "навивка",
                     "reason": "стоп, навивка шпулей", "performer": None,
                     "other_machine": None}])
    assert r.tech_stops[0].machine_stopped is True


# ——— 5. колонка «Технические остановки» в Excel ———

def test_repair_word_beats_submen_marker_in_label():
    """«замена шпулярника и чинка» — не подмена: ремонт сильнее (правило 22.09, п.8).

    Маркер «шпулярник» считал такую строку подменой, а подмены в колонку
    «Технические остановки» не пишутся — 2,78 ч простоя молча исчезали из файла.
    """
    assert is_submen_label("замена шпулярника и чинка в течении всей смены") is False
    assert is_submen_label("подмена (заправка полотна)") is True
    # Шпулярник меняют люди, поэтому такая строка у владельца — подмена (файл
    # за 22.09, ст.151: 8,66 ч).
    assert is_submen_label("замена шпулярника (станок стоит)") is True


def test_material_swap_label_is_not_a_submen_even_without_repair():
    """Приказ владельца 30.09.2026: «заменой одной катушки это не подмена».

    Раньше подменой считались любые ярлыки со словом «бобина», и 56 окон
    «замена N бобины» на ст.321 за 29.09 дали 56 подмен, хотя мастер писал
    «Подмена - 5 из 6». В файле владельца эти часы стоят в технических
    остановках: «3,25+7,32+0,1 | чинка+замена шпулярника+отсутв опер», а клетка
    «подмены, ч» у станка пуста.

    Гейт стоит не в `is_real_submen`: она отвечает только на вопрос «названа ли
    операция замены», то есть «стоял ли станок», и её запрет на материал тихо
    терял 0,55 ч простоя (прогон 15.09, ст.251 — строка уезжала в параллельную
    работу). «Это подмена человека» решают три других места: `is_submen_label`
    (колонка J и привлечённые), `is_submen_window` (колонка I) и `person_submen`
    (счётчик подмен).
    """
    for label in ("замена 21 бобины",
                  BOBBIN_LABEL,
                  "замена катушки на шпулярнике",
                  "сошли 13 бобин на шпулярнике, замена 57 бобины"):
        assert is_submen_label(label) is False, label
        assert is_real_submen(label) is True, label
    # Шпуля — не единица сырья: это подменщик у машины.
    assert is_submen_label("подмена 250 шпулей") is True


def test_bobbin_swap_named_submen_stays_downtime_without_a_submen():
    """Слово мастера «подмена» плюс бобины: станок стоит, подмены человека нет."""
    r = _synthetic([{"start": "10:00", "end": "11:00", "kind": "подмена",
                     "reason": "Замена 21 бобины", "performer": "Иванова",
                     "other_machine": None}])
    row = r.tech_stops[0]
    assert (row.machine_stopped, row.submen_recorded, row.is_sub) == (True, True, False), row
    assert _downtime(r)[BOBBIN_LABEL] == 1.0, _downtime(r)
    assert _subs_hours_cell(r) == "", _subs_hours_cell(r)
    assert r.subs_count in (None, 0), r.subs_count


def test_material_swap_row_is_named_creel_replacement():
    """Приказ владельца 30.09.2026: «замена сырья это замена шпулярника».

    Мастер пишет «замена 21 бобины», а в его же ручном файле за 29.09 те самые
    часы стоят строкой «замена шпулярника» (7,32 ч) в технических остановках
    станка 321 при пустых «подмены, ч» и «Кол-во подмен». Значит операция
    называется работой на шпулярнике, а не «подстановкой бобин». Слово «бобин»
    в ярлыке остаётся намеренно: по нему `is_material_swap` не пускает строку в
    подмены человека, и её часы не исчезают из колонки «Технические остановки».
    """
    r = _synthetic([{"start": "10:00", "end": "11:00", "kind": "остановка",
                     "reason": "Замена 21 бобины", "performer": None,
                     "other_machine": None}])
    label = r.tech_stops[0].label
    assert "шпулярник" in label, label
    assert _downtime(r)[BOBBIN_LABEL] == 1.0, _downtime(r)
    q, reason = _stops_columns(r)
    assert (q, reason) == (1.0, BOBBIN_LABEL), (q, reason)
    assert _subs_hours_cell(r) == "", _subs_hours_cell(r)


def test_321_2909_material_hours_stay_in_tech_stops_named_creel():
    """Ст.321 за 29.09: 7,03 ч замены бобин — строка «замена шпулярника», не подмена.

    Дословные строки мастера — «замена 21 бобины», «замена 22 бобины», … (58
    записей), род модель назвала «остановка». В его файле Q = «3,25+7,32+0,1»,
    R = «чинка+замена шпулярника+отсутв опер», I и S пустые.
    """
    r = _r("321", "ai_raw_29_09_2026.json")
    rows = _downtime(r)
    assert rows[BOBBIN_LABEL] == 7.03, rows
    _q, reason = _stops_columns(r)
    assert "замена шпулярника" in reason, reason
    assert "подстановка/подмена бобин" not in reason, reason
    assert _subs_hours_cell(r) == "", _subs_hours_cell(r)
    assert r.subs_count in (None, 0), r.subs_count


def test_named_submen_without_material_is_still_a_submen():
    """Контроль той же ветки: подмена без единицы сырья считается подменой."""
    r = _synthetic([{"start": "10:00", "end": "11:00", "kind": "подмена",
                     "reason": "подмена 250 шпулей", "performer": "Иванова",
                     "other_machine": None}])
    assert r.tech_stops[0].is_sub is True, r.tech_stops[0]
    assert _subs_hours_cell(r) == 1.0, _subs_hours_cell(r)
    assert r.subs_count == 1, r.subs_count
    assert _stops_columns(r) == ("", ""), _stops_columns(r)
    # «Кол-во подмен» не заполняется и там, где подмена была: приказ 30.09 на
    # вопрос про эту колонку — «не заполняй колонку» (в его файле клетка пуста у
    # всех десяти станков, в том числе у 98-го с 1,66 ч подмен).
    assert _result_columns(r, r.lines[0])["S"] == "", r.subs_count


def test_91_excel_tech_stops_column_keeps_repair_hours():
    """Ст.91 в Excel: «2,78+0,25 | замена шпулярника и чинка…+валы», а не 0,25.

    0,25 ч — «сняли вал, поставили вал»: раньше причина уходила в безликую
    «прочее», теперь названа словами мастера (правило 5 — причины словами).
    """
    hours, reasons = _stops_columns(_r("91"))
    assert hours == "2,78+0,25", hours
    assert reasons == ("замена шпулярника и чинка в течении всей смены"
                       "+снятие и установка валов"), reasons


def test_98_excel_tech_stops_column_stays_empty():
    """Ст.98 — эталон 22.09: подмены и обед в свою колонку, тех. остановки пустые."""
    assert _stops_columns(_r("98")) == ("", "")


# ——— 6. инвариант смены ———

def test_no_machine_of_24_09_stands_and_runs_at_once():
    """Ни один станок дня не «стоял» дольше, чем allows его работа по счётчику.

    Без этого правила отчёт за 24.09 показывал ст.98 на 22,28 ч, а ст.91 на
    19,88 ч при 12-часовой смене. Обед (45 мин) накладывается на счётчик и
    превышением не считается — см. machine_excess.
    """
    for r in _reports():
        over = round(r.machine_balance - r.lunch_in_downtime, 2)
        assert over <= MACHINE_SHIFT_HOURS + TOLERANCE_H, (r.machine, over)
        assert r.machine_excess == 0.0, (r.machine, r.machine_excess)
        assert r.machine_shortfall == 0.0, (r.machine, r.machine_shortfall)


# ——— 7. второй прогон того же дня через ИИ: те же правила, другие окна ———

DRIFT = "ai_raw_24_09_2026_drift.json"


def test_shiftlong_row_without_times_gets_the_shift_window():
    """Запись «в течении всей смены» без start/end = окно 07:00–19:00.

    Во втором прогоне 24.09 модель вернула строки ст.91 («замена шпулярника в
    течении всей смены», «чинка в течении всей смены») вообще без времени. Прежний
    код превращал их в «простой — время не указано у причин…» на остаток смены, и
    отчёт сам себе противоречил: рядом стояла строка «!!! РАСХОЖДЕНИЕ: станок
    −2,78 ч до 12 ч». Слова мастера уже называют окно — берём его, а не остаток.
    """
    r = _r("91", DRIFT)
    rows = [s for s in r.tech_stops if s.shiftlong_parallel]
    assert len(rows) == 2, [(s.reason, s.hours) for s in rows]
    assert all(s.hours == MACHINE_SHIFT_HOURS for s in rows), rows
    assert all((s.start_min, s.end_min) == (420, 1140) for s in rows), rows
    assert all(s.machine_stopped is False for s in rows), rows
    assert r.unexplained_downtime == 0.0, r.unexplained_downtime


def test_shiftlong_residual_is_named_by_masters_words():
    """Остаток смены у 91-го — «замена шпулярника в течении всей смены», а не «прочее».

    Это же окно мастер называет и в колонке «Технические остановки»: 2,78+0,25 ч
    вместо молчаливых 0,25.
    """
    hours, reasons = _stops_columns(_r("91", DRIFT))
    assert hours == "2,78+0,25", hours
    assert hours == _stops_columns(_r("91"))[0], "прогоны разошлись в часах"


def test_creel_swap_under_work_kind_is_counted_but_not_a_submen_window():
    """Ст.321: «Меняю на шпулярнике…» — не подмена вовсе.

    Первый прогон вернул ту же запись kind «остановка», и часы колонки «подмены, ч»
    брались из цифры ИИ, а «Кол-во подмен» оставалось пустым. Во втором прогоне —
    наоборот. Счётчик подмен обязан сходиться при любом kind, а часы нет: у
    мастера в ручном файле (27.09) клетка «подмены, ч» заполнена только вместе с
    фамилей подменщика в «фио на подменах». Здесь подменщика нет — это оператор
    меняет шпулярник на СВОЕЙ машине, поэтому клетка остаётся пустой (правило
    29.09.2026: запасной путь по r.subs_hours убран, колонка I считает только
    окно, названное подменой).

    С 30.09.2026 та же запись не становится и «подменой» в счётчике: приказ
    владельца «строку мастера считай подменой, заменой одной катушки это не
    подмена». Окно остаётся простоем станка, но подменой оно считается только
    по слову мастера, а здесь мастер писал про замену шпулярника.
    """
    for fixture in (DRIFT, "ai_raw_24_09_2026.json"):
        r = _r("321", fixture)
        assert submen_hours(r) == 0.0, (fixture, submen_hours(r))
        assert _subs_hours_cell(r) == "", (fixture, _subs_hours_cell(r))
        assert not [s for s in r.tech_stops if s.is_sub], (
            fixture, [(s.hours, s.reason) for s in r.tech_stops if s.is_sub])
        assert r.subs_count in (None, 0), (fixture, r.subs_count)


def test_submen_count_is_never_below_the_windows_the_code_sees():
    """Цифра ИИ не отменяет посчитанные окна: ст.111 за 24.09 — 3 подмены.

    Во втором прогоне модель вернула subs_count=1 при ТРЁХ названных окнах подмены
    и трёх фамилиях в «фио на подменах» — в отчёте человек читал «1» и видел троих.
    """
    r = _r("111", DRIFT)
    subs = [s for s in r.tech_stops if s.is_sub]
    assert len(subs) == 3, [(s.hours, s.reason) for s in subs]
    assert r.subs_count == 3, r.subs_count


def test_whole_shift_creel_swap_does_not_become_a_submen_window():
    """Фон «замена шпулярника в теч.смены» — не подмена: станок шёл 10,28 ч.

    Та же строка без слов о всей смене (случай выше) — подмена с окном и счётчиком.
    """
    r = _r("98", DRIFT)
    assert _stops_columns(r) == ("", ""), _stops_columns(r)
    assert r.subs_count == 2, r.subs_count
    assert r.work_hours == 10.28, r.work_hours


def test_both_24_09_parses_agree_on_counters_and_shift_hours():
    """Что не зависит от формулировок модели, не разъезжается между прогонами.

    Выпуск и время работы — из счётчика, окна «на всю смену» — из правил: они
    обязаны совпасть байт в байт. Часы оператора считаются из окон, которые
    модель каждый раз режет заново (на ст.321 во втором прогоне 26 записей
    дотягивания вместо 24 — те же минуты на границах), поэтому с них
    спрашивается только потолок смены и расхождение в пределах допуска ±20 мин.

    Раньше расхождение прятала добивка до нормы: оба прогона закрывались в
    11,25, и тест казался зелёным. Приказ владельца от 28.09.2026 — норму не
    подгонять, поэтому разница окон видна честными 0,12 ч (7 минут).
    """
    acc = {str(r.machine): r for r in _reports()}
    drift = {str(r.machine): r for r in _reports(DRIFT)}
    assert sorted(acc) == sorted(drift)
    for num in ("91", "98", "111", "321"):
        a, d = acc[num], drift[num]
        assert a.work_hours == d.work_hours, (num, a.work_hours, d.work_hours)
        assert a.output_total == d.output_total, num
        assert abs(a.operator_hours - d.operator_hours) <= TOLERANCE_H, (
            num, a.operator_hours, d.operator_hours)
    for r in drift.values():
        over = round(r.machine_balance - r.lunch_in_downtime, 2)
        assert over <= MACHINE_SHIFT_HOURS + TOLERANCE_H, (r.machine, over)
        assert r.operator_hours <= MACHINE_SHIFT_HOURS + TOLERANCE_H, (
            r.machine, r.operator_hours)
