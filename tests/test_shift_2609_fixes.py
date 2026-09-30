# -*- coding: utf-8 -*-
"""Регрессии аудита отчёта за 26.09.2026 (данные пользователя, файл 26.09.2026.csv).

Пользователь: «проверь данные за 26 число... как будто там есть ошибки... по 98
машине как минимум... если увидишь какие то недочеты в работе то перекодируй их
чтобы таких ошибок больше не повторялось». Нашли девять дефектов (пять в самом
отчёте, четыре в листе «Остатки»); каждый заперт здесь отдельным тестом.

Сырьё тестов — зафиксированный снимок накопителя `tests/store_26_09_2026/`
(переписка day.json, оплаченный разбор ai_raw.json и журнал счётчиков), а не
живой `data/shifts/`: платный прогон перезаписывает разбор, а проверять нужно
детерминированный слой — арифметику и правила, закодированные в коде.

1. Станок-призрак №151. В теме 251-й мастер пишет размер полотна
   «днпа 187х6-100,0-151,5» и просит помощь «на 151м». Подсказка по станкам
   родила №151, модель собрала его из ЧУЖИХ сообщений — со своим оператором,
   10,09 ч «наладка (весь день)» и чужими счётчиками.
2. Ст.98: «выпуск 0 / работа 0 ч / простой 12 ч» по станку, который закрыл
   срез и дал 7 срезов. Причина — единственное показание счётчика, записанное
   и в начало, и в конец смены.
3. Правило 0 поднимало начало смены ровно до этого единственного показания,
   то есть до конца — выпуск так и оставался нулевым.
4. Ст.251: «Общ сч 391938» утром вместо 301938 и 303370 вечером дали
   −88 568 узлов в Excel и «станок простаивал весь день».
5. Ст.321: 1,11 кг выдуманной сдачи отходов. Это вес ОДНОЙ бобины из
   инструкции мастера («бобинами ориентировочным весом 1,11кг»), а не то, что
   станок сдал в отходы.
6. Ст.251: настоящая сдача «Развивка 3х шпуль окончена Вес 2,8кг» висела
   вопросом «назовите вид отходов» — маркер знал только родительный падеж.
7. Ст.251: тот же вопрос про 0.12 кг — это вес из ПРИМЕРА, как подписывать
   запись в журнале, его никто не взвешивал.
8. Ст.321: вопрос про 1,400 кг — паспортный вес бобины в скобках, которым
   мастер отвечает руководителю («на их место маленькие (1,400 кг)»).
9. Ст.111 за 21.09.2026 (проверка, что правка 6 ничего не сдвинула): строчная
   «развивка» в описании работы не должна перекрывать строку бланка той же
   смены, а «10, 4кг» из выгрузки — это одно число, а не «4».

Десятая проверка — не дефект 26.09, а дыра, которую чистка журнала вскрывает:
незаписанный `counter_ledger.json` молчал (только в лог), а из него завтра
берётся начало смены. Теперь он называется в предупреждениях ответа.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import counter_check, day_pipeline, remains_report, shift_store

DATE = "26.09.2026"
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "store_26_09_2026")


def _num(value):
    return int(value) if value is not None and float(value) == int(value) \
        else value


# ---------------------------------------------------------------------------
# 1. Станок, которого нет в переписке
# ---------------------------------------------------------------------------

def test_raw_is_cleaned_of_stations_absent_from_topics():
    """№151 есть в оплаченном разборе, но нет ни в одной теме — разбор вон."""
    with open(os.path.join(STORE, DATE, "ai_raw.json"), encoding="utf-8") as f:
        raw = json.load(f)
    result = day_pipeline.DayResult(date=DATE, machines=[
        "251", "321", "111", "98", "121", "601", "91", "14тр", "15тр"])
    day_pipeline._drop_invented_machines(raw, result.machines, result)
    left = [str(m.get("machine")) for m in raw["machines"]]
    assert "151" not in left, left
    assert sorted(left) == sorted(["251", "321", "111", "98", "121", "601",
                                   "14тр", "15тр"]), left
    # Тема «91 машина» в переписке есть, а разбора по ней модель не дала —
    # отсутствующий станок чистильщик не трогает.
    assert "91" in result.machines, result.machines
    assert any("151" in w for w in result.ai_warnings), result.ai_warnings
    print("✅ разбор ИИ очищен от станка-призрака №151")


def test_stations_stay_when_shift_has_no_topics():
    """Смена одним текстом (тегов тем нет) — по догадке по тексту ничего не выбрасываем.

    Прогон тестов сайта 19/20.08.2026: там переписка приходит одним сообщением,
    станки в ней названы только словами. «Знать» список станков по догадке —
    не доказательство, что станка в смене не было; по такому списку №251 и №321
    и пропали бы из отчёта.
    """
    raw = {"machines": [{"machine": "151", "lines": []},
                        {"machine": "98", "lines": []}]}
    result = day_pipeline.DayResult(date=DATE, machines=[])
    day_pipeline._drop_invented_machines(raw, None, result)
    assert len(raw["machines"]) == 2, raw
    assert not result.ai_warnings, result.ai_warnings
    print("✅ без точных тем подсказку по тексту не считаем ложью")


def test_topics_win_over_number_guessed_from_text():
    """Номер станка из заголовка темы надёжнее догадки: «151,5» в размере полотна — не станок."""
    tmp = tempfile.mkdtemp()
    try:
        keep = shift_store.get_data_dir()
        shift_store.set_data_dir(tmp)
        shift_store.add_message(
            text="251 машина(Гудкова) - информация за смену 26.09.2026г.\n"
                 "Ассортимент днпа 187х6-80,0-151,5\n"
                 "помощь Петровского О на 151м",
            date=DATE, machine="251")
        assert shift_store.day_machines(DATE) == ["251"], \
            shift_store.day_machines(DATE)
        print("✅ тема станка задаёт список, «151» из текста не рождает станок")
    finally:
        shift_store.set_data_dir(keep)
        shutil.rmtree(tmp, ignore_errors=True)


def test_stations_are_guessed_from_text_when_source_has_no_tags():
    """Рядом с точной разметкой должен остаться и старый способ: смена без тем."""
    tmp = tempfile.mkdtemp()
    try:
        keep = shift_store.get_data_dir()
        shift_store.set_data_dir(tmp)
        shift_store.add_message(
            text="251 машина(Гудкова) - информация за смену 26.09.2026г.\n"
                 "7:00 машина в работе, Общ сч 301938",
            date=DATE)
        assert "251" in shift_store.day_machines(DATE), \
            shift_store.day_machines(DATE)
        print("✅ смена без заголовков тем по-прежнему читается догадкой")
    finally:
        shift_store.set_data_dir(keep)
        shutil.rmtree(tmp, ignore_errors=True)


def test_topic_tags_are_exact_only_when_every_message_has_one():
    """Точный список станков есть, только если тег темы получен на КАЖДОЕ сообщение."""
    tmp = tempfile.mkdtemp()
    try:
        keep = shift_store.get_data_dir()
        shift_store.set_data_dir(tmp)
        shift_store.add_message(text="98 машина(Белякова) - смена 26.09.2026",
                                date=DATE, machine="98")
        assert shift_store.day_topic_tags(DATE) == ["98"]
        # Одно сообщение без тега — и список станков снова догадка: по нему
        # станок из разбора выбрасывать нельзя.
        shift_store.add_message(text="321 машина(Демидов) - смена 26.09.2026",
                                date=DATE)
        assert shift_store.day_topic_tags(DATE) is None
        print("✅ список тем точен, только если размечена вся переписка")
    finally:
        shift_store.set_data_dir(keep)
        shutil.rmtree(tmp, ignore_errors=True)


def test_store_snapshot_topics_are_exact():
    """В снятом на 26.09 экспорте тема указана у каждого сообщения."""
    tmp = _store_dir()
    keep = shift_store.get_data_dir()
    try:
        shift_store.set_data_dir(tmp)
        tags = shift_store.day_topic_tags(DATE)
        assert tags and "151" not in tags, tags
        assert "91" in tags, tags        # тема есть — разбора по ней модель не дала
        assert sorted(tags) == sorted(shift_store.day_machines(DATE)), tags
        print("✅ выгрузка 26.09 размечена темами полностью")
    finally:
        shift_store.set_data_dir(keep)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2–3. Единственное показание за смену (ст.98)
# ---------------------------------------------------------------------------

def _store_dir():
    """Копия снимка накопителя в песочнице: перенос и журнал не трогают data/."""
    tmp = tempfile.mkdtemp()
    shutil.copytree(STORE, tmp, dirs_exist_ok=True)
    return tmp


def test_single_reading_is_recognised_as_missing_morning():
    """Один счётчик, записанный дважды (в начало и в конец), — это «утра не было»."""
    tmp = _store_dir()
    keep = shift_store.get_data_dir()
    try:
        shift_store.set_data_dir(tmp)
        lines = [{"counter_start": 21563900, "counter_end": 21563900}]
        assert day_pipeline._single_reading_needs_carry("98", lines, DATE)
        # Другое дело — две разных показания: разбивку по ассортиментам не трогаем.
        two = [{"counter_start": 21552800, "counter_end": 21563900}]
        assert not day_pipeline._single_reading_needs_carry("98", two, DATE)
        # Станок без согласованной нормы доказать нечем: оставляем как есть.
        assert not day_pipeline._single_reading_needs_carry(
            "601", [{"counter_start": 100, "counter_end": 100}], DATE)
        print("✅ единственное показание распознано, а парное — нет")
    finally:
        shift_store.set_data_dir(keep)
        shutil.rmtree(tmp, ignore_errors=True)


def test_98_gets_the_morning_counter_from_previous_shift():
    """Ст.98 за 26.09: начало берётся с 25.09, а не остаётся равным концу."""
    tmp = _store_dir()
    keep = shift_store.get_data_dir()
    try:
        shift_store.set_data_dir(tmp)
        with open(os.path.join(STORE, DATE, "ai_raw.json"), encoding="utf-8") as f:
            data = json.load(f)
        result = day_pipeline.DayResult(date=DATE)
        day_pipeline._carry_over_counters(data, DATE, result)
        m98 = next(m for m in data["machines"] if str(m.get("machine")) == "98")
        start = day_pipeline._machine_start_counter(m98)
        end = day_pipeline._machine_end_counter(m98)
        assert start == 21552800, (_num(start), _num(end))
        assert end == 21563900, _num(end)
        assert any("единственным" in n for n in result.carry_notes), \
            result.carry_notes
        print("✅ ст.98: начало 21552800 с прошлой смены, конец 21563900")
    finally:
        shift_store.set_data_dir(keep)
        shutil.rmtree(tmp, ignore_errors=True)


# 21.09.2026, ст.98: начало взяли из утренней сводки мастера, хотя Белякова
# сама написала «7.00 машина в работе … Сч 21497900».
MORNING_SUMMARY = (
    "Ирина П., 06:38: 98 машина(Белякова) - информация за смену 21.09.2026г.\n"
    "состояние до начала смены, Общ. Сч 21486750\n"
    "Белякова С., 06:53: 7.00 машина в работе, Сч 21497900\n"
    "Белякова С., 19:05: 19.00 конец смены, общий счётчик 21509000")


def test_shift_start_is_the_operators_own_reading():
    """Правило 0 работает: своё показание оператора больше утренней сводки."""
    start, end, notes = counter_check.check_counters(
        "98", 21486750, 21509000, text=MORNING_SUMMARY,
        operator="Белякова")
    assert start == 21497900, (_num(start), _num(end))
    assert any("утренней сводки" in n for n in notes), notes
    print("✅ начало смены — показание оператора, а не сводка мастера")


def test_start_is_not_raised_up_to_the_end_value():
    """Правило 0 не должно превращать смену в «выпуск 0».

    Прогон 26.09.2026, ст.98: после переноса начало 21552800, а единственное
    показание Беляковой 21563900 — это КОНЕЦ смены. Поднять начало до него
    значило бы оставить станок с нулевым выпуском и 12 ч простоя.
    """
    text = ("Ирина П., 06:38: 98 машина(Белякова) - информация за смену "
            "26.09.2026г. Общ. Сч 21552800\n"
            "Белякова С., 07:03: 7:00 начало работы, Общ. Сч 21563900")
    start, end, notes = counter_check.check_counters(
        "98", 21552800, 21563900, text=text, operator="Белякова")
    assert start == 21552800, (_num(start), _num(end))
    assert not any("утренней сводки" in n for n in notes), notes
    print("✅ начало не поднимается до конца смены")


# ---------------------------------------------------------------------------
# 4. Счётчик назад не отматывается (ст.251)
# ---------------------------------------------------------------------------

def test_reversed_pair_is_repaired_from_the_previous_shift():
    """Ст.251 за 26.09: 391938 -> 303370 лечится показанием за 25.09 (301901)."""
    start, end, notes = counter_check.check_counters(
        "251", 391938, 303370, prev_end=301901, prev_is_adjacent=True)
    assert start == 301901, (_num(start), _num(end))
    assert end == 303370, _num(end)
    assert any("больше конечного" in n and "с прошлой смены" in n
               for n in notes), notes
    assert start < end
    print("✅ перевёрнутая пара исправлена показанием прошлой смены")


def test_reversed_pair_without_evidence_books_no_output():
    """Доказательств нет — начала нет: отчёт отдаёт «нет данных», а не минус."""
    start, end, notes = counter_check.check_counters("251", 391938, 303370)
    assert start is None, (_num(start), _num(end))
    assert end == 303370, _num(end)
    assert any("выпуск и время по станку не" in n for n in notes), notes
    print("✅ без доказательств отрицательный выпуск в отчёт не идёт")


# ---------------------------------------------------------------------------
# 5. Приблизительный вес и паспортный вес бобины — не сдача отходов (ст.321)
# ---------------------------------------------------------------------------

# Прогон 26.09.2026, ст.321: мастер разоблачает инструкцию от 24.09, а разбор
# увидел в ней сдачу отходов — 1,11 кг уехали в лист «Остатки».
BOBBIN_INSTRUCTION_321 = (
    "Погодина Л., 15:10: На шпулярнике , в первую очередь сойдут 5 маловесных"
    " бобин, сошедшие бобины подставлять(бобинами ориентировочным весом 1,11кг"
    " это чистый вес . Вес вместе со втулкой 1,405кг-1,410кг) Данные бобины"
    " получаем, путем навивки шпулей.")


def test_approximate_weight_is_not_shift_waste():
    """«Ориентировочным весом 1,11кг» — вес бобины, а не сданные отходы."""
    day = remains_report.build_day_remains(DATE, messages=[
        {"machine": "321", "text": BOBBIN_INSTRUCTION_321}])
    b = day.by_machine("321")
    assert (b is None or not b.waste), b.waste if b else None
    assert day.waste_total == 0, day.waste_total
    assert not [w for w in day.warnings if "1,11" in w], day.warnings
    # Настоящая сдача в той же теме остаётся.
    both = remains_report.build_day_remains(DATE, messages=[
        {"machine": "321", "text": BOBBIN_INSTRUCTION_321},
        {"machine": "321",
         "text": "Конец смены. Отходы от навивки шпулей = 4,5 кг"}])
    assert both.by_machine("321").waste == {"навивка_шпулей": 4.5}, \
        both.by_machine("321").waste
    print("✅ примерный вес бобины не стал сдачей отходов")


# Прогон 26.09.2026, ст.321: «Мне сказали большие снимать на их место маленькие
# (1,400 кг)» — разговор про вес одной катушки со шпулярника.
UNIT_WEIGHT_321 = ("Евгений Демидов, 15:18: Мне сказали большие снимать на их"
                   " место маленькие (1,400 кг) Тогда с поддона брать?")


def test_per_unit_weight_is_not_shift_waste():
    """«катушек (весом ± 1,400 кг)» и «маленькие (1,400 кг)» — не отходы смены."""
    for text in ("подготовка катушек (весом ± 1,400 кг)", UNIT_WEIGHT_321):
        day = remains_report.build_day_remains(
            DATE, messages=[{"machine": "321", "text": text}])
        assert day.waste_total == 0, (text, day.waste_total)
        # Килограммы в скобках — ответ на вопрос про размер бобины. Требовать к
        # ним вид отходов не у кого: их никто не взвешивал и не сдавал.
        assert not [w for w in day.warnings if "не назван" in w], (text,
                                                                   day.warnings)
    print("✅ вес одной катушки не попал в отходы и не стал вопросом")


# ---------------------------------------------------------------------------
# 6. Сдача 251-й названа словами, а пример записи — не сдача (ст.251, 26.09)
# ---------------------------------------------------------------------------

# Прогон 26.09.2026, ст.251: «Развивка 3х шпуль окончена Вес 2,8кг». Вид
# назван, но ИМЕНИТЕЛЬНЫМ падежом с большой буквы — разбор знал только
# родительный («развивки шпул») и просил назвать вид отходов к тем же 2,8 кг.
DEVELOP_DONE_251 = ("Инна Гудкова, 11:34: Развивка 3х шпуль окончена"
                    " Вес 2,8кг")

# Тот же день, ст.251, 16:11: руководитель объясняет, как подписывать журнал.
# Разбор требовал «скажите, к какому виду отнести 0.12 кг».
LOG_EXAMPLE_251 = (
    "Погодина Л., 16:11: 27.09 развивка шпулей с НПА 187х6 машина 251"
    " производится следующим образом. 1 сматывать шпули по 5 шт по порядковым"
    " номерам и с одного шпинделя например( шпиндель 3 , шпули с 20 по 25) 2"
    " завесить отходы со смотанных 5 шпулей 3 произвести запись например ("
    " шпиндель 3, шпули с 20 по 25 , вес 0.12 кг)")


def test_nominative_report_sentence_names_the_waste_kind():
    """«Развивка 3х шпуль окончена Вес 2,8кг» — развивка 2,8 кг, вопроса нет."""
    day = remains_report.build_day_remains(DATE, messages=[
        {"machine": "251", "text": DEVELOP_DONE_251}])
    assert day.by_machine("251").waste == {"развивка_шпулей": 2.8}, \
        day.by_machine("251").waste
    assert day.waste_total == 2.8, day.waste_total
    assert not [w for w in day.warnings if "не назван" in w], day.warnings
    print("✅ сдача, названная именительным падежом, лежит в своём виде")


def test_lowercase_process_note_does_not_rename_a_handover():
    """Строчная «развивка» в описании работы не перекрывает строку бланка.

    Ст.111 за 21.09.2026: «10:59 развивка шпуль в кол-ве 750шт окончена» и
    «Вес отходов после подмены 10, 4кг» — один и тот же сход ниток, подписанный
    в конце смены бланком («Шпульные отходы - 10,4кг»). Маркер с малой буквы
    приклеил бы к этой сдаче второй вид, и в лист «Остатки» леглось бы 20,8 кг
    вместо 10,4. Заодно «10, 4кг» — одно число, а не «4»: пробел после запятой
    вставляет выгрузка чата.
    """
    day = remains_report.build_day_remains("21.09.2026", messages=[
        {"machine": "111",
         "text": "Инна Гудкова, 10:59: 10:59 развивка шпуль в кол-ве 750шт"
                 " окончена Машина в работе\nИнна Гудкова, 11:10: 10:59 -11:10"
                 " упаковка, взвешивание отходов Вес отходов после подмены"
                 " 10, 4кг Машина в работе"},
        {"machine": "111",
         "text": "Конец смены\nШпульный стол в рабочем состоянии, отклонений"
                 " нет Отходы: Шпульные отходы - 10,4кг"}])
    b = day.by_machine("111")
    assert b.waste == {"навивка_шпулей": 10.4}, b.waste
    assert day.waste_total == 10.4, day.waste_total
    assert not [w for w in day.warnings if "не назван" in w], day.warnings
    print("✅ строчная «развивка» не удвоила сдачу и не попросила назвать вид")


def test_example_of_a_log_entry_is_not_shift_waste():
    """Вес из примера записи журнала — не сдача и не вопрос мастеру."""
    day = remains_report.build_day_remains(DATE, messages=[
        {"machine": "251", "text": LOG_EXAMPLE_251}])
    assert day.waste_total == 0, day.waste_total
    assert not [w for w in day.warnings if "не назван" in w], day.warnings
    # Пример закончился закрывающей скобкой — настоящая сдача после него
    # по-прежнему считается.
    both = remains_report.build_day_remains(DATE, messages=[
        {"machine": "251",
         "text": LOG_EXAMPLE_251 + "\nИнна Гудкова, 18:20: Отходы от навивки"
                                   " шпулей 3кг"}])
    assert both.by_machine("251").waste == {"навивка_шпулей": 3.0}, \
        both.by_machine("251").waste
    print("✅ пример записи не стал сдачей и не просит назвать вид отходов")


# ---------------------------------------------------------------------------
# Всё вместе: бесплатная пересборка 26.09 из зафиксированного разбора
# ---------------------------------------------------------------------------

def test_free_rebuild_of_26_09():
    """Пересборка без ИИ: призрака нет, 98 работает, 251 не в минусе, 321 без 1,11 кг."""
    tmp = _store_dir()
    keep_data = shift_store.get_data_dir()
    keep_ai = os.environ.get("AI_ENABLED")
    try:
        os.environ["AI_ENABLED"] = "false"
        shift_store.set_data_dir(tmp)
        result = day_pipeline.build_day_local(DATE)
        nums = [str(m) for m in result.machines]
        assert "151" not in nums, nums
        reps = {str(r.machine): r for r in result.reports}
        assert "151" not in reps, sorted(reps)
        assert any("151" in w for w in result.ai_warnings), result.ai_warnings

        r98 = reps["98"]
        assert r98.output_total == 11100, (_num(r98.output_total),
                                           r98.warnings)
        assert r98.work_hours > 10, r98.work_hours
        assert 0 <= r98.work_hours + r98.tech_stops_total <= 12 + 0.01, \
            (r98.work_hours, r98.tech_stops_total)

        r251 = reps["251"]
        assert r251.output_total == 1469, (_num(r251.output_total),
                                           r251.warnings)
        assert r251.work_hours > 0, r251.work_hours

        w321 = result.remains.by_machine("321")
        assert not [v for v in (w321.waste.values() if w321 else [])
                    if v and abs(v - 1.11) < 1e-6], w321.waste if w321 else None
        w251 = result.remains.by_machine("251")
        assert w251.waste == {"развивка_шпулей": 2.8}, \
            w251.waste if w251 else None
        assert not [w for w in result.remains.warnings if "не назван" in w], \
            result.remains.warnings
        # Журнал счётчиков: у призрак-станка записи за эту дату оставаться не
        # должно, иначе 27.09 №151 получит начало смены от несуществовавшей смены.
        with open(os.path.join(tmp, "counter_ledger.json"),
                  encoding="utf-8") as f:
            ledger = json.load(f)
        assert "26.09.2026" not in (ledger.get("151") or {}), ledger.get("151")
        assert ledger["251"]["26.09.2026"] == 303370, ledger["251"]
        print("✅ бесплатная пересборка 26.09: №151 нет (и в журнале тоже),"
              " №98 = 11100 узлов, №251 = +1469 и 2,8 кг, №321 без 1,11 кг")
    finally:
        shift_store.set_data_dir(keep_data)
        if keep_ai is None:
            os.environ.pop("AI_ENABLED", None)
        else:
            os.environ["AI_ENABLED"] = keep_ai
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 10. Журнал, который не записался, не должен молчать
# ---------------------------------------------------------------------------

def test_journal_that_cannot_be_written_says_so():
    """Незаписанный журнал счётчиков — предупреждение в ответ, а не тихая дыра.

    Из журнала завтра берётся начало смены, если мастер не прислал утреннее
    показание. Не записать его молча — значит через день считать выпуск с
    чужого числа; тот же механизм, что и у №151 за 26.09.
    """
    tmp = tempfile.mkdtemp()
    keep = shift_store.get_data_dir()
    try:
        # Каталог данных заводим внутри ФАЙЛА: путь законный, а makedirs
        # падает — ровно то, что делает запись журнала невозможной.
        blocker = os.path.join(tmp, "not-a-dir")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("файл вместо каталога")
        shift_store.set_data_dir(os.path.join(blocker, "shifts"))
        res = day_pipeline.DayResult(date=DATE)
        day_pipeline._save_counter_ledger(
            DATE, {"machines": [{"machine": "98",
                                 "lines": [{"counter_end": 21563900}]}]},
            result=res)
        assert any("журнал счётчиков" in w for w in res.ai_warnings), \
            res.ai_warnings
        print("✅ журнал, который не записался, назван в предупреждениях")
    finally:
        shift_store.set_data_dir(keep)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n--- {name}")
            fn()
    print("\nвсе проверки прошли")
