"""Регрессии прогона 10.09.2026: расклейка текста, обед, часы станка 321.

1. Выгрузка чата теряет переводы строк, и сообщения мастеров слипаются:
   «ср сч 57688:18 обрыв устранен … машина в работе8:23 стоп машина».
   По такому тексту модель теряла почти все остановки (ст.151: два останова
   из восьми, 1,98 ч «время не указано»). Расклеиваем КОДОМ, детерминированно.

2. Обед без интервала (kind «обед», start/end null) попадал в untimed_stops,
   и в отчёте появлялось «время не указано у причин: …, обед» рядом с уже
   посчитанными 0,75 ч обеда — одно и то же время дважды.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shift_store import split_glued_text                    # noqa: E402
from services.ai_extract import detect_machines                    # noqa: E402
from services.shift_report import (                                  # noqa: E402
    build_shift_reports_from_structured)


def test_glued_time_after_letters_is_split():
    """«машина в работе8:23 стоп машина» — это «в работе» + стоп в 8:23."""
    out = split_glued_text("обрыв устранен, машина в работе8:23 стоп машина")
    assert "работе\n8:23 стоп" in out, out
    print("✅ буква перед временем разделена")


def test_glued_counter_and_time_are_split():
    """«ср сч 57688:18» — это «ср сч 5768» и время «8:18»."""
    out = split_glued_text("Сч 751459, ср сч 57688:18 обрыв устранен")
    assert "ср сч 5768\n8:18 обрыв" in out, out
    # то же для полудня: «651112:45» → «6511» + «12:45»
    out2 = split_glued_text("ОбедСч 752202, ср сч 651112:45 машина в работе")
    assert "ср сч 6511\n12:45 машина" in out2, out2
    print("✅ счётчик не съедает время")


def test_glued_uppercase_after_lowercase_is_split():
    """«полотнаСч 753952» и «6022Причина обрыва» — две записи."""
    assert split_glued_text("чинка полотнаСч 753952").endswith("\nСч 753952")
    out = split_glued_text("ср сч 6022Причина обрываВызвана служба наладки")
    assert out == "ср сч 6022\nПричина обрыва\nВызвана служба наладки", out
    print("✅ границы предложений восстановлены")


def test_split_is_idempotent_and_safe():
    """Повторный прогон ничего не меняет, данные не портятся."""
    text = "Сч 753952 ср сч 826118:41 остановка чинки полотна19:00 Конец смены:"
    once = split_glued_text(text)
    assert split_glued_text(once) == once, "расклейка не идемпотентна"
    # Ассортименты и номера станков не ломаются.
    for safe in ("187.3 * 32мм 250.0 зел. Микаилов з. 315",
                 "187.3-28 250.5 зел", "121,251", "√251", "на 321 машине",
                 "Сотволдиев98шт", "подмена на 90 шп.", "Общ.сч. -745636",
                 "С 7:00-16:00 он освоил мониторинг навивки шпулей"):
        assert split_glued_text(safe) == safe, safe
    print("✅ идемпотентно, ассортимент и номера целы")


def test_split_does_not_lose_machines():
    """Станки после расклейки находятся те же (или больше)."""
    text = ("–321\n10 сентября 2026 г.Олег, 06:55:Общ.сч. -118526 Ср.сч. - 750 "
            "из 750 Срез - 3 из 3 Подмена - 2 из 6 Шпуля: навито - 90 из 180\n"
            "Юлия Черныш, 12:40:вставайте на подмену\n–98\nБелякова С., 07:31:")
    assert set(detect_machines(split_glued_text(text))) >= set(detect_machines(text))
    print("✅ станки не теряются")


def test_untimed_lunch_is_not_listed_as_unexplained():
    """Обед без времени не должен стоять в «время не указано у причин»."""
    r = build_shift_reports_from_structured({"machines": [{
        "machine": "151", "operator": "Гудкова",
        "lines": [{"counter_start": 750733, "counter_end": 754129}],
        "events": [
            {"kind": "обед", "reason": "Обед", "start": None, "end": None},
            {"kind": "остановка", "reason": "продолжаю навивку шпулей",
             "start": None, "end": None},
        ]}]})[0]
    assert r.lunch_recorded is True
    assert "Обед" not in r.untimed_stops, r.untimed_stops
    assert "обед" not in r.unexplained_label, r.unexplained_label
    # 0,75 ч обеда по-прежнему в простое станка.
    assert abs(r.lunch_in_downtime - 0.75) < 0.01, r.lunch_in_downtime
    print("✅ обед больше не дублируется в «время не указано»")


def test_zero_output_machine_stood_all_shift():
    """Счётчик не сдвинулся = станок стоял всю смену, а не «−9,85 ч расхождение».

    Прогон 10.09.2026, ст.321: «Общ.сч. -118526» утром и «Общ.сч. -118526» в
    конце, весь день шли подмена и навивка шпулей. Бот выдавал
    «!!! РАСХОЖДЕНИЕ: станок −9,85 ч до 12 ч» и 9,85 ч «время не указано».
    """
    r = build_shift_reports_from_structured({"machines": [{
        "machine": "321", "operator": "Олег",
        "lines": [{"counter_start": 118526, "counter_end": 118526}],
        "events": [
            {"kind": "остановка", "reason": "Навиваю шпули на подмену",
             "start": None, "end": None},
            {"kind": "подмена", "reason": "Сеньков подменил 86 шпулей",
             "performer": "Сеньков", "start": None, "end": None},
        ]}]})[0]
    assert r.zero_output_all_shift is True
    assert r.work_hours == 0.0
    assert r.machine_shortfall == 0.0, r.machine_shortfall
    assert r.unexplained_downtime == 0.0, r.unexplained_downtime
    assert abs(r.machine_balance - 12.0) < 0.01, r.machine_balance
    labels = [l for l, _h, _a in r.downtime_by_reason()]
    assert any("не работал всю смену" in l for l in labels), labels
    assert any("Навиваю шпули на подмену" in l for l in labels), labels
    print("✅ станок с нулевым выпуском: простой объяснён, расхождения нет")


def test_zero_output_without_reasons_stays_unexplained():
    """Нет названных причин — часы честно остаются «причина не указана»."""
    r = build_shift_reports_from_structured({"machines": [{
        "machine": "321", "operator": "Олег",
        "lines": [{"counter_start": 118526, "counter_end": 118526}],
        "events": []}]})[0]
    assert r.zero_output_all_shift is True
    assert r.zero_output_downtime_hours == 0.0
    assert r.unexplained_downtime > 0.0, "без причин дыра обязана остаться"
    print("✅ без названных причин ничего не выдумывается")


def test_moved_counter_is_not_zero_output():
    """Обычная смена (счётчик сдвинулся) новым правилом не трогается."""
    r = build_shift_reports_from_structured({"machines": [{
        "machine": "151", "operator": "Гудкова",
        "lines": [{"counter_start": 750733, "counter_end": 754129}],
        "events": [{"kind": "остановка", "reason": "Навиваю шпули",
                    "start": None, "end": None}]}]})[0]
    assert r.zero_output_all_shift is False
    assert r.zero_output_downtime_hours == 0.0
    print("✅ обычная смена правилом не затронута")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
