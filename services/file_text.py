"""Извлечение текста из файла, который прислали на сайт.

Зачем это нужно. Переписку смены в мессенджере приходится резать на части по
4096 знаков, и разрез попадает ПОСЕРЕДИНЕ обсуждения станка (прогон
10.09.2026: «–251» оказался в первом сообщении, а «Конец смены … Общий сч.:
278755» — во втором). Хвост без заголовка терялся, а показания уезжали не на
тот станок.

Файл приходит ЦЕЛИКОМ, одним документом, поэтому:

  * текст не режется — нет проблемы «разрез посередине станка»;
  * разделы «–151», «–251» … видны сразу, и их можно разложить по станкам
    детерминированно, до обращения к ИИ (см. shift_store.add_document);
  * выгрузка из мессенджера сохраняет абзацы, поэтому строки не слипаются в
    «ср сч 57688:18 обрыв устранен» (см. shift_store.split_glued_text).

Поддерживаются форматы, которыми реально обмениваются на участке:
 plain text (.txt, .log, .md, .csv), Word (.docx), OpenDocument (.odt),
 Excel (.xlsx), JSON (.json), HTML (.html), PDF (.pdf).

Старые бинарные форматы (.doc, .xls, .rtf) не читаются: их надо пересохранить
в .docx/.xlsx — об этом сайт говорит словами, а не падает молча.

Модуль на чистом stdlib, кроме Excel (openpyxl) и PDF (pypdf) — они импортируются
лениво, и их отсутствие даёт понятный текст, а не трассировку.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime
import xml.etree.ElementTree as ET
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ограничения: документ из Telegram может быть до 20 МБ, но переписка смены —
# это килобайты. Жёсткий потолок защищает диск и запрос к ИИ от случайного
# видео/архива, присланного не в тот чат.
MAX_FILE_BYTES = 8 * 1024 * 1024
# Сколько символов текста максимум уходит в накопитель смены.
MAX_TEXT_CHARS = 400_000

# Что умеем читать и чем.
TEXT_EXTS = (".txt", ".log", ".md", ".csv", ".tsv")
WORD_EXTS = (".docx",)
ODF_EXTS = (".odt",)
EXCEL_EXTS = (".xlsx", ".xlsm")
JSON_EXTS = (".json",)
HTML_EXTS = (".html", ".htm", ".xhtml")
PDF_EXTS = (".pdf",)
# Старые бинарные форматы: не читаем, но объясняем, что сделать.
LEGACY_EXTS = {
    ".doc": "пересохраните как .docx (Файл → Сохранить как → Документ Word)",
    ".xls": "пересохраните как .xlsx (Файл → Сохранить как → Книга Excel)",
    ".rtf": "пересохраните как .docx или .txt",
}

SUPPORTED_EXTS = (TEXT_EXTS + WORD_EXTS + ODF_EXTS + EXCEL_EXTS
                  + JSON_EXTS + HTML_EXTS + PDF_EXTS)


class FileTextError(RuntimeError):
    """Файл прочитать не удалось — причина показывается пользователю."""


class UnsupportedFileError(FileTextError):
    """Формат не поддерживается (или это вообще не документ)."""


class FileTooLargeError(FileTextError):
    """Файл больше MAX_FILE_BYTES."""


class EmptyFileError(FileTextError):
    """Файл открылся, но текста в нём нет."""


# ——— namespace для Office Open XML / OpenDocument ———
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_OO_TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_OO_OFFICE_NS = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def supported_extensions() -> Tuple[str, ...]:
    """Список расширений, которые принимает сайт (для текста помощи)."""
    return SUPPORTED_EXTS


def file_ext(filename: str) -> str:
    """.docx из «Отчет 10.09.docx» (в нижнем регистре, '' если нет)."""
    return os.path.splitext(filename or "")[1].lower()


# ─────────────────────────── кодировки plain text ───────────────────────────
# Выгрузка из мессенджера бывает и в UTF-8, и в cp1251 (Windows), и в cp866
# (старые консоли). Пробуем по порядку: сначала те, что почти не ошибаются,
# потом windows-1251. Latin-1 — последний resort: он декодирует любые байты,
# поэтому раньше него ничего не «провалится» молча.
_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "cp1251", "cp866", "latin-1")


def _decode_bytes(blob: bytes) -> str:
    """Байты → текст, перебирая кодировки. Битый UTF-8 не ломает приём."""
    for enc in _ENCODINGS:
        try:
            return blob.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return blob.decode("utf-8", errors="replace")


def _looks_like_text(text: str) -> bool:
    """В декодированном тексте есть буквы/цифры, а не сплошные спецсимволы."""
    letters = sum(1 for c in text if c.isalnum() or c in " \n\t.,:;-")
    return len(text) > 0 and letters / max(1, len(text)) > 0.5


# ─────────────────────────────── Word / ODF ───────────────────────────────

def _read_zip_xml(blob: bytes, member: str) -> Optional[ET.Element]:
    """Корневой элемент XML из члена zip-архива (docx/odt/xlsx — все zip)."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            if member not in z.namelist():
                return None
            return ET.fromstring(z.read(member))
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError) as e:
        raise FileTextError(f"файл повреждён или это не документ ({e})")


