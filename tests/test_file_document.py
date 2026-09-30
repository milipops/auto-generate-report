"""Приём файла с перепиской смены (services/file_text.py + shift_store.add_document).

Зачем файл вообще нужен. Telegram режет длинный текст на части по 4096 знаков,
и разрез попадает ПОСЕРЕДИНЕ обсуждения станка (реальный прогон 10.09.2026:
«–251» оказался в первом сообщении, а «Конец смены … Общий сч.: 278755» — во
втором). Хвост без заголовка терялся, показания уезжали на чужой станок, а
строки слипались в «ср сч 57688:18 обрыв устранен».

Файл приходит ЦЕЛИКОМ: не режется, сохраняет абзацы, а разделы «–151», «–251»,
«–98» читаются как заголовки обсуждений — станок каждого куска известен точно,
модель его не угадывает.

Запуск: python tests/test_file_document.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import file_text as ft                        # noqa: E402
from services import shift_store as ss                       # noqa: E402
from services import remains_report as rr                    # noqa: E402

# Переписка смены ровно такой, как её отдаёт выгрузка чата в Word.
CHAT = """–151
10 сентября 2026 г.
Инна Гудкова, 06:50:7:00 начало смены. Общ.сч. -750733
Ср.сч. - 5042_ из14500__
Срез - _1__ из _1___
7:57 стоп машина, обрыв нити
Сч 751459, ср сч 5768
8:18 обрыв устранен силами оператора, машина в работе
8:23 стоп машина, несоответствие в полотне
19:00 Конец смены:
Общ.сч. -_754129_
Ср.сч. - _8438_ из_14500_
Срез - _1_ из _1_
Подмена - _2_ из _4_
Шпуля: навито - _169_ из _303_
Состояние машины и полотна: Машина в рабочем состоянии.

–321
10 сентября 2026 г.
Олег, 06:55:Общ.сч. -118526 Ср.сч. - 750 из 750 Срез - 3 из 3
Подмена - 2 из 6 Шпуля: навито - 90 из 180 Развито - 105 из 205
Машина в рабочем состоянии, поставлена на подмену.
Навиваю шпули на подмену.

