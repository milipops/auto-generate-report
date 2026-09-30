"""Дата смены: все шапки разделов важнее имени файла.

Что случилось 28.09.2026. Мастер прислала выгрузку, названную
«26.09.2026.csv», а внутри неё десять тем озаглавлены «… - информация за смену
27.09.2026г.». Имя файла было главным источником даты, и смена 27.09 легла в
26.09: поверх уже принятой там смены 26.09 (её пришлось возвращать из
резервной копии выгрузки), а 27.09 осталась пустой.

Правило теперь такое:

1. имя файла — по-прежнему главный источник, пока шапки с ним не спорят
   (прогон 10.09.2026: перевёрнутый PDF вообще не даёт даты из текста, и только
   имя файла тогда и спас);
2. НО если ВСЕ названные станками шапки (не меньше двух) говорят одну и ту же
   ДРУГУЮ дату — это хор, а не оговорка: тему открывает тот, кто ведёт смену, и
   дату в имени просто копируют с прошлого файла. Берём дату из шапок и
   обязательно предупреждаем, что именно так сделали;
3. шапки спорят между собой (станки назвали разные даты) — дата из имени
   файла, перебивать её нечем;
4. одну дату назвала ОДНА шапка — это не хор, имя файла остаётся;
5. дату выбрал пользователь в интерфейсе («задана явно») — она главнее всего,
   но спор показываем предупреждением, а не молча кладём данные не туда.

Запуск: python tests/test_shift_date_priority.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import shift_store as ss                       # noqa: E402

HEAD = "Дата и Время сообщения;Тип;Имя автора;Текст сообщения\n"


def _rows(date, *machines):
    """Темы станков с датой «за смену <date>» и одним ответом в каждой."""
    out = []
    for i, m in enumerate(machines, start=1):
        out.append(f"2026-09-{20 + i:02d} 06:3{i}:00;Сообщение;;{m} машина - "
                   f"информация за смену {date}г. 06:3{i}")
        out.append(f"2026-09-{20 + i:02d} 07:0{i}:00;Ответ;Мастер;07:0{i} "
                   f"машина в работе Общ.сч. {100000 + i} 07:0{i}")
    return HEAD + "\n".join(out) + "\n"


def _tmp_store():
    ss.set_data_dir(tempfile.mkdtemp())


def test_agreeing_headers_beat_the_file_name():
    """Реальный прогон 28.09.2026: имя «26.09» против десяти шапок «27.09»."""
    _tmp_store()
    acc = ss.add_document("26.09.2026.csv",
                          _rows("27.09.2026", "321", "111", "251").encode(),
                          author="Мастер")
    assert acc.date == "27.09.2026", acc.date
    assert acc.date_source == "шапки разделов", acc.date_source
    # Смена 26.09 осталась чистой: туда не лег ни один раздел.
    assert ss.load_day("26.09.2026")["messages"] == []
    assert len(ss.load_day("27.09.2026")["messages"]) == 3
    print("✅ хор шапок перетянул дату на свой день")


def test_conflict_is_said_out_loud():
    """Перебитое имя файла не проходит молча — в ответе видно обе даты."""
    _tmp_store()
    acc = ss.add_document("26.09.2026.csv",
                          _rows("27.09.2026", "321", "111").encode())
    warn = acc.quality_warning
    assert "26.09.2026" in warn and "27.09.2026" in warn, warn
    assert "26.09.2026.csv" in warn, warn          # назван сам файл
    assert "№321" in warn and "№111" in warn, warn  # и станки, которые это сказали
    print("✅ спор имени и шапок показан пользователю")


def test_same_date_in_name_and_headers_stays_quiet():
    """Имя файла и шапки согласны — ничего не перебиваем и не предупреждаем."""
    _tmp_store()
    acc = ss.add_document("24.09.2026.csv",
                          _rows("24.09.2026", "321", "111").encode())
    assert acc.date == "24.09.2026", acc.date
    assert acc.date_source == "имя файла", acc.date_source
    assert acc.quality_warning == "", acc.quality_warning
    print("✅ согласие имени и шапок: дата из имени, без предупреждения")


def test_disagreeing_headers_keep_the_file_name():
    """Станки назвали РАЗНЫЕ даты — хора нет, имя файла остаётся главным."""
    _tmp_store()
    blob = (HEAD +
            "2026-09-26 06:30:00;Сообщение;;321 машина - информация за смену "
            "26.09.2026г. 06:30\n"
            "2026-09-26 06:31:00;Сообщение;;111 машина - информация за смену "
            "27.09.2026г. 06:31\n"
            "2026-09-26 06:32:00;Сообщение;;251 машина - информация за смену "
            "25.09.2026г. 06:32\n").encode()
    acc = ss.add_document("26.09.2026.csv", blob)
    assert acc.date == "26.09.2026", acc.date
    assert acc.date_source == "имя файла", acc.date_source
    assert acc.quality_warning == "", acc.quality_warning
    print("✅ спор шапок между собой дату не перебивает")


def test_single_header_is_not_a_chorus():
    """Одна шапка с другой датой — это оговорка, а не хор: имя файла важнее."""
    _tmp_store()
    blob = (HEAD +
            "2026-09-26 06:30:00;Сообщение;;321 машина - информация за смену "
            "27.09.2026г. 06:30\n"
            "2026-09-26 06:31:00;Сообщение;;111 машина 06:31\n"
            "2026-09-26 06:32:00;Ответ;Мастер;07:00 машина в работе 06:32\n"
            ).encode()
    acc = ss.add_document("26.09.2026.csv", blob)
    assert acc.date == "26.09.2026", acc.date
    assert acc.date_source == "имя файла", acc.date_source
    print("✅ одиночная шапка дату не перебивает")


def test_explicit_date_wins_but_shows_the_fight():
    """Дата, выбранная в интерфейсе, главнее шапок — но спор виден."""
    _tmp_store()
    acc = ss.add_document("26.09.2026.csv",
                          _rows("27.09.2026", "321", "111").encode(),
                          date="26.09.2026")
    assert acc.date == "26.09.2026", acc.date
    assert acc.date_source == "задана явно", acc.date_source
    assert "27.09.2026" in acc.quality_warning, acc.quality_warning
    assert len(ss.load_day("26.09.2026")["messages"]) == 2
    print("✅ явная дата не перебита, но предупреждение отдано")


def test_no_contender_means_no_noise():
    """В имени и в выборе даты нет даты — шапки просто дают её, без спора.

    Предупреждение про «перебитое имя» законно только когда спорить есть с
    чем: пустую дату перебивать нечем, а ложное предупреждение мастер читает
    как поломку приёма.
    """
    _tmp_store()
    acc = ss.add_document("переписка.csv",
                          _rows("27.09.2026", "321", "111").encode())
    assert acc.date == "27.09.2026", acc.date
    assert acc.date_source == "текст файла", acc.date_source
    assert acc.quality_warning == "", acc.quality_warning
    print("✅ без спорщика дата молча берётся из шапок")


def test_foreign_dates_in_the_text_do_not_join_the_chorus():
    """Срок стопа «до 15.10.2026» и совещание «08.09.2026» — не хор."""
    _tmp_store()
    blob = (HEAD +
            "2026-09-26 06:30:00;Сообщение;;14т машина(в стопе до 15.10.2026г. "
            "инж. служба) - информация за смену 26.09.2026г. 06:30\n"
            "2026-09-26 06:31:00;Сообщение;;321 машина - информация за смену "
            "26.09.2026г. 06:31\n"
            "2026-09-26 06:32:00;Ответ;Мастер;После совещания 08.09.2026г. "
            "приняты решения 06:32\n").encode()
    acc = ss.add_document("26.09.2026.csv", blob)
    assert acc.date == "26.09.2026", acc.date
    assert acc.date_source == "имя файла", acc.date_source
    assert acc.quality_warning == "", acc.quality_warning
    print("✅ посторонние даты дату смены не перебивают")