def _from_docx(blob: bytes) -> str:
    """Текст .docx: абзацы и переводы строк сохраняются.

    Именно так устроена выгрузка чата из Word (файлы пользователя
    «10,09,2026.docx»): каждый абзац — одна запись мастера, а разделы станков
    начинаются строкой «–151». Терять переводы строк здесь нельзя.
    """
    root = _read_zip_xml(blob, "word/document.xml")
    if root is None:
        raise FileTextError("внутри .docx нет word/document.xml")
    parts: List[str] = []
    for el in root.iter():
        tag = el.tag
        if tag == _W_NS + "t":
            parts.append(el.text or "")
        elif tag == _W_NS + "tab":
            parts.append("\t")
        elif tag == _W_NS + "br":
            parts.append("\n")
        elif tag == _W_NS + "p":
            parts.append("\n")
    text = "".join(parts)
    # В таблицах Word текст лежит в тех же w:t, поэтому они уже собраны.
    return _clean(text)


def _from_odt(blob: bytes) -> str:
    """Текст .odt (LibreOffice Writer)."""
    root = _read_zip_xml(blob, "content.xml")
    if root is None:
        raise FileTextError("внутри .odt нет content.xml")
    parts: List[str] = []
    block_tags = {_OO_TEXT_NS + "p", _OO_TEXT_NS + "h",
                  _OO_OFFICE_NS + "p", _OO_OFFICE_NS + "h"}
    for el in root.iter():
        if el.tag in block_tags:
            parts.append("\n")
        elif el.tag == _OO_TEXT_NS + "s":
            parts.append(" " * int(el.get(_OO_TEXT_NS + "c", "1") or 1))
        elif el.tag == _OO_TEXT_NS + "tab":
            parts.append("\t")
        elif el.tag == _OO_TEXT_NS + "line-break":
            parts.append("\n")
        elif el.text:
            parts.append(el.text)
    return _clean("".join(parts))


# ──────────────────────────────── Excel ────────────────────────────────

def _from_xlsx(blob: bytes) -> str:
    """Текст .xlsx: по строке на каждую строку листа, ячейки через « | ».

    Порядок листов — как в файле. Пустые ячейки пропускаем, но разделитель
    сохраняем, чтобы колонки не съезжали («Фамилия | | Марка» читается
    однозначнее, чем «Фамилия Марка»).
    """
    try:
        import openpyxl
    except ImportError:                            # pragma: no cover
        raise FileTextError(
            "не установлен openpyxl — Excel-файлы читать нечем. Выполните: "
            "py -m pip install -r requirements.txt")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True,
                                    read_only=True)
    except Exception as e:
        raise FileTextError(f"Excel-файл не открылся ({e})")
    out: List[str] = []
    try:
        for ws in wb.worksheets:
            if len(wb.worksheets) > 1:
                out.append(f"=== лист «{ws.title}» ===")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip() for c in row]
                while cells and not cells[-1]:
                    cells.pop()
                if not cells or not any(cells):
                    continue
                out.append(" | ".join(cells))
    finally:
        wb.close()
    return _clean("\n".join(out))


