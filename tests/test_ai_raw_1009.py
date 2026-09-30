"""Регрессии по сырому разбору ИИ за 10.09.2026 (data/shifts/10.09.2026/ai_raw.json).

Разбор показал, что модель ВЫПОЛНИЛА правила промпта, а расчётный код их
выбросил. Три дефекта, все видны только на реальном ai_raw.json:

A. «Обед» в СЕРЕДИНЕ длинной причины. Модель собрала останов 10:03–12:45
   одной записью («стоп машина, чинка полотна …; обед; в 12:45 машина в
   работе»). Классификатор по слову «обед» назвал всё это «перерыв»: 2,7 ч
   ЧИНКИ оператора ушли из его часов, а простой задвоился с обедом.

B. Стажёр потерялся. Модель вернула kind «работа» с performer «Демидов» и
   интервалом 07:00–16:00 (мастер написал «работа стажёра окончена (8,5
   часов)»). Ветка «работа» просто делала continue — часы человека исчезали,
   блок «Работа привлекаемого персонала всего» оставался 0.

C. Обед внутри интервалов оператора. Интервалы Олега на ст.321 закрыли
   07:00–19:00 целиком, и оператор получил 12,0 ч с флагом «+0,75 ч сверх
   нормы» — ровно обед, которого он не отрабатывал. Симметрично: та же 45-
   минутка задваивалась и в строках простоя станка (12,75 ч при смене 12 ч).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_report import (                                   # noqa: E402
    build_shift_reports_from_structured, classify_stop)


def _one(machine):
    return build_shift_reports_from_structured({"machines": [machine]})[0]


# ─────────────────────── A. обед внутри рабочей записи ───────────────────────

def test_lunch_word_inside_a_work_reason_is_not_a_break():
    """«…чинка полотна …; обед; …машина в работе» — это чинка, а не перерыв."""
    long_reason = ("стоп машина, чинка полотна (одна стренга); сч 751999; "
                   "вкл машину для поднятия узлов для чинки; обед; сч 752202; "
                   "в 12:45 машина в работе")
    label, active = classify_stop(long_reason)
    assert label == "чинка/ремонт", label
    assert active is True, "оператор в это время чинил полотно"
    print("✅ «обед» в середине длинной причины чинку не перебивает")


def test_plain_lunch_record_still_a_break():
    """Обычные записи обеда читаются как раньше."""
    for text in ("обед", "Обед", "12:00-12:30 обед", "обед (время не указано)",
                 "перерыв"):
        label, active = classify_stop(text)
        assert label == "перерыв", (text, label)
        assert active is False, text
    print("✅ обычный обед остался перерывом")


def test_machine_151_downtime_is_not_doubled_by_lunch():
    """Ст.151 за 10.09: 2,7 ч чинки в работе оператора, простой без задвоения."""
    r = _one({
        "machine": "151", "operator": "Гудкова", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 750733, "counter_end": 754129}],
        "events": [
            {"start": "10:03", "end": "12:45", "kind": "чинка",
             "reason": "стоп машина, чинка полотна (одна стренга); обед; "
                       "в 12:45 машина в работе"},
            {"start": None, "end": None, "kind": "обед",
             "reason": "обед (время не указано)"}]})
    labels = {l: h for l, h, _a in r.downtime_by_reason()}
    # Окно чинки 10:03–12:45 = 2,7 ч, но внутри него обед 12:00–12:45 (0,75 ч),
    # а обед ВСЕГДА стоит отдельной строкой простоя. Поэтому в строке чинки
    # остаётся 1,95 ч, и 1,95 + 0,75 = 2,7 — ровно длина окна, без задвоения.
    # Раньше строки давали 2,7 + 0,75 = 3,45 ч простоя на окно 2,7 ч, а излишек
    # прятался в _lunch_trim (он снимал только то, что вылезало за 12 ч).
    # Правило пользователя: станок = 12 ч, из них обед 45 мин отдельной строкой.
    assert abs(labels.get("чинка/ремонт", 0) - 1.95) < 0.05, labels
    assert abs(labels.get("обед (станок стоит)", 0) - 0.75) < 0.01, labels
    assert "перерыв" not in labels, labels
    # Часы ОПЕРАТОРА — те же 1,95: 45 мин обеда он не отрабатывал.
    assert abs(r.operator_stop_hours - 1.95) < 0.05, r.operator_stop_hours
    assert r.machine_balance <= 12.3, r.machine_balance
    print("✅ ст.151: простой 1,95 + обед 0,75 = 2,7 ч (окно без задвоения)")


# ─────────────────────────── B. стажёр ───────────────────────────

def test_trainee_hours_are_attracted_personnel():
    """kind «работа» + performer = стажёр → часы в привлекаемый персонал."""
    r = _one({
        "machine": "251", "operator": "Арташина", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 274682, "counter_end": 278755}],
        "events": [
            {"start": "07:00", "end": "16:00", "kind": "работа",
             "performer": "Демидов",
             "reason": "работа стажёра: мониторинг навивки шпулей, стоп и пуск "
                       "машины; 16:00 работа стажёра окончена (8,5 часов)"}]})
    assert r.attracted_total > 8.0, r.attracted_total
    people = r.attracted_by_person()
    assert any("Демидов" in who for who, _h in people), people
    # Часы стажёра НЕ идут в часы оператора станка (8,5 ч Демидова остаются
    # только в привлекаемом персонале): у Арташиной — 9,17 ч, и это время
    # работающего станка по счётчику. До приказа владельца от 28.09.2026 здесь
    # стояло 11,25 — остаток до нормы закрывал филлер.
    assert abs(r.operator_hours - 9.17) < 0.05, r.operator_hours
    assert abs(r.operator_hours - r.work_hours) < 0.01, (
        r.operator_hours, r.work_hours)
    print(f"✅ стажёр учтён: {people[0][1]} ч в привлекаемом персонале")


def test_operator_own_work_kind_is_still_ignored():
    """kind «работа» без чужого performer по-прежнему не даёт часов станку."""
    r = _one({
        "machine": "251", "operator": "Арташина", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 274682, "counter_end": 278755}],
        "events": [
            {"start": "12:45", "end": "14:45", "kind": "работа",
             "reason": "мониторинг навивки шпулей (машина в работе)"}]})
    assert r.attracted_total == 0.0, r.attracted_total
    # Своя «работа» не задвоена: 2,0 ч названных + 7,17 ч станка без них =
    # 9,17 ч. Ровно 11,25 здесь было добивкой до нормы (приказ от 28.09.2026 —
    # так больше не делать).
    assert abs(r.operator_hours - 9.17) < 0.05, r.operator_hours
    assert abs(r.operator_hours - (r.operator_explicit_hours
                                   + r.operator_control_hours)) < 0.02, \
        r.operator_hours
    print("✅ своя «работа» станка часами не задваивается")


# ─────────────────────── C. обед внутри интервалов ───────────────────────

def test_lunch_is_not_counted_as_operator_work():
    """Интервалы закрыли 07:00–19:00, но 45 мин обеда из них вырезаны.

    07:00–12:40 (5,67) + 14:04–19:00 (4,93) = 10,60 ч, а пересечение с окном
    обеда 12:00–12:45 снимает 0,67 ч → 9,93. Проверяем именно ЯВНЫЕ часы
    оператора: при нулевом выпуске станка филлер «контроль за работой машины»
    не начисляется (правило zero_output_all_shift), поэтому итог operator_hours
    здесь равен explicit и прежняя проверка «= 11,25» больше не описывает
    реальность. Сверка с эталоном пользователя 10.09, ст.321: оператор 8 ч
    (6,62 навивка + 1,38 подмена) при работе машины 0 ч — без добивки до 11,25.
    """
    r = _one({
        "machine": "321", "operator": "Олег", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 118526, "counter_end": 118526}],
        "events": [
            {"start": "07:00", "end": "12:40", "kind": "навивка",
             "reason": "машина на подмене, навиваю шпули"},
            {"start": "14:04", "end": "19:00", "kind": "остановка",
             "reason": "готова к пропуску узлов, пуска не было"}]})
    assert r.zero_output_all_shift, "станок не дал выпуска — признак обязателен"
    assert abs(r.operator_explicit_hours - 9.93) < 0.02, r.operator_explicit_hours
    assert abs(r.operator_hours - 9.93) < 0.02, r.operator_hours
    assert r.operator_excess <= 0.01, r.operator_excess
    print("✅ обед вырезан из часов оператора (9,93 вместо 10,60)")


def test_downtime_rows_do_not_exceed_12_hours():
    """Строки простоя станка складываются не больше чем в 12 ч."""
    r = _one({
        "machine": "321", "operator": "Олег", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 118526, "counter_end": 118526}],
        "events": [
            {"start": "07:00", "end": "12:40", "kind": "навивка",
             "reason": "машина на подмене, навиваю шпули"},
            {"start": "14:04", "end": "19:00", "kind": "остановка",
             "reason": "готова к пропуску узлов, пуска не было"}]})
    rows = r.downtime_by_reason()
    total = sum(h for _l, h, _a in rows)
    assert total <= 12.01, (total, rows)
    assert r.machine_excess == 0.0, r.machine_excess
    # Итог совпадает со своим же набором строк (правило Р22).
    assert abs(r.accounted_downtime - total) < 0.05, (r.accounted_downtime, total)
    print(f"✅ строки простоя = {total} ч, не больше 12")


def test_naladka_all_shift_keeps_its_lunch_row():
    """Эталон 01.09 ст.121: наладка 10,49 + обед 0,75 = 12 ч, обед НЕ съедается."""
    r = _one({
        "machine": "121", "operator": "Вашурина",
        "lines": [{"counter_start": 887105, "counter_end": 887770}],
        "events": [{"kind": "наладка", "start": "07:00", "end": "19:00",
                    "reason": "станок в наладке", "performer": "Пронкин"}]})
    labels = {l: h for l, h, _a in r.downtime_by_reason()}
    assert abs(labels.get("наладка/переналадка", 0) - 10.49) < 0.02, labels
    assert abs(labels.get("обед (станок стоит)", 0) - 0.75) < 0.02, labels
    assert abs(r.machine_balance - 12.0) < 0.02, r.machine_balance
    print("✅ эталон 01.09 ст.121 не тронут")


def test_named_hours_win_over_the_interval():
    """Мастер назвал часы словами — они главнее интервала (стажёр: 8,5, не 9).

    Прогон 10.09.2026, ст.251: «С 7:00-16:00 он освоил мониторинг навивки
    шпулей» + «16:00 работа стажёра окончена ( 8,5 часов )». Интервал даёт 9 ч,
    а мастер написал 8,5 — он сам вычел перерыв. Код обязан взять hours, если
    модель его отдала, и не пересчитывать интервал.
    """
    r = _one({
        "machine": "251", "operator": "Арташина", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 274682, "counter_end": 278755}],
        "events": [{"start": "07:00", "end": "16:00", "kind": "работа",
                    "performer": "Демидов", "hours": 8.5,
                    "reason": "работа стажёра окончена (8,5 часов)"}]})
    assert abs(r.attracted_total - 8.5) < 0.01, r.attracted_total
    people = r.attracted_by_person()
    assert any("Демидов" in who and abs(h - 8.5) < 0.01 for who, h in people), people
    print(f"✅ названные мастером часы важнее интервала: {people[0][1]} ч")


def test_trainee_without_named_hours_gets_the_interval():
    """Если часы словами не названы — считается интервал (07:00–16:00 = 9 ч)."""
    r = _one({
        "machine": "251", "operator": "Арташина", "operator_shift_end": "19:00",
        "lines": [{"counter_start": 274682, "counter_end": 278755}],
        "events": [{"start": "07:00", "end": "16:00", "kind": "работа",
                    "performer": "Демидов", "reason": "мониторинг навивки"}]})
    assert abs(r.attracted_total - 9.0) < 0.01, r.attracted_total
    print("✅ без названных часов берётся интервал (9 ч)")


def test_service_worker_lunch_is_not_deducted():
    """Эталон 01.09: наладчику за 08:00–14:00 стоит 6,0 ч, обед не вычитается."""
    r = _one({
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 733963, "counter_end": 736700}],
        "events": [
            {"kind": "наладка", "start": "08:00", "end": "14:00",
             "reason": "машина в службе наладки, обрыв, занимается Пронкин",
             "performer": "Пронкин"}]})
    people = dict(r.attracted_by_person())
    hours = [h for who, h in people.items() if who.startswith("Пронкин")]
    assert hours and abs(hours[0] - 6.0) < 0.02, people
    print("✅ наладчику обед не вычитается (эталон 01.09 сохранён)")


if __name__ == "__main__":
    for _name, fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(fn):
            fn()
    print("\nРегрессии разбора 10.09 проверены.")
