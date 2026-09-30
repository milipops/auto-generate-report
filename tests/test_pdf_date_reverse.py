"""Регрессии прогона 10.09.2026 из PDF: дата смены и перевёрнутый текст.

Что случилось. Пользователь прислал «10,09,2026.pdf» — тот же текст, что и
«10,09,2026.docx», но сохранённый печатью из мессенджера/браузера. Такой PDF
кладёт слова в порядке ОТРИСОВКИ, а не чтения, поэтому строки приходят задом
наперёд:

    «г.2026 сентября 10»   вместо  «10 сентября 2026 г.»
    «нитиобрывмашина,стоп шпулей»  вместо  «7:57 стоп машина, обрыв нити»

Два следствия, которые и чиним:

1. ДАТА. Регэксп _SECTION_DATE_RE без границы слева съел «26 сентября» из
   «20**26** сентября» — и смена уехала на 26.09.2026. Плюс: в перевёрнутом
   тексте нормальной даты вообще нет, а ИМЯ файла «10,09,2026.pdf» — самый
   надёжный источник, им и пользуемся в первую очередь.

2. КАЧЕСТВО. Порядок слов чиним (unreverse_lines), но потерянные ПРОБЕЛЫ
   («из14500__5042_-Ср.сч.») восстановить нельзя. Такой файл надо опознать и
   честно сказать пользователю прислать .docx/.txt — молча выдавать отчёт с
   выдуманными остатками сырья хуже.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import file_text as ft                        # noqa: E402
from services import shift_store as ss                       # noqa: E402

# Реальные строки из перевёрнутого PDF (прогон 10.09.2026).
REVERSED_HEAD = "\n".join([
    "–151",
    "г.2026 сентября 10",
    "-750733",
    "Общ.сч. смены.начало 06:50:7:00",
    "Гудкова, Инна",
    "из14500__5042_-Ср.сч.",
    "_1___из_1__-Срез",
    "_4_из_2_- Подмена",
    "303из_105-навито Шпуля:",
    "работев",
    "Машина",
    "нитиобрывмашина,стоп шпулей",
    "7:57 навивку Продолжаю",
])
FORWARD_HEAD = "\n".join([
    "–151",
    "10 сентября 2026 г.",
    "Инна Гудкова, 06:50:7:00 начало смены. Общ.сч. -750733",
    "Ср.сч. - 5042_ из14500__",
    "Срез - _1__ из _1___",
    "Подмена - _2_ из _4_",
    "Шпуля: навито - _105 из 303",
    "Машина в работе",
    "Продолжаю навивку шпулей",
    "7:57 стоп машина, обрыв нити",
])


def _tmp_store():
    path = tempfile.mkdtemp()
    ss.set_data_dir(path)
    return path


# ─────────────────────────── 1. ДАТА ───────────────────────────

def test_year_tail_is_not_a_date():
    """«г.2026 сентября 10» больше не даёт «26 сентября» как дату."""
    matches = [m.group(0) for m in ss._SECTION_DATE_RE.finditer(REVERSED_HEAD)]
    assert "26 сентября" not in matches, matches
    assert "26.09.2026" not in [ss.detect_shift_date(m) for m in matches], matches
    print("✅ хвост года «20|26 сентября» датой не считается")


def test_time_interval_is_not_a_date():
    """Интервалы времени и ассортимент не читаются как дата."""
    for text in ("подмена 10.05-10.55", "15.25-15.55 переход",
                 "187.3-22-250.5 зел", "Сч 751459, ср сч 5768"):
        assert ss._SECTION_DATE_RE.search(text) is None, text
    for text in ("10 сентября 2026 г.", "10.09.2026", "10.09.26"):
        assert ss._SECTION_DATE_RE.search(text) is not None, text
    print("✅ интервалы и ассортимент датой не считаются")


def test_filename_date_is_the_primary_source():
    """Дата из ИМЕНИ файла — главный источник (текст может быть перевёрнут)."""
    assert ft.date_from_filename("10,09,2026.pdf") == "10.09.2026"
    assert ft.date_from_filename("Отчет_производства_за_смену_10_09_2026.xlsx") == "10.09.2026"
    assert ft.date_from_filename("1С выгрузка 09.09.2026.json") == "09.09.2026"
    assert ft.date_from_filename("смена 10-09-26.txt") == "10.09.2026"
    assert ft.date_from_filename("данные.pdf") == ""
    print("✅ дата из имени файла читается")


def test_reversed_pdf_goes_to_the_right_shift():
    """Перевёрнутый текст из «10,09,2026.*» → смена 10.09.2026, НЕ 26.09.2026.

    Расширение здесь .txt: для .pdf нужен настоящий PDF-контейнер, а нам важно
    проверить именно работу с ТЕКСТОМ (расширение влияет только на выбор
    парсера). Сам парсинг PDF проверен в test_pdf_layout_mode.
    """
    _tmp_store()
    acc = ss.add_document("10,09,2026.txt", REVERSED_HEAD.encode("utf-8"))
    assert acc.date == "10.09.2026", acc.date
    assert acc.date_source == "имя файла", acc.date_source
    assert "26.09.2026" not in acc.date
    print("✅ перевёрнутый текст лёг в смену 10.09.2026")


def test_forward_docx_date_still_from_text():
    """Обычный .docx: дата из текста файла, имя файла её не перебивает зря."""
    _tmp_store()
    acc = ss.add_document("переписка.docx", FORWARD_HEAD.encode("utf-8"))
    assert acc.date == "10.09.2026", acc.date
    # Имя без даты — значит источник «текст файла».
    assert acc.date_source == "текст файла", acc.date_source
    print("✅ прямой текст: дата из содержимого")


# ─────────────────────── 2. ПЕРЕВЁРНУТЫЙ ПОРЯДОК ───────────────────────

def test_reversed_text_is_detected():
    """Детектор узнаёт перевёрнутый текст по характерным склейкам."""
    assert ft.looks_reversed(REVERSED_HEAD) is True
    assert ft.looks_reversed(FORWARD_HEAD) is False
    print("✅ перевёрнутый текст распознаётся")


def test_unreverse_restores_word_order():
    """Разворот строк возвращает читаемый порядок слов."""
    fixed = ft.unreverse_lines(REVERSED_HEAD)
    assert "10 сентября" in fixed, fixed[:60]
    assert "Инна Гудкова" in fixed, fixed
    # Идемпотентно: второй прогон ничего не меняет.
    assert ft.unreverse_lines(fixed) == fixed, "разворот не идемпотентен"
    # Прямой текст не трогается вообще.
    assert ft.unreverse_lines(FORWARD_HEAD) == FORWARD_HEAD
    print("✅ порядок слов восстановлен, идемпотентно")


def test_forward_text_is_never_reversed():
    """Ни одна накопленная прямая смена не считается перевёрнутой."""
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "data", "shifts")
    if not os.path.isdir(base):
        return
    checked = 0
    for date in os.listdir(base):
        p = os.path.join(base, date, "day.json")
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        text = "\n".join(m.get("text") or "" for m in data.get("messages", []))
        if not text.strip():
            continue
        # 26.09.2026 — это как раз перевёрнутый PDF, её пропускаем.
        if date == "26.09.2026":
            continue
        assert ft.looks_reversed(text) is False, f"{date} ложно перевёрнут"
        checked += 1
    assert checked >= 5, f"проверено мало смен: {checked}"
    print(f"✅ {checked} прямых смен не затронуты разворотом")


# ─────────────────────── 3. КАЧЕСТВО ТЕКСТА ───────────────────────

def test_glued_text_is_flagged():
    """Потерянные пробелы помечаются, пользователь узнает прислать .docx."""
    assert ft.text_quality(REVERSED_HEAD) == "glued"
    warn = ft.quality_warning(REVERSED_HEAD, "10,09,2026.pdf", "PDF")
    assert warn and ".docx" in warn, warn
    print("✅ склеенный PDF помечен предупреждением")


def test_clean_text_is_not_flagged():
    """Нормальный текст (docx/txt) предупреждения не даёт."""
    assert ft.text_quality(FORWARD_HEAD) == ""
    assert ft.quality_warning(FORWARD_HEAD, "смена.docx", "Word") == ""
    print("✅ чистый текст без ложного предупреждения")


def test_quality_warning_surfaces_in_accepted():
    """add_document возвращает предупреждение — бот его покажет.

    Файл подаём как .txt, но kind для quality_warning берём из того, что
    вернул file_text; отдельно проверяем, что для PDF-подписи предупреждение
    формируется (именно про PDF мы и советуем прислать .docx).
    """
    _tmp_store()
    acc = ss.add_document("10,09,2026.txt", REVERSED_HEAD.encode("utf-8"))
    assert acc.quality_warning, "предупреждение о качестве потерялось"
    # Для .txt предупреждение пустое (совет про .docx адресован именно PDF),
    # но сам факт низкого качества зафиксирован.
    assert ft.text_quality(ft.unreverse_lines(REVERSED_HEAD)) == "glued"
    # А для PDF подпись предупреждения обязана советовать .docx.
    warn = ft.quality_warning(REVERSED_HEAD, "10,09,2026.pdf", "PDF")
    assert warn and ".docx" in warn, warn
    print("✅ предупреждение о качестве доходит до результата приёма")


def test_pdf_layout_mode_keeps_line_breaks():
    """Настоящий PDF (из Word) читается с переносами и НЕ разворачивается.

    Если pypdf не установлен или тестового PDF нет — пропускаем честно:
    это проверка внешнего инструмента, а не нашей логики.
    """
    path = os.path.join(os.environ.get("TEMP", "/tmp"), "repro_1009.pdf")
    if not os.path.isfile(path):
        print("⚠️ тестового PDF нет — пропускаю")
        return
    try:
        import pypdf                                     # noqa: F401
    except ImportError:
        print("⚠️ pypdf не установлен — пропускаю")
        return
    text, kind = ft.extract_text("10,09,2026.pdf", open(path, "rb").read())
    assert kind == "PDF", kind
    assert ft.looks_reversed(text) is False, "PDF из Word не должен разворачиваться"
    assert "10 сентября 2026 г." in text, text[:200]
    assert "–151" in text and "–98" in text
    assert ft.text_quality(text) == "", "нормальный PDF помечен как склеенный"
    print("✅ PDF из Word читается нормально и не разворачивается")


if __name__ == "__main__":
    for _name, fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(fn):
            fn()
    print("\nРегрессии даты и перевёрнутого PDF проверены.")