# ──────────────────────────────── JSON ────────────────────────────────
# Пользователь присылает и выгрузку 1С («1С выгрузка 10.09.2026.json»), и
# дамп сообщений чата. Оба формата надо уметь превратить в читаемый текст:
# для списка сообщений берём их text/body, для остального — печатаем JSON как
# есть (модель его разберёт, а человеку так удобнее сверять).

_JSON_TEXT_KEYS = ("text", "body", "message", "message_text", "content",
                   "caption", "ocr_text")


def _json_collect_text(node: Any, out: List[str], depth: int = 0) -> None:
    """Собрать значения текстовых полей из произвольного JSON."""
    if depth > 12:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if (isinstance(value, str) and value.strip()
                    and str(key).lower() in _JSON_TEXT_KEYS):
                out.append(value)
            else:
                _json_collect_text(value, out, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _json_collect_text(item, out, depth + 1)


def _from_json(blob: bytes) -> str:
    """Текст .json: сообщения чата — их содержимым, иначе — сам JSON."""
    text = _decode_bytes(blob)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Это не JSON, хотя расширение такое: отдаём как обычный текст.
        return _clean(text)
    collected: List[str] = []
    _json_collect_text(data, collected)
    if collected:
        return _clean("\n".join(collected))
    return _clean(json.dumps(data, ensure_ascii=False, indent=1))


# ──────────────────────────────── HTML ────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_END_RE = re.compile(r"</\s*(p|div|br|li|tr|h[1-6]|table)\s*>",
                           re.IGNORECASE)
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_STYLE_SCRIPT_RE = re.compile(
    r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)


def _from_html(blob: bytes) -> str:
    """Текст .html: выкидываем разметку, сохраняем переносы блоков."""
    import html as _html
    text = _decode_bytes(blob)
    text = _STYLE_SCRIPT_RE.sub(" ", text)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    return _clean(_html.unescape(text))


# ──────────────────────────────── PDF ────────────────────────────────

def _from_pdf(blob: bytes) -> str:
    """Текст .pdf (нужен pypdf; скан без текстового слоя не читается)."""
    try:
        import pypdf
    except ImportError:                            # pragma: no cover
        raise FileTextError(
            "для PDF нужен пакет pypdf: py -m pip install pypdf. Либо "
            "перешлите переписку файлом .txt/.docx — их сайт читает без "
            "дополнительных пакетов.")
    try:
        reader = pypdf.PdfReader(io.BytesIO(blob))
        pages = [_pdf_page_text(page) for page in reader.pages]
    except Exception as e:
        raise FileTextError(f"PDF не открылся ({e})")
    text = "\n".join(pages)
    if not text.strip():
        raise EmptyFileError(
            "в PDF нет текстового слоя (это скан-картинка). Пришлите файл "
            ".txt/.docx или фотографией — фото сайт распознаёт через OCR.")
    return _clean(text)


