"""Резервная сводка без ИИ: что она показывает, когда модель не ответила.

Путь `day_pipeline._fallback_reports` включается, когда ИИ не дал отчёта
(нет ключа, таймаут, 403). Раньше он считал одним куском по всему тексту
смены, и на выгрузке мессенджера (строки «Автор, время: …») выходил один
блок «станок не определён» с первым попавшимся числом в роли общего
счётчика. Плюс сокращённую метку бланка «Общ.сч.» регулярка не читала
совсем. Формулировки — по реальному бланку ст.111 за 24.09.2026.

Запуск: python tests/test_fallback_summary.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import day_pipeline                             # noqa: E402
from services.daily_report import build_daily_reports         # noqa: E402

# Вечерний бланк 111-й за 24.09.2026 — дословно из выгрузки, вместе с
# разметкой жирным и подчёркиваниями пустых клеток.
BOLD_111 = (
    "**Конец(начало) смены**: **Общ.сч**. -223351______ **Ср.сч**. - _3001____ "
    "из2700____ **Срез** - __2_ из 2 **Подмена** - 4 из 15____ **Шпуля: навито** "
    "- _0____ из _759___ **Развито** - _0____ из __750____ **Состояние машины** "
    "**и полотна**: машина после подмены шпулей 750 из 750."
)

PLAIN_111 = (
    "Конец(начало) смены: Общ.сч. -223351______ Ср.сч. - _3001____ из 2700____ "
    "Срез - __2_ из 2 Подмена - 4 из 15____ Шпуля: навито - _0____ из _759___"
)


def _one(text):
    reports = build_daily_reports(text)
    assert len(reports) == 1, reports
    return reports[0]


def test_dotted_general_counter_is_read():
    """«Общ.сч.» — та же клетка, что «Общий счётчик»: число обязано читаться."""
    assert _one(PLAIN_111).general_counter == 223351, _one(PLAIN_111).general_counter
    print("✅ сокращённая метка общего счётчика читается")


def test_bold_markup_from_messenger_export_is_read():
    """Жирный бланк из выгрузки не должен терять показания (см. #71)."""
    r = _one(BOLD_111)
    assert r.general_counter == 223351, r.general_counter
    assert r.fractions.get("срез") == (2, 2), r.fractions
    assert r.fractions.get("подмена") == (4, 15), r.fractions
    assert r.fractions.get("навито") == (0, 759), r.fractions
    print("✅ разметка мессенджера внутри бланка не съедает резервную сводку")


def test_blank_underscores_do_not_hide_fractions():
    """Подчёркивания — пустые клетки бланка, а не часть числа."""
    r = _one(PLAIN_111)
    assert r.fractions.get("срез") == (2, 2), r.fractions
    assert r.fractions.get("подмена") == (4, 15), r.fractions
    print("✅ клетки, заполненные подчёркиваниями, читаются дробями")


def test_evening_reading_wins_over_morning_one():
    """Два бланка в смене — итог вечера, а не приёмка (как в листе «Остатки»)."""
    text = ("111 машина, Вашурина: Начало смены: Общ.сч. -218263______ Срез - _614__ "
            "из _2700___\nКонец(начало) смены: Общ.сч. -223351______ Срез - __2_ из 2")
    r = _one(text)
    assert r.general_counter == 223351, r.general_counter
    assert r.fractions.get("срез") == (2, 2), r.fractions
    print("✅ вечернее показание не перебивается утренним")


def test_general_label_with_a_plan_is_not_the_odometer():
    """Дробь под меткой общего счётчика — не одометр (то же, что в «Остатках»).

    21.09.2026, ст.98: «Общий сч.21486750 Со.сч.1000 из 1450» — если у числа
    стоит план «из», это соседний счётчик; общий одометр мастер пишет одним
    числом. Часы от 1000 вышли бы отрицательными.
    """
    r = _one("98 машина: 19:00 Конец смены: Об. Сч.: 1000 из 1450 "
             "Срезовой счётчик - 200 из 1450 Срез - 3 из 4")
    assert r.general_counter is None, r.general_counter
    assert r.slice_counter == (200, 1450), r.slice_counter
    print("✅ дробь под меткой общего счётчика не становится показанием")


def test_machine_without_counters_says_so_instead_of_inventing():
    """Нет показаний — в предупреждении, а не в цифре, взятой из чужого места."""
    r = _one("111 машина, Вашурина: 7:00 пуск, 8:00 стоп, чинка")
    assert r.general_counter is None, r.general_counter
    assert any("Счётчики" in w for w in r.warnings), r.warnings
    print("✅ без показаний станок честно предупреждён")


def test_fallback_splits_by_machine_and_never_shows_a_foreign_counter():
    """Сводка по станкам, а не одним куском (реальный накопитель за 24.09).

    Прежний способ на этом тексте выдавал один блок «станок не определён»,
    где общим счётчиком оказывалось первое попавшееся число — 3083.
    """
    date = "24.09.2026"
    text = day_pipeline.shift_store.day_text(date)
    machines = day_pipeline.shift_store.day_machines(date)
    assert machines, "проверка бессмысленна без накопителя"

    reports = day_pipeline._fallback_reports(text, machines, date)
    assert reports, "резервная сводка не должна быть пустой"
    assert all(r.machine for r in reports), [r.machine for r in reports]

    by_machine = {r.machine: r for r in reports}
    assert "111" in by_machine, sorted(by_machine)
    assert by_machine["111"].general_counter == 223351, by_machine["111"].general_counter
    # 3083 — показание 601-й, а не «станка не определён»: прежний способ
    # лепил его к первому блоку дня, и в сводке число гуляло не на свой станок.
    assert by_machine["601"].general_counter == 3083, by_machine["601"].general_counter
    print(f"✅ резервная сводка разложена по станкам: {sorted(by_machine)}")


def test_fallback_falls_back_to_single_block_when_machines_are_unknown():
    """Файл без тем и без заголовков: сводка остаётся, а не исчезает."""
    text = "Юлия Черныш, 06:37: 7:00 пуск машины; 8:00 стоп, чинка"
    reports = day_pipeline._fallback_reports(text, [], "24.09.2026")
    assert len(reports) == 1, reports
    assert reports[0].machine is None, reports[0].machine
    print("✅ неопознанный текст не теряет сводку совсем")


def test_bobbin_quantity_is_not_read_as_a_machine():
    """«-12 шт. годные в работу» — это шпули, а не станок №12.

    Прежний разбор на теме 251-й за 15.09.2026 резал её на три блока, и два
    последних шли на вымышленный станок «12»: у настоящей 251-й оставался
    утренний 288402, а вечерний 291916 уезжал на машину, которой нет в парке.
    """
    text = ("–251 машина\n15 сентября 2026 г.\n\nЮлия Черныш, 06:51: "
            "Об. Сч.: 288402\nСр. Сч: 4274 из 14800\n\n"
            "-12 шт. годные в работу, навитые во время наладки\n"
            "16:30 Об. Сч.: 291916\nСр. Сч.: 7788 из 14800")
    reports = build_daily_reports(text, machine_hint="251")
    assert [r.machine for r in reports] == ["251"], reports
    assert reports[0].general_counter == 291916, reports[0].general_counter
    print("✅ количество шпулей не стало станком №12")


def test_evening_reading_with_blank_underscores_wins():
    """Клетка «Общ.сч. -_217622_» — то же показание, что «Общ.сч. -217622»."""
    text = ("111 машина, Гудкова: 07:00 Общ сч 214721, ср сч 2501 из 2700\n"
            "15:20 Общ сч 216460, ср сч 1539 из 2700\n"
            "Конец смены: Общ.сч. -_217622_ Ср.сч. - _2700_ из _2700_")
    assert _one(text).general_counter == 217622, _one(text).general_counter
    print("✅ вечерняя клетка с подчёркиваниями не потеряна")


def test_slice_counter_reads_underscored_blanks():
    """«Срез. Сч - _4__ из_4__» — та же дробь, что «4 из 4»."""
    r = _one("111 машина: Конец смены: Общ.сч. -_217622_ Срез. Сч - _4__ из_4__")
    assert r.slice_counter == (4, 4), r.slice_counter
    print("✅ счётчик срезов с подчёркиваниями читается")


def test_partial_chat_is_warned_about_not_silently_totalled():
    """Смены, легшие кусками без заголовка станка, помечены вопросом.

    На 31.08.2026 переписка 251-й оборвана: продолжение дня пришло отдельным
    куском без «–251», и резервная сводка видела только 259877 вместо вечерних
    261740. Число это не выдуманное, но и не итог смены — значит, надо сказать.
    """
    for date, expected in (("31.08.2026", True), ("24.09.2026", False)):
        reports = day_pipeline._fallback_reports(
            day_pipeline.shift_store.day_text(date),
            day_pipeline.shift_store.day_machines(date), date)
        flagged = any("кусками" in w for r in reports for w in r.warnings)
        assert flagged is expected, (date, flagged)
    print("✅ неполная переписка помечена, полная — нет")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