–98
10 сентября 2026 г.
Белякова С., 07:31:
7.00- 8.30 ремонт
Сч 21378750
Общ.сч 21387950
Ср.сч 250узл. из 1400
4 ср. из.4 ср.
Навито 250шп. из 250шп.
Замена шпулярника в течении всей смены.
19.00 конец смены.
"""


def _tmp_store():
    """Накопитель во временный каталог: тесты не трогают data/shifts."""
    path = tempfile.mkdtemp()
    ss.set_data_dir(path)
    return path


def test_txt_is_read():
    """Простой текстовый файл читается как есть."""
    text, kind = ft.extract_text("смена.txt", CHAT.encode("utf-8"))
    assert kind == "текстовый файл"
    assert "–151" in text and "–98" in text
    print("✅ .txt читается")


def test_cp1251_is_read():
    """Выгрузка в cp1251 (Windows) читается, а не превращается в кашу."""
    text, _kind = ft.extract_text("смена.txt", CHAT.encode("cp1251"))
    assert "начало смены" in text, text[:120]
    assert "Общ.сч." in text
    print("✅ cp1251 определён правильно")


def test_json_export_is_readable():
    """Выгрузка 1С (JSON) превращается в читаемый текст."""
    payload = ('{"выгрузка": "Отчёт производства за смену", "дата": "10.09.2026",'
               ' "станки": [{"станок": "151", "оператор": "Гудкова"}]}')
    text, kind = ft.extract_text("1С выгрузка 10.09.2026.json",
                                 payload.encode("utf-8"))
    assert kind == "JSON"
    assert "10.09.2026" in text and "Гудкова" in text
    print("✅ JSON выгрузки читается")


def test_html_is_readable():
    """HTML: разметка убирается, переносы блоков сохраняются."""
    html = ("<html><body><p>–151</p><p>7:57 стоп машина</p>"
            "<script>var x=1;</script><br>8:18 в работе</body></html>")
    text, kind = ft.extract_text("chat.html", html.encode("utf-8"))
    assert kind == "HTML-страница"
    assert "var x=1" not in text
    assert "стоп машина" in text and "в работе" in text
    print("✅ HTML читается без разметки")


def test_legacy_doc_is_explained():
    """Старый .doc не читаем, но объясняем, что сделать."""
    try:
        ft.extract_text("смена.doc", b"\xd0\xcf\x11\xe0binary")
    except ft.UnsupportedFileError as e:
        assert ".docx" in str(e), e
        print("✅ .doc объясняется словами")
        return
    raise AssertionError(".doc не должен читаться")


def test_oversize_file_is_refused():
    """Файл больше 8 МБ не принимаем (видео/архив не в тот чат)."""
    blob = b"x" * (ft.MAX_FILE_BYTES + 1)
    try:
        ft.extract_text("big.txt", blob)
    except ft.FileTooLargeError as e:
        assert "МБ" in str(e), e
        print("✅ огромный файл отклонён с понятным текстом")
        return
    raise AssertionError("огромный файл не должен приниматься")


def test_empty_file_is_refused():
    """Пустой файл — понятная ошибка, а не пустая смена."""
    try:
        ft.extract_text("пусто.txt", b"")
    except ft.EmptyFileError:
        print("✅ пустой файл отклонён")
        return
    raise AssertionError("пустой файл не должен приниматься")


def test_unknown_binary_is_refused():
    """Бинарь без расширения не принимаем (и не молчим)."""
    try:
        ft.extract_text("photo", bytes(range(256)) * 40)
    except ft.UnsupportedFileError as e:
        assert ".txt" in str(e) or ".docx" in str(e), e
        print("✅ бинарь отклонён с подсказкой")
        return
    raise AssertionError("бинарь не должен приниматься")


def test_sections_are_split_by_machine():
    """Разделы «–151», «–321», «–98» раскладываются по станкам."""
    secs = ss.split_sections(CHAT)
    got = [s.machine for s in secs]
    assert got == ["151", "321", "98"], got
    by = {s.machine: s for s in secs}
    assert by["151"].date == "10.09.2026", by["151"].date
    def _has(authors, last):
        return any(last in a for a in authors)
    assert _has(by["151"].authors, "Гудкова"), by["151"].authors
    assert _has(by["321"].authors, "Олег"), by["321"].authors
    assert _has(by["98"].authors, "Белякова"), by["98"].authors
    # Показания своего станка остались в своей секции.
    assert "754129" in by["151"].text and "754129" not in by["98"].text
    assert "21387950" in by["98"].text and "21387950" not in by["151"].text
    print("✅ секции разложены по станкам, данные не перемешаны")


def test_counter_value_is_not_a_section_header():
    """«Общ.сч. - 100» — значение, а не станок №100."""
    secs = ss.split_sections("Конец смены:\nОбщ.сч. - 100\nСр.сч. - 251 из 900")
    assert [s.machine for s in secs] == [None], [s.machine for s in secs]
    print("✅ значение счётчика разделом не считается")


def test_text_without_sections_stays_one_block():
    """Обычная переписка без «–N» сохраняется одним блоком (machine=None)."""
    secs = ss.split_sections("321 машина, Олег: 7:00 пуск. Общ.сч. -118526")
    assert len(secs) == 1 and secs[0].machine is None, secs
    print("✅ текст без разделов не ломается")


def test_add_document_stores_each_machine():
    """add_document: станок каждой секции сохранён ТОЧНО (machine=…)."""
    _tmp_store()
    acc = ss.add_document("10,09,2026.txt", CHAT.encode("utf-8"),
                          author="Руслан")
    assert acc.date == "10.09.2026", acc.date
    assert acc.file_machines == ["151", "321", "98"], acc.file_machines
    assert acc.file_sections == 3, acc.file_sections
    day = ss.load_day(acc.date)
    assert [m["machine"] for m in day["messages"]] == ["151", "321", "98"]
    assert ss.day_machines(acc.date) == ["151", "321", "98"]
    # Станок из заголовка попадает и в текст для ИИ — модель его не угадывает.
    prompt = ss.day_text(acc.date)
    assert "обсуждение по станку №151" in prompt, prompt[:300]
    print("✅ файл сохранён по станкам, станок известен точно")


def test_document_remains_are_attributed_correctly():
    """Остатки сырья по файлу: каждый станок со своим счётчиком."""
    _tmp_store()
    acc = ss.add_document("10,09,2026.txt", CHAT.encode("utf-8"))
    day = rr.build_day_remains(acc.date,
                               messages=ss.load_day(acc.date)["messages"])
    b151, b321, b98 = (day.by_machine("151"), day.by_machine("321"),
                       day.by_machine("98"))
    assert b151 and b321 and b98, [b.machine for b in day.machines]
    assert b151.counter("общий_счётчик") == 754129.0, b151.counters
    assert b321.counter("общий_счётчик") == 118526.0, b321.counters
    assert b98.counter("общий_счётчик") == 21387950.0, b98.counters
    # Главное: счётчик 98-й НЕ уехал на 151-ю (баг прогона 10.09.2026).
    assert b151.counter("общий_счётчик") != 21387950.0
    assert day.warnings == [], day.warnings
    print("✅ остатки по файлу: показания на своих станках")


def test_document_and_messages_coexist():
    """Файл + обычные сообщения за ту же смену не конфликтуют."""
    _tmp_store()
    acc = ss.add_document("10,09,2026.txt", CHAT.encode("utf-8"))
    extra = ss.add_message(text="98 машина: 18:55 в машине посторонний шум",
                           date=acc.date)
    assert extra.messages == acc.file_sections + 1, extra.messages
    day = ss.load_day(acc.date)
    assert day["messages"][-1]["machine"] is None
    assert ss.day_machines(acc.date) == ["151", "321", "98"]
    print("✅ файл и сообщения за одну смену сосуществуют")


def test_same_day_second_document_appends():
    """Второй файл за ту же дату добавляется, а не затирает первый."""
    _tmp_store()
    first = ss.add_document("утро.txt", CHAT.encode("utf-8"))
    second = ss.add_document("вечер.txt",
                             "–111\n10 сентября 2026 г.\nГудкова, 18:00: "
                             "Общ.сч. -206761\nСрез - 1 из 1".encode("utf-8"))
    assert second.date == first.date == "10.09.2026"
    assert second.messages == first.messages + 1, second.messages
    assert set(ss.day_machines(first.date)) == {"151", "321", "98", "111"}
    print("✅ два файла за одну смену не затирают друг друга")


def test_caption_is_stored_before_sections():
    """Подпись к файлу — ПЕРВОЕ сообщение, иначе она «прилипнет» к последнему станку.

    У сообщения без заголовка станок наследуется от предыдущего раздела
    (build_day_remains, carried), поэтому подпись, сохранённая в конец,
    отнеслась бы к №98 и могла увести её цифры.
    """
    _tmp_store()
    acc = ss.add_document("10,09,2026.txt", CHAT.encode("utf-8"),
                          caption="Смена за 10.09, задание №315 верное")
    day = ss.load_day(acc.date)
    assert day["messages"][0]["text"].startswith("Смена за 10.09"), day["messages"][0]
    assert day["messages"][0]["machine"] is None
    assert [m["machine"] for m in day["messages"][1:]] == ["151", "321", "98"]
    # Разделов три, подпись в это число не входит.
    assert acc.file_sections == 3, acc.file_sections
    # Остатки по подписи не уехали на 98-ю.
    rem = rr.build_day_remains(acc.date, messages=day["messages"])
    b98 = rem.by_machine("98")
    assert b98 is not None and b98.counter("общий_счётчик") == 21387950.0
    assert rem.warnings == [], rem.warnings
    print("✅ подпись к файлу сохранена первой и ничего не утянула")


if __name__ == "__main__":
    for _name, fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(fn):
            fn()
    print("\nПриём файлов проверен.")