def _pdf_page_text(page) -> str:
    """Текст одной страницы PDF, с восстановлением порядка слов.

    pypdf отдаёт слова в порядке их ОТРИСОВКИ, а не чтения. Для PDF,
    сохранённого из Word, порядок совпадает («10 сентября 2026 г.»). Но если
    файл получен печатью из Telegram/браузера/просмотрщика (реальный прогон
    10.09.2026: «10,09,2026.pdf»), слова в каждой строке приходят ЗАДОМ
    НАПЕРЁД:

        г.2026 сентября 10
        нитиобрывмашина,стоп шпулей
        вмашина оператора, силами устраненобрыв 5768

    Такой текст модель ещё разбирает (она угадывает смысл), а вот регулярки
    дат и остатков ломаются: «20**26** сентября» давало дату 26.09 вместо
    10.09. Поэтому перевёрнутые строки ЧИНИМ КОДОМ — детектор и разворот
    токенов, см. unreverse_lines().

    Режим layout пробуем первым: он сохраняет колонки и переносы, а если его
    нет (старый pypdf) — читаем plain и чиним порядок слов.
    """
    text = ""
    try:
        text = page.extract_text(extraction_mode="layout") or ""
    except (TypeError, ValueError):
        text = ""                        # pypdf без параметра extraction_mode
    except Exception:                    # pragma: no cover
        text = ""
    if not text.strip():
        try:
            text = page.extract_text() or ""
        except Exception:
            return ""
    return text or ""


# ——— перевёрнутый порядок слов (печать PDF из мессенджера/браузера) ———
#
# Детектор: ищем характерные пары «прямой порядок» vs «обратный». В прямом
# тексте мастера пишут «стоп машина», «машина в работе», «Конец смены»,
# «Общ.сч.»; при развороте строки получается «машинастоп», «работев»,
# «смены.Конец», «.сч.Общ». Сравниваем счётчики по всему тексту: где больше,
# так и читаем. Порог «2 и больше перевёрнутых» нужен, чтобы не разворачивать
# текст по одному случайному совпадению.
# Маркеры подбирались по замерам на ВСЕХ накопленных сменах: у прямого текста
# перевёрнутых маркеров 0–2 при 10–90 прямых, у перевёрнутого PDF (прогон
# 10.09.2026) — 18 при 9. Одиночные слова («подмена», «навито») маркерами быть
# не могут: их порядок в строке от разворота не меняется, и они глушили сигнал.
_FORWARD_MARKERS = ("стоп машина", "машина в работе", "конец смены",
                    "начало смены", "машина стоп", "об.сч", "общ.сч", "ср.сч",
                    "навито -", "в работе", "из 303", "из 180")
_REVERSED_MARKERS = ("машинастоп", "работев", "смены.конец", "смены.начало",
                     ".сч.об", ".сч.ср", "стопашина", "амедалопд",
                     "-отиван", "303из", "180из", "г.2026 сентября")


def _order_score(text: str) -> int:
    """Насколько текст похож на нормальный: прямые маркеры минус обратные."""
    low = (text or "").lower()
    return (sum(low.count(m) for m in _FORWARD_MARKERS)
            - sum(low.count(m) for m in _REVERSED_MARKERS))


def looks_reversed(text: str) -> bool:
    """Похоже, что в строках текста слова идут задом наперёд.

    Порог «не меньше 3 перевёрнутых маркеров и их больше, чем прямых» взят с
    запасом: на реальных прямых сменах максимум 2 случайных совпадения, на
    перевёрнутом PDF — 18 при 9 прямых.
    """
    low = (text or "").lower()
    forward = sum(low.count(m) for m in _FORWARD_MARKERS)
    reversed_ = sum(low.count(m) for m in _REVERSED_MARKERS)
    return reversed_ >= 3 and reversed_ > forward


def unreverse_lines(text: str) -> str:
    """Развернуть слова в каждой строке, если весь текст перевёрнут.

    Разворот ПО СЛОВАМ (по пробелам), а не по символам: «10 сентября г.2026»
    получается из «г.2026 сентября 10». Склейки типа «нитиобрывмашина,стоп»
    остаются склеенными — их потом разбирает shift_store.split_glued_text().
    ВАЖНО: выбираем ЛУЧШИЙ из двух вариантов по _order_score, а не
    разворачиваем вслепую. После разворота часть склеек всё равно остаётся
    («смены.начало», «303из»), и детектор снова назвал бы текст перевёрнутым —
    второй прогон развернул бы его обратно. Сравнение счёта делает функцию
    идемпотентной: на уже развёрнутом тексте кандидат набирает меньше, и
    возвращается исходный вариант.
    """
    if not text or not looks_reversed(text):
        return text or ""
    candidate = "\n".join(" ".join(reversed(line.split(" ")))
                          for line in text.split("\n"))
    # Разворот применяем, только если он реально улучшил читаемость.
    if _order_score(candidate) <= _order_score(text):
        return text
    return candidate


# ─────────────────── качество извлечённого текста ───────────────────
#
# PDF — единственный формат, где текст может прийти ИСПОРЧЕННЫМ: порядок слов
# зависит от того, как файл был создан. Word кладёт слова в порядке чтения,
# а «Печать → Сохранить как PDF» из мессенджера/браузера кладёт их в порядке
# ОТРИСОВКИ, то есть задом наперёд, да ещё и без пробелов между словами
# (реальный прогон 10.09.2026, файл «10,09,2026.pdf»):
#
#     «нитиобрывмашина,стоп шпулей»  ←  это «7:57 стоп машина, обрыв нити»
#     «г.2026 сентября 10»           ←  это «10 сентября 2026 г.»
#
# Порядок слов мы чиним (unreverse_lines), а вот потерянные ПРОБЕЛЫ
# восстановить нельзя: «из14500__5042_-Ср.сч.» не распадается на «Ср.сч. -
# 5042_ из 14500__» без словаря. Поэтому такой файл надо опознать и честно
# сказать пользователю, что прислать вместо него — молча выдавать отчёт с
# выдуманными остатками сырья хуже.

# Признак, что пробелы в тексте потеряны: подряд идут склейки «цифра+буква»
# или «буква+буква» там, где в нормальном тексте всегда разделитель.
_GLUED_RE = re.compile(
    r"\d[a-яёa-z]{3,}|[a-яёa-z]{2,}\d{3,}|\d{2,}[a-яёa-z]{2,}\d{2,}")


# Пороги замерены на ВСЕХ накопленных сменах и на реальных файлах:
#   перевёрнутый PDF 10.09.2026 — 8,31 склейки на 100 слов, средняя длина
#                                 слова 9,13;
#   худший из прямых текстов    — 2,08 склейки на 100 слов, средняя 5,81
#                                 (.docx за 10.09 — 2,08/5,31).
# Запас взят двукратный, чтобы рукописные особенности мастеров («Сотволдиев98шт»)
# не давали ложное предупреждение.
_GLUED_RATIO = 4.0        # склеек на 100 слов
_AVG_WORD_LEN = 7.5       # средняя длина слова, символов


def text_quality(text: str) -> str:
    """Короткий диагноз качества текста: "" — нормально, иначе — что не так."""
    if not text or len(text.strip()) < 40:
        return ""
    words = text.split()
    if len(words) < 8:
        return ""
    glued = len(_GLUED_RE.findall(text))
    ratio = 100.0 * glued / len(words)
    avg = sum(len(w) for w in words) / len(words)
    if ratio >= _GLUED_RATIO and avg >= _AVG_WORD_LEN:
        return "glued"
    return ""


def quality_warning(text: str, filename: str = "", kind: str = "") -> str:
    """Готовое сообщение пользователю, если текст извлечён плохо.

    Предупреждаем для ЛЮБОГО источника со склеенными словами, а не только для
    PDF: показания счётчиков в таком тексте читаются ненадёжно независимо от
    того, откуда он пришёл, и молча выдавать отчёт с выдуманными остатками
    сырья хуже, чем сказать правду.
    """
    problem = text_quality(text)
    if not problem:
        return ""
    is_pdf = (file_ext(filename) == ".pdf" or "pdf" in (kind or "").lower())
    head = ("⚠️ В этом PDF порядок слов был обратным, а пробелы между словами "
            "потерялись — так получается, когда PDF делают через «Печать → "
            "Сохранить как PDF» из мессенджера или браузера."
            if is_pdf else
            "⚠️ В присланном тексте слова слиплись без пробелов "
            "(«из14500__5042_-Ср.сч.»).")
    return (head + " Порядок слов я восстановил, поэтому ЧАСЫ по станкам "
            "считаются, но ПОКАЗАНИЯ СЧЁТЧИКОВ и остатки сырья из такого "
            "текста читаются ненадёжно.\n"
            "👉 Пришлите переписку файлом .docx, .txt или .xlsx (или просто "
            "текстом сообщения) — из них всё читается точно. Тот же текст из "
            "Word даёт верную дату и все показания.")


# ─────────────────────────── дата из имени файла ───────────────────────────

# «10,09,2026.pdf», «Отчет_производства_за_смену_10_09_2026.xlsx»,
# «1С выгрузка 09.09.2026.json», «смена 10-09-26.docx».
_FILENAME_DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*[,\-._/]\s*(\d{1,2})\s*[,\-._/]\s*(\d{2,4})(?!\d)")


def date_from_filename(filename: str) -> str:
    """Дата смены из ИМЕНИ файла: «10,09,2026.pdf» → «10.09.2026».

    Это самый надёжный источник: пользователь сам называет файл датой смены,
    и именно так у него заведено («Отчет_производства_за_смену_10_09_2026»,
    «1С выгрузка 09.09.2026»). Содержимое файла при этом может дату вообще не
    содержать в читаемом виде (перевёрнутый PDF, скан, таблица) — тогда без
    имени файла смена уехала бы на сегодня или на случайное число из текста.

    Возвращает "" если даты в имени нет — вызывающий код решит сам.
    """
    name = os.path.splitext(filename or "")[0]
    best = ""
    for m in _FILENAME_DATE_RE.finditer(name):
        day, month, year = m.group(1), m.group(2), m.group(3)
        try:
            d, mo, y = int(day), int(month), int(year)
        except ValueError:
            continue
        if y < 100:
            y += 2000
        if not (1 <= d <= 31 and 1 <= mo <= 12 and 2000 <= y <= 2100):
            continue
        # «13.10.2026» невозможно перепутать, а вот «10.09» vs «09.10» —
        # да. Берём вариант ДЕНЬ.МЕСЯЦ, как его пишет пользователь, и только
        # если он валиден; иначе пробуем МЕСЯЦ.ДЕНЬ.
        try:
            best = datetime(y, mo, d).strftime("%d.%m.%Y")
            break
        except ValueError:
            try:
                best = datetime(y, d, mo).strftime("%d.%m.%Y")
                break
            except ValueError:
                continue
    return best


# ─────────────────────────────── очистка ───────────────────────────────

def _clean(text: str) -> str:
    """Нормализовать пробелы и обрезать до MAX_TEXT_CHARS.

    Убираем невидимые символы, которые ломают поиск маркеров (BOM, нулевой
    пробел, «мягкий» перенос), и схлопываем длинные пустые хвосты. Переводы
    строк СОХРАНЯЕМ: на них держится разбор разделов «–151».
    """
    # Перевёрнутый порядок слов чиним ПЕРВЫМ: дальше идут регулярки, которым
    # нужен нормальный текст. Работает для любого формата, а не только PDF —
    # так же печатают в .txt/.docx из сторонних просмотрщиков.
    text = unreverse_lines(text)
    text = (text or "").replace("\ufeff", "").replace("\u200b", "")
    text = text.replace("\u00ad", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    # PDF в режиме layout ставит по несколько пробелов между словами
    # («Общии   сч.: 278755») — они ломают поиск маркеров, поэтому схлопываем.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        logger.warning("Текст файла обрезан до %d символов", MAX_TEXT_CHARS)
    return text


# ─────────────────────────── единая точка входа ───────────────────────────

def extract_text(filename: str, blob: bytes) -> Tuple[str, str]:
    """Прочитать документ и вернуть (текст, подпись формата для ответа).

    Бросает UnsupportedFileError / FileTooLargeError / EmptyFileError /
    FileTextError — все с текстом, который можно показать пользователю как
    есть (без технических деталей).
    """
    name = filename or "файл"
    ext = file_ext(name)
    if blob is None:
        raise EmptyFileError("файл пришёл пустым")
    if len(blob) > MAX_FILE_BYTES:
        raise FileTooLargeError(
            f"файл «{name}» занимает {len(blob) / 1048576:.1f} МБ — больше "
            f"{MAX_FILE_BYTES // 1048576} МБ. Пришлите только переписку "
            f"смены, без вложений.")
    if not blob:
        raise EmptyFileError(f"файл «{name}» пустой")

    if ext in LEGACY_EXTS:
        raise UnsupportedFileError(
            f"«{name}» — старый формат, я его не читаю: {LEGACY_EXTS[ext]}.")
    if not ext:
        raise UnsupportedFileError(
            f"у файла «{name}» нет расширения — не могу понять формат. "
            f"Поддерживаю: {', '.join(SUPPORTED_EXTS)}.")

    try:
        if ext in WORD_EXTS + ODF_EXTS:
            # Расширение может врать: пользователь иногда переименовывает
            # .txt в .docx или присылает файл, сохранённый другим приложением.
            # Тогда читаем как обычный текст, а не падаем с «не zip» — данные
            # важнее точного соответствия расширения.
            try:
                if ext in WORD_EXTS:
                    return _from_docx(blob), "документ Word"
                return _from_odt(blob), "документ LibreOffice"
            except FileTextError:
                guess = _clean(_decode_bytes(blob))
                if _looks_like_text(guess) and len(guess) >= 20:
                    logger.warning(
                        "Файл %s не открылся как документ — читаю как текст",
                        filename)
                    return guess, "файл с расширением документа (прочитал как текст)"
                raise
        if ext in EXCEL_EXTS:
            return _from_xlsx(blob), "таблица Excel"
        if ext in JSON_EXTS:
            return _from_json(blob), "JSON"
        if ext in HTML_EXTS:
            return _from_html(blob), "HTML-страница"
        if ext in PDF_EXTS:
            return _from_pdf(blob), "PDF"
        if ext in TEXT_EXTS:
            return _clean(_decode_bytes(blob)), "текстовый файл"
    except FileTextError:
        raise
    except Exception as e:                          # pragma: no cover
        logger.exception("Не удалось прочитать файл %s", name)
        raise FileTextError(f"не получилось прочитать «{name}»: {e}")

    # Расширение неизвестное: пробуем как текст, если похоже на текст.
    guess = _clean(_decode_bytes(blob))
    if _looks_like_text(guess) and len(guess) >= 20:
        return guess, "файл без расширения (прочитал как текст)"
    raise UnsupportedFileError(
        f"«{name}» — не текстовый документ. Поддерживаю: "
        f"{', '.join(SUPPORTED_EXTS)}. Переписку можно прислать и обычным "
        f"сообщением или фотографией.")


def extract_with_diagnosis(filename: str, blob: bytes) -> Tuple[str, str, str]:
    """То же, что extract_text, плюс третьим элементом — предупреждение.

    Возвращает (текст, подпись формата, предупреждение о качестве).
    """
    text, kind = extract_text(filename, blob)
    return text, kind, quality_warning(text, filename, kind)


def extract_or_none(filename: str, blob: bytes) -> Tuple[Optional[str], str]:
    """То же, но ошибка возвращается текстом вместо исключения."""
    try:
        return extract_text(filename, blob)
    except FileTextError as e:
        return None, str(e)
