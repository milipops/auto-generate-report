"""Накопитель сообщений смены: «кидаю данные → бот говорит принято».

Зачем отдельное хранилище, а не сразу разбор:

* Данные по станку приходят кусками весь день (текст, фото, текст+фото) от
  разных мастеров. Разбирать каждое сообщение через ИИ — это 20–30 запросов
  за смену и лишние токены, а главное — по одному сообщению не видно
  картины целиком (счётчик начала пришёл утром, конца — вечером).
* Поэтому приём сообщения работает БЕЗ ИИ: сообщение кладётся на диск как
  есть, ответ формируется быстрым разбором регулярками («что я увидел»).
  Один запрос к ИИ делается позже — по кнопке «Отчёт за день», сразу по
  всей накопленной переписке и всем фото.

Формат хранения простой и правится руками:

    data/shifts/20.08.2026/day.json      — сообщения смены по порядку и
                                          отпечатки принятых файлов (повтор
                                          того же файла не дублирует данные)
    data/shifts/20.08.2026/photos/*.jpg  — приложенные фотографии

Фотографии на приёме проходят через бесплатный OCR (services/photo_ocr.py):
фото без текста не сохраняются вовсе, с рабочим текстом — текст уходит в ИИ
вместо картинки, а посторонний текст выносится отдельным списком и в отчёт
не попадает.

Модуль на чистом stdlib (без БД и сторонних пакетов), чтобы логику можно
было тестировать без aiogram/SQLAlchemy.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from runtime_paths import app_path

from . import machine_rates, photo_ocr

logger = logging.getLogger(__name__)

DATE_FMT = "%d.%m.%Y"

# Смена начинается в 07:00 (требование пользователя). Сообщение, пришедшее
# глубокой ночью, относим к прошедшей смене, а не к начинающейся.
NEW_DAY_AFTER_H = 5

_DEFAULT_DIR = os.environ.get(
    "FORWORKTEST_DATA_DIR", app_path("data", "shifts"))
_data_dir = _DEFAULT_DIR

_MONTHS = {  # оставлено для обратной совместимости; разбор идёт по _MONTH_WORD_NUM
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "ма": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}
# «20.08.2026», «20.08.26», «20/08/2026», «20-08-2026»
_DATE_NUM_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\b")
# «20 августа 2026 г.», «20 августа». Месяц — по ЯВНОМУ корню названия, а не
# по короткому префиксу: иначе «21 машине» → «21 мая», «5 марка» → «5 марта»
# и т.п. давали ложные даты. Корни подобраны так, чтобы «машина/марка/…»
# под них не попадали (ма[йя] ≠ «ма-шина»).
_MONTH_WORD_RE = (
    r"(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|"
    r"ноябр|декабр)[а-яё]*")
_MONTH_WORD_NUM = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11,
    "декабр": 12,
}
_DATE_WORD_RE = re.compile(
    r"\b(\d{1,2})\s+" + _MONTH_WORD_RE + r"(?:\s+(\d{4}))?", re.IGNORECASE)
_TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")
# Кандидаты в номер станка: 2–3 цифры или «14тр». Отбираем только те,
# что есть в справочнике норм, — иначе «115 шпулей» станет станком.
# Слева не буква/цифра (чтобы «Сотволдиев98шт» не стало станком 98) и не
# дробь вида «0,98» (одна цифра перед запятой), справа не буква и не цифра.
# При этом «121,251» и «√251» распознаются.
_MACHINE_TOKEN_RE = re.compile(
    r"(?<![\w])(?<!\b\d[.,])(\d{2,3}т[р]?|\d{2,3})(?![\w])", re.IGNORECASE)
_COUNTER_RE = re.compile(r"\b\d{5,8}\b")

# ─────────────── разбиение файла на секции станков (прогон 10.09.2026) ───────────────
#
# Выгрузка чата, присланная файлом, устроена так: каждый станок — ОТДЕЛЬНЫЙ
# РАЗДЕЛ с шапкой «–151», «–251», «–98» на своей строке, дальше заголовок
# обсуждения, имя и время автора («Инна Гудкова, 06:50:»), а затем сообщения.
#
#     –151
#     10 сентября 2026 г.
#     Инна Гудкова, 06:50:7:00 начало смены. Общ.сч. -750733
#     …
#     –251
#     Юлия Черныш, 16:57:Навито 226 из 303 шт. шпулей
#
# Разбив файл по этим шапкам, мы ЗНАЕМ станок каждого сообщения точно — так же
# надёжно, как заголовок темы в Яндекс Мессенджере (см. machine/thread в
# add_message). Это снимает сразу три проблемы прогона 10.09.2026:
#   • модель не угадывает станок по «251» в тексте мастера (это могло быть и
#     количество шпуль);
#   • остатки сырья не уезжают на чужой станок;
#   • длинный файл не режется по 4096 знаков посередине обсуждения.
#
# Тире перед номером обязано стоять НЕ после буквы/цифры («Общ.сч. - 100» —
# значение, а не станок), а номер — заканчивать строку.
# После номера может стоять слово «машина» («–151 машина», «–98 машина») —
# так заголовки оформлены в файле за 14.09.2026. Раньше требовался конец строки
# сразу после цифр, и такой файл НЕ раскладывался по станкам: всё легло одним
# сообщением с machine=None, станки модель угадывала по тексту.
_SECTION_HEAD_TAIL_RE = r"(?:\s*(?:машина|маш|машины|станок)[а-яё\.]*\s*)?(?=\n|$)"
_SECTION_HEAD_RE = re.compile(
    r"(?<![\w\d])\s*[–—\-]\s*(\d{2,3}т[р]?|\d{2,3})" + _SECTION_HEAD_TAIL_RE)
# Строка-подпись автора в экспорте: «Инна Гудкова, 06:50:», «Олег, 07:02:».
_AUTHOR_LINE_RE = re.compile(
    r"^\s*([^\n]{2,60}?,\s*\d{1,2}[:.;]\d{2})\s*:", re.MULTILINE)
# Дата смены из шапки раздела: «10 сентября 2026 г.», «10.09.2026».
# (?<![\w\d]) обязателен в ОБЕИХ альтернативах: без него в «г.2026 сентября 10»
# (перевёрнутая строка из PDF, реальный прогон 10.09.2026) датой считался хвост
# года «26 сентября», и вся смена уезжала на 26.09.2026.
_SECTION_DATE_RE = re.compile(
    # Числовая дата: РАЗДЕЛИТЕЛЬ ОБЯЗАН БЫТЬ ОДИНАКОВЫМ (обратная ссылка \2).
    # Иначе интервал времени «подмена 10.05-10.55» читался как дата «10.05-10»,
    # а ассортимент «187.3-22-250.5» — как «3-22-250» (прогон 10.09.2026).
    r"(?<![\w\d])(\d{1,2})([.\-/])(\d{1,2})\2(\d{4}|\d{2}(?!\d))"
    r"|(?<![\w\d])\d{1,2}\s+"
    r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|"
    r"ноябр|декабр)[а-яё]*(?:\s+\d{4})?", re.IGNORECASE)

# ─────────── плоский экспорт переписки из мессенджера (CSV) ───────────
#
# Выгрузка чата бывает НЕ с шапками «–N», а плоским списком строк
# «дата_время;тип;автор;текст» (файл «22.09.2026.csv» от 22.09.2026). Тема
# машины в таком экспорте — первое сообщение блока:
#
#     2026-09-22 06:36:11;Сообщение;;151 машина(Арташина) - информация за смену 22.09.2026г. 06:36
#     2026-09-22 09:18:05;Ответ;Татьяна Арташина;09:15 совместно с наладчиком … Об. Сч.: 790782 …
#
# Это те же заголовки обсуждений, что разбирает services/yandex_source.py,
# только приехавшие файлом. Без их распознавания весь файл ложился ОДНИМ
# сообщением без станка: номер угадывала модель, а «251» у мастера бывает и
# количеством шпуль.
#
# Разделитель и формат даты в выгрузке зависят от того, КАК её делали
# (Excel-«Сохранить как», буфер обмена из веб-чата, разная региональная
# настройка Windows), поэтому перебираем вероятные разделители и не требуем
# строку заголовка: проверяем содержимое, а не форму.
_EXPORT_DELIMS = (";", "\t", ",")
# Первая колонка — дата/время сообщения: ISO «2026-09-22 06:36:11»,
# «22.09.2026 06:36» или голое время «06:36:11».
_EXPORT_ROW_TS_RE = re.compile(
    r"^\s*(?:"
    r"\d{4}-\d{2}-\d{2}[ T]\d{1,2}[:.]\d{2}"
    r"|\d{1,2}[.-]\d{1,2}[.-]\d{2,4}[ T]\d{1,2}[:.]\d{2}"
    r"|\d{1,2}[:.]\d{2}(?::\d{2})?(?![\d.])"
    r")")
_EXPORT_HAS_TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})")
# Тип строки: тема («Сообщение», «Новое сообщение», «Message») или ответ.
_EXPORT_KIND_RE = re.compile(
    r"^(?:новое\s+)?(?:сообщение|message|ответ|reply|re)\b", re.IGNORECASE)
_EXPORT_ROOT_KIND_RE = re.compile(
    r"^(?:новое\s+)?(?:сообщение|message)\b", re.IGNORECASE)
# «Сообщение 151 машина…» — тип и текст склеились там, где была пустая
# колонка автора (общая очистка текста съедает подряд идущие табы).
_EXPORT_STICKY_KIND_RE = re.compile(
    r"^((?:новое\s+)?(?:сообщение|message|ответ|reply|re)\b)\s+",
    re.IGNORECASE)
# Число в начале заголовка обязано тут же называть машину/станок: иначе
# обычное сообщение «212 протяжных узлов …» стало бы началом новой темы.
_TOPIC_PREFIX_RE = re.compile(
    r"^\s*(?:\d{2,3}т[р]?|\d{2,3})\s*(?:машин|маш|станк)", re.IGNORECASE)
# Подписи колонок в строке заголовка выгрузки. Нужны, потому что набор
# колонок зависит от того, как делали выгрузку: в одном варианте после автора
# идёт сразу текст, в другом — колонка «Роль» ( Администратор / Участники), и
# тогда текст сдвигается на колонку вправо.
_COL_TEXT_RE = re.compile(r"текст|тело|само\s+сообщ", re.IGNORECASE)
_COL_KIND_RE = re.compile(r"^\s*(?:тип|вид)\b", re.IGNORECASE)
_COL_AUTHOR_RE = re.compile(r"автор|имя|фио|участник", re.IGNORECASE)
_COL_TS_RE = re.compile(r"дата|врем", re.IGNORECASE)
# Значение колонки «Роль» одной служебным словом — это не текст сообщения.
_ROLE_CELL_RE = re.compile(
    r"^(?:администратор|владелец|создатель|бот|гость|участник\w*|"
    r"admin|owner|member|bot)$", re.IGNORECASE)
# Пометка о вложении в выгрузке: самого вложения в CSV нет, есть только слово.
_EXPORT_MEDIA_RE = re.compile(r"\[(?:медиа|media|изображения|images?)\]",
                              re.IGNORECASE)
# Разметка чата («**Общ.сч**. -___125077___»), которую копия сообщения теряет.
_EXPORT_MARKUP_RE = re.compile(r"[*_`~]+")
# Приписанное временем в хвосте копии («… 07:06 [медиа]»).
_EXPORT_CLOCK_TAIL_RE = re.compile(r"(?:\s+\d{1,2}[:.]\d{2})+\s*$")
_TIME_ONLY_RE = re.compile(r"^\d{1,2}[:.]\d{2}$")


def _export_message_body(msg: str) -> str:
    """Содержание сообщения без оформления выгрузки.

    Одно и то же сообщение в разных копиях записано по-разному: в теме-
    оригинале — с разметкой чата, в копии — голым текстом с приписанными
    временем и пометкой о фото. Сравнивать их надо по содержанию, иначе копия
    пройдёт за новое сообщение.
    """
    body = _EXPORT_MEDIA_RE.sub(" ", msg or "")
    body = _EXPORT_MARKUP_RE.sub("", body)
    body = re.sub(r"\s+", " ", body).strip()
    return _EXPORT_CLOCK_TAIL_RE.sub("", body).strip()


@dataclass
class FileSection:
    """Один раздел файла: станок + его текст."""
    machine: Optional[str]     # None — преамбула/служебный текст без станка
    text: str
    date: str = ""             # дата из шапки раздела, если названа
    authors: List[str] = field(default_factory=list)
    mirrored: int = 0          # строк, оказавшихся копией из другой темы


def split_sections(text: str) -> List[FileSection]:
    """Разбить текст файла на секции по шапкам «–N».

    Возвращает список по порядку следования. Текст ДО первой шапки идёт
    секцией с machine=None (преамбула выгрузки, служебные строки) — терять его
    нельзя, там бывает дата смены и общие указания.

    Если шапок «–N» в файле нет вовсе (мастер прислал просто переписку),
    возвращаем ОДНУ секцию с machine=None — вызывающий код разберёт станки по
    тексту, как раньше.
    """
    raw_text = text or ""
    text = split_glued_text(raw_text)
    known = set(machine_rates.known_machines())
    heads: List[Tuple[int, str]] = []
    for m in _SECTION_HEAD_RE.finditer(text):
        key = machine_rates._norm_key(m.group(1).replace(" ", ""))
        if key in known:
            heads.append((m.start(), key))
    if not heads:
        # ВАЖНО: плоский экспорт разбираем по НЕрасклеенному тексту.
        # split_glued_text вставляет перевод строки и ВНУТРИ записи
        # («Склад 49-24.08.26» → перенос перед «08.26», принятым за 08:26),
        # а здесь физическая строка = сообщение: на разорванной записи
        # терялся счётчик станка №98. При выдаче расклейка применяется сама
        # (см. day_text и remains_report), в накопителе она не нужна.
        csv_secs = _sections_from_chat_export(raw_text)
        if csv_secs:
            return csv_secs
        return [FileSection(machine=None, text=text,
                            date=detect_shift_date(text, None) or "",
                            authors=_section_authors(text))]

    out: List[FileSection] = []
    # Преамбула до первой шапки.
    if heads[0][0] > 0:
        head_text = text[:heads[0][0]].strip()
        if head_text:
            out.append(FileSection(machine=None, text=head_text,
                                   date=detect_shift_date(head_text, None) or "",
                                   authors=_section_authors(head_text)))
    for i, (at, key) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        body = text[at:end].strip()
        date = ""
        m = _SECTION_DATE_RE.search(body)
        if m:
            date = detect_shift_date(m.group(0), None) or ""
        out.append(FileSection(machine=key, text=body, date=date,
                               authors=_section_authors(body)))
    return out


def _section_authors(text: str) -> List[str]:
    """Кто писал в разделе: «Инна Гудкова», «Олег» (по подписям экспорта)."""
    out: List[str] = []
    for m in _AUTHOR_LINE_RE.finditer(text or ""):
        name = m.group(1).rsplit(",", 1)[0].strip()
        if name and name not in out and len(name) <= 60:
            out.append(name)
    return out[:8]


def _export_row_cells(text: str, delim: str) -> List[List[str]]:
    """Строки экспорта, нарезанные разделителем `delim` (пусто — не читается).

    Неровный кавычкам доверять нельзя: одна незакрытая кавычка в тексте
    (мастер пишет «2 полотна, "зеленые" ») заставляет csv читать НЕСКОЛЬКО
    строк как одну, и файл рассыпается. Если строк получилось заметно меньше,
    чем переводов строк в тексте, — это не выгрузка, а испорченный разбор.
    """
    try:
        rows = [[c.strip() for c in row] for row in
                csv.reader(io.StringIO(text or ""), delimiter=delim) if row]
    except csv.Error:                                # pragma: no cover
        return []
    lines = len([ln for ln in (text or "").splitlines() if ln.strip()])
    if lines and len(rows) < lines * 0.6:
        return []
    return rows


def _export_columns(header: List[str]) -> Optional[Dict[str, int]]:
    """Позиции колонок из строки заголовка выгрузки: {'msg':4,'kind':1,…}.

    Берём только подписи, а не содержимое: в выгрузке 22.09.2026 между автором
    и текстом стоит колонка «Роль» («Администратор»), и без карты колонок её
    значение приклеивалось к тексту («Администратор; 14т машина…»), из-за чего
    заголовок темы не узнавался и вся смена ложилась одним сообщением без
    станка. Нераспознанные колонки в текст не попадают.

    None — заголовка нет или он непонятен: тогда колонки ищем по содержимому
    (см. _export_fields).
    """
    cells = [c or "" for c in (header or [])]
    if len(cells) < 3 or _EXPORT_ROW_TS_RE.match(cells[0]):
        return None
    cols: Dict[str, int] = {}
    taken = set()
    # Порядок важен: сначала текст (его подпись самая однозначная), потом
    # тип/автор/дата — иначе «Дата и Время сообщения» съест колонку текста.
    for key, rx in (("msg", _COL_TEXT_RE), ("kind", _COL_KIND_RE),
                    ("author", _COL_AUTHOR_RE), ("ts", _COL_TS_RE)):
        for i, c in enumerate(cells):
            if i in taken or not c:
                continue
            if rx.search(c):
                cols[key] = i
                taken.add(i)
                break
    # Одного текста мало: без второй подтверждённой колонки это не заголовок.
    if "msg" not in cols or len(taken) < 2:
        return None
    return cols


def _export_fields(cells: List[str],
                   cols: Optional[Dict[str, int]] = None
                   ) -> Tuple[str, str, str, str]:
    """(время, автор, текст, тип) строки выгрузки — колонки ищем ПО СОДЕРЖИМОМУ.

    Набор и порядок колонок зависят от того, чем выгрузку делали: где-то есть
    колонка «Тип», где-то только «Дата;Автор;Текст», где-то тип называется
    иначе. Поэтому тип узнаём по подписи («Сообщение», «Ответ»), а текстом
    считаем всё, что осталось после него и автора.

    Если строка заголовка прочитана (`cols`), колонки берём по её подписям —
    это единственный способ не схлопотать в текст соседнюю колонку («Роль»).
    Кривую строку (не хватило ячеек) всё равно разбираем по содержимому.

    Пустую колонку автора в табулированной выгрузке съедает общая очистка
    текста (она схлопывает идущие подряд пробелы и табы, чтобы читался PDF из
    Word), и «Сообщение» склеивается с текстом в одну ячейку — такую склейку
    тоже раскладываем.
    """
    cells = list(cells)
    if cols:
        def _at(key: str) -> str:
            i = cols.get(key)
            return cells[i].strip() if (i is not None and len(cells) > i
                                        and cells[i]) else ""
        msg = _at("msg")
        if msg:
            ts = _at("ts") or (cells[0] if cells else "")
            return ts, _at("author"), msg, _at("kind")
    for i, c in enumerate(cells[1:], start=1):
        m = _EXPORT_STICKY_KIND_RE.match(c) if c else None
        if m:
            cells[i:i + 1] = [m.group(1), c[m.end():].strip()]
            break
    rest = cells[1:]
    kind_at = next((i for i, c in enumerate(rest)
                    if c and _EXPORT_KIND_RE.match(c)), None)
    kind = ""
    if kind_at is not None:
        kind = rest[kind_at]
        rest = rest[:kind_at] + rest[kind_at + 1:]
    author = rest[0] if len(rest) >= 2 else ""
    # В выгрузке без читаемого заголовка роль («Администратор») приходит
    # отдельной ячейкой между автором и текстом. Её видно по форме: одно
    # служебное слово, ни даты, ни станка. Оставлять её в тексте нельзя —
    # заголовок темы перестанет распознаваться.
    msg = ("; ".join(c for c in rest[1:] if c and not _ROLE_CELL_RE.match(c))
           if len(rest) >= 2 else (rest[0] if rest else ""))
    return cells[0], author, msg, kind


def _sections_with_delim(text: str, delim: str
                         ) -> Tuple[List["FileSection"], int, int]:
    """(секции, число разобранных строк, всего строк с датой/временем)."""
    rows = _export_row_cells(text, delim)
    if len(rows) < 3:
        return [], 0, 0

    from . import yandex_source        # круг на уровне импорта — берём локально

    # Строка заголовка (если она есть в начале файла) задаёт порядок колонок.
    cols = None
    for row in rows[:3]:
        cols = _export_columns(row)
        if cols:
            break

    sections: List[FileSection] = []
    cur: Optional[FileSection] = None
    cur_lines: List[str] = []
    answered = data_rows = mirrored = 0
    # Строки, похожие на сообщение (в тексте есть время), но отброшенные,
    # потому что клетка времени не разобралась. Молча терять их нельзя — по
    # числу видно, что выгрузка прочитана не вся.
    content_dropped = 0
    # Подписи уже взятых сообщений. Выгрузка тем умеет приложить ОДНО И ТО ЖЕ
    # сообщение сразу к нескольким темам: в файле за 24.09.2026 вся смена
    # станка №321 (139 строк: пуск/стоп, «сошла шпуля», показания счётчика,
    # килограммы навивки) повторена в теме №14тр, которая стояла весь день.
    # Парсер остатков видел те же отходы в обоих разделах, и «Сдаваемые отходы
    # за смену» удвоились (8,4 кг вместо 4,2). Копия отличается от оригинала
    # только оформлением, а время и автор совпадают до секунды — это и есть
    # признак одного и того же сообщения. Хозяином остаётся тема, где сообщение
    # встретилось первым: блок-зеркало выгружается после оригинала.
    seen: set = set()

    def _flush() -> None:
        if cur is not None and cur_lines:
            cur.text = "\n".join(cur_lines).strip()

    for cells in rows:
        ts, author, msg, kind = _export_fields(cells, cols)
        if not _EXPORT_ROW_TS_RE.match(ts or ""):
            # Строка заголовка выгрузки, перенос текста. Время ищем НЕ в первой
            # клетке, а в колонке, которую назвал заголовок: в выгрузке, где
            # «Дата и Время» стоит не первой (например, после колонки «Тип»),
            # проверка по cells[0] молча выбросила бы ВЕСЬ файл.
            if msg and _EXPORT_HAS_TIME_RE.search(msg):
                content_dropped += 1
            continue
        if not msg:
            continue
        body = _export_message_body(msg)
        if not body or _TIME_ONLY_RE.match(body):
            # Пометка о фото или голое время: в CSV вложение не переносится,
            # сказать такой строке отчёту нечего.
            continue
        key = f"{ts.strip().lower()}|{author.strip().lower()}|{body}"
        if key in seen:
            mirrored += 1
            if cur is not None:
                cur.mirrored += 1
            continue
        seen.add(key)
        data_rows += 1
        tm = _EXPORT_HAS_TIME_RE.search(ts)
        hhmm = ""
        if tm:
            try:
                hhmm = f"{int(tm.group(1)):02d}:{tm.group(2)}"
            except ValueError:       # pragma: no cover
                hhmm = ""
        line = (f"{author}, {hhmm}: {msg}" if author and hhmm else
                f"{author}: {msg}" if author else
                f"{hhmm}: {msg}" if hhmm else msg)
        # Новую тему открывает КОРНЕВОЕ сообщение. Мастер под темой часто
        # дословно повторяет заголовок ответом («Юлия Черныш: 151 машина…» в
        # файле за 22.09.2026) — без проверки типа это стал бы вторая секция
        # того же станка, и показания разделились бы пополам.
        is_root = not kind or bool(_EXPORT_ROOT_KIND_RE.match(kind))
        parsed = (yandex_source.parse_thread_header(msg)
                  if is_root and _TOPIC_PREFIX_RE.match(msg) else None)
        if parsed:
            _flush()
            machine, sdate = parsed
            cur = FileSection(machine=machine, text="", date=sdate or "")
            cur_lines = [line]
            sections.append(cur)
            answered += 1
        elif cur is not None:
            cur_lines.append(line)
            answered += 1
    _flush()
    out = [s for s in sections if s.text.strip()]
    # Соседние секции с одним станком — это эхо заголовка темы первым ответом
    # («Юлия Черныш: 151 машина…» в файле за 22.09.2026), когда колонки «Тип»
    # в выгрузке нет и отличить корневое сообщение от повтора нечем. Раздел
    # станка при этом ломается пополам, поэтому склеиваем.
    merged: List[FileSection] = []
    for s in out:
        prev = merged[-1] if merged else None
        if prev is not None and prev.machine == s.machine:
            prev.text = (prev.text + "\n" + s.text).strip()
            prev.date = prev.date or s.date
            prev.mirrored += s.mirrored
            continue
        merged.append(s)
    for s in merged:
        s.authors = _section_authors(s.text)
    if mirrored:
        logger.info("Выгрузка: %d строк оказались копиями из других тем — "
                    "в разделы не попали", mirrored)
    if content_dropped:
        logger.warning("Выгрузка: %d строк с временем в тексте отброшены — "
                       "клетка даты/времени не разобралась", content_dropped)
    return merged, answered, data_rows


def _sections_from_chat_export(text: str) -> List["FileSection"]:
    """Разложить ПЛОСКИЙ экспорт чата по машинам (см. комментарий выше).

    Перебираем возможные разделители колонок и берём тот, при котором тем
    распозналось больше: выгрузку делают то Excel («;»), то буфером обмена
    («\\t»), то с запятой, и угадывать по первой попытке нельзя.
    """
    best: Tuple[int, int, int, List[FileSection]] = (0, 0, 0, [])
    for delim in _EXPORT_DELIMS:
        out, answered, data_rows = _sections_with_delim(text, delim)
        if (len(out), answered) > (best[0], best[1]):
            best = (len(out), answered, data_rows, out)
        # Разделитель угадан и разобрано почти всё — искать дальше незачем.
        if data_rows and answered >= data_rows * 0.9 and len(out) >= 2:
            break
    _, answered, data_rows, out = best
    # Тем со станком меньше двух или разобралось меньше половины строк —
    # значит это не понятная нам переписка; молча выбрасывать строки нельзя,
    # и вызывающий код сохранит файл как раньше (одним блоком).
    if len(out) < 2 or answered < data_rows * 0.5:
        return []
    return out





def set_data_dir(path: str) -> None:
    """Сменить каталог хранения (используется в тестах)."""
    global _data_dir
    _data_dir = path


def get_data_dir() -> str:
    return _data_dir


def shift_date_for(now: Optional[datetime] = None) -> str:
    """Дата смены по текущему времени (ночные сообщения — к прошедшей смене)."""
    now = now or datetime.now()
    if now.hour < NEW_DAY_AFTER_H:
        now = now - timedelta(days=1)
    return now.strftime(DATE_FMT)


def _valid_date(day: int, month: int, year: int) -> Optional[str]:
    try:
        return datetime(year, month, day).strftime(DATE_FMT)
    except ValueError:
        return None


def _month_from_word(word: str) -> Optional[int]:
    w = (word or "").lower()
    # Длинные корни раньше коротких: «март» до «ма», чтобы «марта» не стало маем.
    for root, num in sorted(_MONTH_WORD_NUM.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(root):
            return num
    return None


def _most_common_date(dates: List[str]) -> Optional[str]:
    """Самая частая дата в списке; при равенстве — та, что встретилась раньше."""
    if not dates:
        return None
    best, best_count, best_pos = None, -1, None
    seen: Dict[str, int] = {}
    for pos, d in enumerate(dates):
        seen[d] = seen.get(d, 0) + 1
    first_pos = {d: dates.index(d) for d in seen}
    for d, cnt in seen.items():
        pos = first_pos[d]
        if cnt > best_count or (cnt == best_count and pos < best_pos):
            best, best_count, best_pos = d, cnt, pos
    return best


def detect_shift_date(text: str, now: Optional[datetime] = None) -> str:
    """Определить дату смены из текста переписки, иначе — текущая смена.

    Мастера присылают выгрузку чата, где НАД КАЖДЫМ станком стоит заголовок
    «26 августа 2026 г.». В теле переписки при этом полно ПОСТОРОННИХ дат:
    «согласно заданию от 05.08.26», «остановлена до 27.08.2026». Раньше брали
    первую же дату и получали смену за 05.08 или 27.08 вместо 26.08.

    Поэтому:
    1) сначала собираем ВСЕ даты-заголовки словами («26 августа 2026») — их
       обычно несколько (по одному на станок), и берём самую частую;
    2) если словесных заголовков нет — берём самую частую ЧИСЛОВУЮ дату
       («19.08.2026» в первой строке);
    3) если дат нет вовсе — считаем, что данные за текущую смену.
    """
    text = text or ""
    now = now or datetime.now()

    word_dates: List[str] = []
    for m in _DATE_WORD_RE.finditer(text):
        month = _month_from_word(m.group(2))
        if not month:
            continue
        year = int(m.group(3)) if m.group(3) else now.year
        found = _valid_date(int(m.group(1)), month, year)
        if found:
            word_dates.append(found)
    picked = _most_common_date(word_dates)
    if picked:
        return picked

    num_dates: List[str] = []
    for m in _DATE_NUM_RE.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        found = _valid_date(day, month, year)
        if found:
            num_dates.append(found)
    picked = _most_common_date(num_dates)
    if picked:
        return picked

    return shift_date_for(now)


# ─────────────────────────── быстрый разбор без ИИ ───────────────────────────

@dataclass
class Scan:
    """Что видно в сообщении «на глаз», без обращения к ИИ."""
    machines: List[str] = field(default_factory=list)
    counters: int = 0
    times: int = 0
    unknown_machines: List[str] = field(default_factory=list)


def scan_text(text: str) -> Scan:
    """Найти в тексте номера станков, показания счётчиков и метки времени.

    Нужно только для ответа «данные приняты, вижу то и то» — расчёт по этим
    данным не делается, поэтому ошибка распознавания здесь ничего не портит.
    """
    text = text or ""
    known = set(machine_rates.known_machines())
    machines: List[str] = []
    unknown: List[str] = []
    for raw in _MACHINE_TOKEN_RE.findall(text):
        key = machine_rates._norm_key(raw.replace(" ", ""))
        if key in known:
            if key not in machines:
                machines.append(key)
        elif len(key) == 3 and key.isdigit() and key not in unknown:
            # Трёхзначное число, похожее на номер станка, но не из справочника —
            # обычно это количество («303 из 303»), поэтому просто запоминаем.
            unknown.append(key)
    return Scan(machines=machines,
                counters=len(_COUNTER_RE.findall(text)),
                times=len(_TIME_RE.findall(text)),
                unknown_machines=unknown)


# ──────────────────────────────── хранилище ─────────────────────────────────

def _day_dir(date: str) -> str:
    return os.path.join(_data_dir, date.replace("/", "."))


def _day_file(date: str) -> str:
    return os.path.join(_day_dir(date), "day.json")


def load_day(date: str) -> Dict:
    """Прочитать накопленное за смену. Пустая структура, если ничего нет."""
    path = _day_file(date)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"date": date, "messages": [], "updated_at": None}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Не удалось прочитать %s: %s", path, e)
        return {"date": date, "messages": [], "updated_at": None}
    data.setdefault("date", date)
    data.setdefault("messages", [])
    data.setdefault("updated_at", None)
    return data


def _save_day(data: Dict) -> None:
    path = _day_file(data["date"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ────────────────────── принятые файлы: защита от повтора ──────────────────────
# Один и тот же файл, присланный дважды, ложился в смену ДВУМЯ копями:
# обсуждение станка уезжало в ИИ два раза, и модель считала удвоенные
# количества. Хеш текста файла на смену — чтобы второй раз сказать
# «уже принимал» и ничего не дописывать.
#
# Считаем по ИЗВЛЕЧЁННОМУ ТЕКСТУ, а не по байтам: тот же чат, выгруженный
# ещё раз (или пересохранённый из Word), приходит другими байтами, а содержание
# у него то же. Нормализация пробелами — файл, сохранённый через другой
# редактор, не должен выглядеть «новым».


def _text_fingerprint(text: str) -> str:
    """Отпечаток текста файла: цифры и буквы, без оформления."""
    flat = "".join((text or "").split())
    return hashlib.sha1(flat.encode("utf-8")).hexdigest()


def accepted_documents(date: str) -> List[Dict]:
    """Файлы, уже принятые в эту смену: [{name, fingerprint, sections}, …].

    Смены, накопленные до появления защиты, возвращаются пустыми — первый
    присланный в такую смену файл не считается повтором.
    """
    return list(load_day(date).get("documents") or [])


def _remember_document(date: str, name: str, fingerprint: str,
                       sections: int, machines: List[str],
                       stamp: datetime, replace: bool = False) -> None:
    data = load_day(date)
    docs = list(data.get("documents") or [])
    record = {"name": os.path.basename(name or ""),
              "fingerprint": fingerprint,
              "sections": sections,
              "machines": list(machines),
              "added_at": stamp.isoformat(timespec="seconds")}
    if replace:
        for n, d in enumerate(docs):
            if (d.get("name") or "") == record["name"]:
                docs[n] = record
                break
        else:
            docs.append(record)
    else:
        docs.append(record)
    data["documents"] = docs
    _save_day(data)


def _forget_document(date: str, name: str) -> int:
    """Убрать из смены разделы одного принятого файла — под перезаём.

    Признак — нитка «файл X, раздел N»: она бывает только у сообщений этого
    файла. Подпись к файлу («вот смена за 10.09») не трогаем — это не данные
    станка, а комментарий, и при перезаёме он остаётся на месте.
    """
    data = load_day(date)
    base = f"файл {name}"
    keep = [m for m in data["messages"]
            if not ((m.get("thread") or "") == base
                    or (m.get("thread") or "").startswith(base + ", раздел "))]
    removed = len(data["messages"]) - len(keep)
    if removed:
        data["messages"] = keep
        _save_day(data)
    return removed


@dataclass
class Accepted:
    """Результат приёма сообщения — из этого строится ответ «принято»."""
    date: str
    index: int
    messages: int
    photos: int
    scan: Scan
    # Что сделали с приложенными фотографиями (см. services/photo_ocr.py).
    photos_saved: int = 0
    photos_skipped: int = 0          # без текста — не сохраняем
    photos_foreign: int = 0          # текст есть, но не про работу
    photos_work: int = 0             # текст снят и уйдёт в отчёт
    photos_vision: int = 0           # текст снять не удалось — отдаём картинкой
    photo_notes: List[str] = field(default_factory=list)
    ocr_warning: str = ""            # OCR недоступен — сказать один раз
    # Приём ФАЙЛА (add_document): сколько секций/станков разложено и что это
    # был за документ. У обычного сообщения эти поля пустые.
    file_name: str = ""
    file_kind: str = ""
    file_sections: int = 0
    file_machines: List[str] = field(default_factory=list)
    file_authors: List[str] = field(default_factory=list)
    file_date: str = ""              # дата смены из текста файла
    # Предупреждение о качестве извлечённого текста (см. file_text.
    # quality_warning): пустая строка — всё прочиталось нормально.
    quality_warning: str = ""
    # Откуда взята дата смены: "имя файла" / "текст файла" / "текущая смена"
    # / "шапки разделов" (все темы файла называют одну другую дату) /
    # "задана явно".
    date_source: str = ""
    # Файл уже лежал в этой смене (см. accepted_documents): данные не
    # добавляются, разделы этого файла перечитываются на месте старых.
    file_duplicate: bool = False
    duplicate_of: str = ""            # имя того, ранее принятого, файла
    # Строки выгрузки, оказавшиеся копией сообщения из другой темы (см.
    # _sections_with_delim): в разделы они не легли, чтобы отчёт не счёл
    # одни и те же отходы дважды.
    file_mirrors: int = 0


def _screen_photos(photos: List[bytes]) -> Tuple[List[bytes], List[Dict],
                                                 List[str], str]:
    """Прогнать фото через бесплатный OCR и решить судьбу каждого.

    Возвращает (что сохранить, описания для day.json, заметки для ответа
    бота, предупреждение про недоступный OCR).
    """
    keep: List[bytes] = []
    meta: List[Dict] = []
    notes: List[str] = []
    warning = ""
    for n, blob in enumerate(photos, start=1):
        check = photo_ocr.check_photo(blob)
        if not check.keep:
            # Фото «показать материал»: текста нет, данных нет — не храним.
            notes.append(f"фото {n}: {check.reason} — пропускаю")
            continue
        keep.append(blob)
        meta.append({
            "verdict": check.verdict,
            "reason": check.reason,
            "ocr_text": check.text,
            # to_prompt/to_vision решают, что уйдёт в единственный запрос к ИИ.
            "to_prompt": check.to_prompt,
            "to_vision": check.to_vision,
        })
        if check.verdict == photo_ocr.WORK:
            notes.append(f"фото {n}: распознан текст ({check.reason})")
        elif check.verdict == photo_ocr.FOREIGN:
            notes.append(f"фото {n}: текст не про работу — вынесу отдельно")
        else:
            notes.append(f"фото {n}: {check.reason}")
            if not warning:
                warning = check.reason
    return keep, meta, notes, warning


def add_message(text: str = "",
                photos: Optional[List[bytes]] = None,
                author: Optional[str] = None,
                date: Optional[str] = None,
                now: Optional[datetime] = None,
                machine: Optional[str] = None,
                thread: Optional[str] = None) -> Accepted:
    """Сохранить входящее сообщение смены как есть (без ИИ и без расчётов).

    photos — содержимое приложенных фотографий. Каждое проходит бесплатный
    OCR: без текста фото не сохраняется, с рабочим текстом в отчёт уходит
    распознанный текст, посторонний текст откладывается отдельно.

    machine/thread заполняются, когда источник знает станок ТОЧНО, а не по
    догадке из текста: в Яндекс Мессенджере руководитель открывает обсуждение
    заголовком «91 машина информация за смену 20.08.2026», и все ответы в
    ветке — про этот станок. Такой номер надёжнее найденного в тексте («251»
    у мастера может быть и количеством шпуль), поэтому он передаётся ИИ
    отдельной пометкой и учитывается в day_machines().
    """
    text = (text or "").strip()
    photos = photos or []
    date = date or detect_shift_date(text, now)
    data = load_day(date)
    # Нумерация продолжена за самой свежей записью, а не по длине списка:
    # после перезаёма файла (см. _forget_document) в номерах остаётся дырка, и
    # «len + 1» дал бы номер занятого сообщения — имя файла фото считается из
    # номера, и снимок перезаписал бы чужой.
    index = max([int(m.get("index") or 0) for m in data["messages"]],
                default=0) + 1

    keep, meta, notes, warning = _screen_photos(photos)

    names: List[str] = []
    if keep:
        photo_dir = os.path.join(_day_dir(date), "photos")
        os.makedirs(photo_dir, exist_ok=True)
        for n, blob in enumerate(keep, start=1):
            name = f"msg{index:03d}_{n}.jpg"
            with open(os.path.join(photo_dir, name), "wb") as f:
                f.write(blob)
            names.append(name)
            meta[n - 1]["name"] = name

    data["messages"].append({
        "index": index,
        "author": author,
        "text": text,
        "photos": names,
        # Итог OCR по каждому сохранённому фото — рядом с ним, чтобы отчёт и
        # ответ бота не пересчитывали распознавание заново.
        "photos_meta": meta,
        # Станок и обсуждение — только если источник их знает наверняка
        # (заголовок темы в Мессенджере). Из Telegram приходит None.
        "machine": machine,
        "thread": thread,
        # Время получения храним отдельно и НЕ отдаём ИИ: иначе модель может
        # принять его за время производственного события.
        "received_at": (now or datetime.now()).isoformat(timespec="seconds"),
    })
    data["updated_at"] = (now or datetime.now()).isoformat(timespec="seconds")
    _save_day(data)

    return Accepted(date=date, index=index, messages=len(data["messages"]),
                    photos=sum(len(m.get("photos") or [])
                               for m in data["messages"]),
                    scan=scan_text(text),
                    photos_saved=len(names),
                    photos_skipped=len(photos) - len(keep),
                    photos_foreign=sum(1 for m in meta
                                       if m["verdict"] == photo_ocr.FOREIGN),
                    photos_work=sum(1 for m in meta
                                    if m["verdict"] == photo_ocr.WORK),
                    photos_vision=sum(1 for m in meta if m["to_vision"]),
                    photo_notes=notes,
                    ocr_warning=warning)


def add_document(filename: str, blob: bytes,
                 author: Optional[str] = None,
                 date: Optional[str] = None,
                 now: Optional[datetime] = None,
                 caption: str = "") -> Accepted:
    """Принять ФАЙЛ с перепиской смены: прочитать, разложить по станкам, сохранить.

    Отличие от add_message принципиальное: файл приходит ЦЕЛИКОМ, поэтому

      * текст не режется по лимиту Telegram (4096 знаков) — а именно разрез
        посередине обсуждения станка сломал прогон 10.09.2026;
      * разделы «–151», «–251», «–98» видны сразу, и станок каждого куска
        известен ТОЧНО (machine=… в накопителе), а не угадан моделью по цифре
        в тексте;
      * каждая секция сохраняется ОТДЕЛЬНЫМ сообщением, поэтому в день можно
        дослать ещё один файл или отдельные сообщения — они не перемешаются;
      * повтор ТОГО ЖЕ файла в ту же смену данных не добавляет — его разделы
        перечитываются и кладутся вместо старых (см. _text_fingerprint и
        _forget_document): иначе ИИ получил бы каждое обсуждение дважды, а
        молча проигнорированный повтор не починил бы уже принятую смену.

    Порядок секций сохраняется. Преамбула файла (текст до первой шапки) идёт
    первым сообщением без станка — в ней бывает дата смены и общие указания.

    Ошибки формата/размера/пустого файла бросают file_text.FileTextError:
    вызывающий код показывает её текст пользователю как есть.
    """
    from . import file_text

    text, kind = file_text.extract_text(filename, blob)
    warning = file_text.quality_warning(text, filename, kind)
    sections = split_sections(text)

    # Дата смены — по убыванию надёжности:
    #   1) ИМЯ ФАЙЛА («10,09,2026.pdf», «Отчет_…_за_смену_10_09_2026.xlsx»):
    #      пользователь сам называет файл датой смены, и это единственный
    #      источник, который не зависит от того, как легли буквы в документе;
    #   2) текст файла («10 сентября 2026 г.» в шапке раздела);
    #   3) текущая смена.
    # Прогон 10.09.2026: PDF из мессенджера отдал слова задом наперёд
    # («г.2026 сентября 10»), и смена уехала на 26.09 — дату из имени файла
    # так не сломать.
    #
    # Имя файла НЕ всесильно: если ВСЕ шапки тематических разделов (не меньше
    # двух) называют одну и ту же ДРУГУЮ дату, мастер скопировал имя с
    # прошлого файла. Прогон 28.09.2026: выгрузка «26.09.2026.csv» легла в
    # смену 26.09, хотя все десять её тем озаглавлены «информация за смену
    # 27.09.2026г.». Название темы пишет тот, кто открывает тему смены, и
    # одинаковое оно у всех станков — это факт о данных, а имя файла — только
    # предположение. Молча положить смену не того дня хуже, чем спросить:
    # счётчики и отходы переезжают в чужую смену, а своя остаётся пустой.
    # При споре шапок (станки назвали разные даты) имя файла остаётся главным.
    date_source = "задана явно"
    file_date = date or ""
    name_date = "" if file_date else file_text.date_from_filename(filename)
    # Согласие шапок: ВСЕ названные станками даты — одной секции, и таких
    # секций минимум две. Одна шапка — это ещё не хор.
    named = [(s.machine, s.date) for s in sections if s.machine and s.date]
    agreed = ""
    if len(named) >= 2 and len({d for _m, d in named}) == 1:
        agreed = named[0][1]
    # Спор возможен только если дата уже выбрана КЕМ-ТО до шапок: явным выбором
    # или именем файла. Без них перебивать нечего — дата и так пойдёт из шапок.
    chosen = file_date or name_date
    if agreed and chosen and agreed != chosen:
        machines = ", ".join("№" + m for m, _d in named)
        if file_date:
            # Дату выбрал пользователь в интерфейсе: она главнее текста файла,
            # но спор показываем, а не прячем.
            extra = (
                f"⚠️ Дата смены выбрана {file_date}, но все шапки разделов "
                f"файла — про смену {agreed} ({machines}). Положил данные в "
                f"{file_date}, как вы и просили.")
        else:
            file_date = agreed
            date_source = "шапки разделов"
            extra = (
                f"⚠️ Файл называется «{os.path.basename(filename or '')}» — "
                f"{name_date}, а все шапки разделов внутри него говорят про "
                f"смену {agreed} ({machines}). Дату взял из шапок: их пишет "
                f"тот, кто открывает тему смены, а имя файла берут из прошлой "
                f"пересылки.\n"
                f"👉 Если верным было имя файла ({name_date}) — очистите "
                f"смену {agreed} и пришлите файл заново с датой {name_date}.")
        warning = "\n\n".join(x for x in (warning, extra) if x)
    if not file_date:
        file_date = name_date
        if file_date:
            date_source = "имя файла"
    if not file_date:
        # Дату берём из шапки ТЕМАТИЧЕСКОГО раздела («151 машина … за смену
        # 22.09.2026»): преамбула без станка может содержать стороннюю дату
        # («в стопе до 15.10.2026») и утянуть смену на неё.
        for s in sections:
            if s.machine and s.date:
                file_date = s.date
                date_source = "текст файла"
                break
    if not file_date:
        file_date = detect_shift_date(text, now) or shift_date_for(now)
        date_source = "текущая смена"

    stamp = now or datetime.now()
    saved: List[Accepted] = []
    machines: List[str] = []
    authors: List[str] = []
    filled = [s for s in sections if s.text.strip()]
    if not filled:
        raise file_text.EmptyFileError(
            f"в файле «{filename}» нет текста про станки")
    for s in filled:
        if s.machine and s.machine not in machines:
            machines.append(s.machine)
        for a in s.authors:
            if a not in authors:
                authors.append(a)

    # Такой файл в эту смену уже принимали — второй копией не множим: ИИ
    # получил бы каждое обсуждение дважды и удвоил бы количества. Разделы при
    # этом перечитываем заново и кладём вместо старых: так починка разбора
    # выгрузки доходит до уже принятых смен без нового запроса к ИИ — мастер
    # переприсылает тот же файл, и накопитель освежается сам.
    fingerprint = _text_fingerprint(text)
    store_name = os.path.basename(filename or "")
    refresh_of = ""
    for doc in accepted_documents(file_date):
        if (doc.get("fingerprint") or "") != fingerprint:
            continue
        refresh_of = (os.path.basename(str(doc.get("name") or ""))
                      or store_name)
        break
    mirrors = sum(s.mirrored for s in filled)
    if refresh_of:
        _forget_document(file_date, refresh_of)
        store_name = refresh_of
        # Подпись старого приёма остаётся в смене, вторую ту же не пишем.
        caption = ""

    # Подпись к файлу («вот смена за 10.09», «задание №315 верное») сохраняем
    # ПЕРВЫМ сообщением, до разделов. Если положить её в конец, парсер остатков
    # отнесёт её к станку ПОСЛЕДНЕГО раздела (у сообщения без заголовка станок
    # наследуется от предыдущего — см. build_day_remains, carried).
    caption = (caption or "").strip()
    sections_saved = 0
    if caption:
        add_message(text=caption, photos=[], author=author,
                    date=file_date, now=stamp,
                    thread=f"файл {store_name}, подпись")
    for n, s in enumerate(sections, start=1):
        body = s.text.strip()
        if not body:
            continue
        # Станок секции — ТОЧНЫЙ (из шапки «–N»), поэтому кладём его в
        # machine: он попадёт и в day_machines(), и в текст для ИИ, и в
        # парсер остатков как подсказка.
        acc = add_message(text=body, photos=[], author=author,
                          date=file_date, now=stamp,
                          machine=s.machine,
                          thread=(f"файл {store_name}, раздел {n}"
                                  if s.machine else f"файл {store_name}"))
        saved.append(acc)
        sections_saved += 1

    _remember_document(file_date, store_name, fingerprint, sections_saved,
                       machines, stamp, replace=bool(refresh_of))

    first = saved[0]
    total = load_day(file_date)
    return Accepted(
        date=file_date, index=first.index,
        messages=len(total["messages"]),
        photos=sum(len(m.get("photos") or []) for m in total["messages"]),
        scan=scan_text(text),
        file_name=os.path.basename(filename or ""),
        file_kind=kind,
        file_sections=sections_saved,
        file_machines=machines,
        file_authors=authors,
        file_date=file_date,
        quality_warning=warning,
        date_source=date_source,
        file_duplicate=bool(refresh_of),
        duplicate_of=refresh_of,
        file_mirrors=mirrors,
    )


def list_days() -> List[Tuple[str, int, int, Optional[str]]]:
    """Накопленные смены: (дата, сообщений, фото, когда обновлялась).

    Отсортированы по дате смены, свежие — в конце.
    """
    out = []
    try:
        names = os.listdir(_data_dir)
    except OSError:
        return out
    for name in names:
        if not os.path.isfile(_day_file(name)):
            continue
        data = load_day(name)
        msgs = data["messages"]
        out.append((name, len(msgs),
                    sum(len(m.get("photos") or []) for m in msgs),
                    data.get("updated_at")))

    def _key(row):
        try:
            return datetime.strptime(row[0], DATE_FMT)
        except ValueError:
            return datetime.min
    return sorted(out, key=_key)


def latest_day() -> Optional[str]:
    """Смена, в которую последней добавляли данные (её и просят в отчёте)."""
    days = [d for d in list_days() if d[1]]
    if not days:
        return None
    return max(days, key=lambda row: (row[3] or "", row[0]))[0]


def _photo_meta(msg: Dict) -> List[Dict]:
    """Описания фото сообщения, с поддержкой старых day.json без OCR.

    В сменах, накопленных до появления OCR, поля photos_meta нет. Тогда
    считаем, что фото должно уйти в ИИ картинкой, — как и работало раньше.
    """
    meta = msg.get("photos_meta") or []
    names = msg.get("photos") or []
    if len(meta) == len(names):
        for item, name in zip(meta, names):
            item.setdefault("name", name)
        return meta
    return [{"name": name, "verdict": "unknown", "reason": "",
             "ocr_text": "", "to_prompt": False, "to_vision": True}
            for name in names]


# ── Расклейка слипшихся строк (прогон 10.09.2026) ──
#
# Выгрузка чата из Яндекс Мессенджера/Telegram часто теряет переводы строк, и
# сообщения мастеров слипаются в одну строку:
#
#     «…ср сч 57688:18 обрыв устранен силами оператора, машина в работе8:23
#       стоп машина, несоответствие в полотне…»
#
# Здесь три разных факта: «ср сч 5768», стоп-время «8:18», и «машина в работе»
# со СЛЕДУЮЩИМ стопом в «8:23». Модель по такому тексту теряла почти все
# остановки (по ст.151 в отчёт попало два останова из восьми, а 1,98 ч повисли
# как «время не указано»).
#
# Разбиение делаем КОДОМ, а не просим об этом модель: правила простые,
# проверяемые и не зависят от внимания модели.
#
# 1) буква вплотную перед временем  →  «в работе8:23» = «в работе» + «8:23»;
# 2) длинный ряд цифр перед «:ММ», где хвост — час смены (7…19)
#                                     →  «57688:18» = «5768» + «8:18»,
#                                        «651112:45» = «6511» + «12:45»;
# 3) строчная буква вплотную перед заглавной → граница предложений
#                                     →  «полотнаСч 753952» = «полотна» + «Сч 753952».
#
# Час смены ограничен 7…19 (смена 07:00–19:00, плюс ночные сообщения до 06:00),
# поэтому «57688:18» не может быть числом 57688 с мусором «:18» — это время.

# Время в любом из форматов мастера: «8:18», «08.18», «8;18», «10-45».
_GLUED_TIME_RE = re.compile(
    r"(?:(?<=\D)|^)(\d{1,2})\s*[:.;\-]\s*(\d{2})(?!\d)")
# Цифры вплотную перед «:ММ» (случай 2).
_GLUED_DIGITS_RE = re.compile(r"(\d{3,})([:.;])(\d{2})(?!\d)")
# строчная → заглавная (кириллица и латиница).
_GLUED_CASE_RE = re.compile(r"([a-zа-яё])([A-ZА-ЯЁЁ])")

_SHIFT_HOURS = set(range(6, 20))     # 06:00…19:00 — реальные часы смены


def split_glued_text(text: str) -> str:
    """Вставить переводы строк там, где выгрузка чата их потеряла.

    Ничего не переписывает и не выдумывает: только добавляет «\n» в местах,
    которые однозначно являются границей записей. Уже разбитый текст остаётся
    как есть (функция идемпотентна), поэтому её можно звать на каждом прогоне.
    """
    if not text:
        return text or ""
    out = text

    # 2) «57688:18» → «5768» + новая строка «8:18».
    # Проверяем хвост цифр: час берём ДВУЗНАЧНЫМ, если он в пределах смены,
    # иначе ОДНОЗНАЧНЫМ («88» — не час, «8» — час).
    def _split_digits(m: "re.Match") -> str:
        digits, sep, mm = m.group(1), m.group(2), m.group(3)
        for take in (2, 1):
            if len(digits) > take:
                hh = digits[-take:]
                if hh.isdigit() and int(hh) in _SHIFT_HOURS and int(mm) < 60:
                    return digits[:-take] + "\n" + hh + sep + mm
        return m.group(0)

    out = _GLUED_DIGITS_RE.sub(_split_digits, out)
    # 1) буква вплотную перед временем → граница записи.
    out = re.sub(r"([A-Za-zА-Яа-яЁё\.\,\)])(\d{1,2}[:.;]\d{2})(?!\d)",
                 r"\1\n\2", out)
    # 3) «полотнаСч» → «полотна» + «Сч».
    out = _GLUED_CASE_RE.sub(r"\1\n\2", out)
    # 4) цифра вплотную перед заглавной буквой → «ср сч 6022Причина обрыва»
    # = «ср сч 6022» + «Причина обрыва». Латиницу не трогаем в середине
    # ассортимента («187.3 -28 250.5 зел»), только начало слова.
    out = re.sub(r"(\d)([А-ЯЁ][а-яё])", r"\1\n\2", out)
    return out


def day_text(date: str) -> str:
    """Вся переписка смены одним текстом — вход для единственного запроса к ИИ."""
    data = load_day(date)
    parts = [f"Дата смены: {date}. Смена 07:00–19:00."]
    for m in data["messages"]:
        head = f"--- сообщение {m['index']}"
        if m.get("author"):
            head += f", прислал {m['author']}"
        if m.get("machine"):
            # Номер из заголовка обсуждения — надёжнее, чем найденный в тексте.
            head += (f", обсуждение по станку №{m['machine']} "
                     f"(номер из заголовка темы, не из текста)")
        if m.get("photos"):
            head += f", приложено фото: {len(m['photos'])}"
        parts.append(head + " ---")
        if m.get("text"):
            # Расклеиваем слипшиеся строки: иначе модель теряет остановки и
            # показания (см. split_glued_text).
            parts.append(split_glued_text(m["text"]))
        ocr_parts = [item for item in _photo_meta(m)
                     if item.get("to_prompt") and item.get("ocr_text")]
        for item in ocr_parts:
            # Честно помечаем, откуда взялся текст: Tesseract на снимке
            # журнала может перепутать цифру, и модель должна об этом знать —
            # лучше пометка «проверьте», чем уверенное неверное показание.
            parts.append("[текст распознан с фотографии автоматически (OCR), "
                         "возможны ошибки в цифрах — если расходится с "
                         "остальными сообщениями, доверяйте тексту сообщений]")
            parts.append(item["ocr_text"])
        if not m.get("text") and not ocr_parts and m.get("photos"):
            parts.append("(только фотография, текст на изображении)")
    return "\n".join(parts)


def day_photos(date: str, limit: int = 12) -> List[Tuple[str, str]]:
    """Фото, которые нужно отправить в ИИ картинкой, как (media_type, base64).

    По умолчанию таких почти нет: с фото сначала снимается текст бесплатным
    OCR, и в запрос уходит он, а не изображение (так просил пользователь —
    картинки в запросе дороже). Картинка отправляется только когда прочитать
    её иначе нельзя: OCR не установлен, не справился, или в `.env` включено
    OCR_SEND_PHOTOS_TO_AI=true.
    """
    data = load_day(date)
    photo_dir = os.path.join(_day_dir(date), "photos")
    out: List[Tuple[str, str]] = []
    for m in data["messages"]:
        for item in _photo_meta(m):
            if not item.get("to_vision"):
                continue
            if len(out) >= limit:
                logger.warning("Фото смены %s больше %d — остальные не "
                               "отправляю в ИИ", date, limit)
                return out
            path = os.path.join(photo_dir, item["name"])
            try:
                with open(path, "rb") as f:
                    out.append(("image/jpeg",
                                base64.b64encode(f.read()).decode("ascii")))
            except OSError as e:
                logger.warning("Не удалось прочитать фото %s: %s", path, e)
    return out


def day_foreign_photos(date: str) -> List[Tuple[int, str, str]]:
    """Фото, чей текст к работе не относится: (сообщение, файл, текст).

    Такой текст в отчёт не идёт, но и молча не пропадает: бот показывает его
    отдельным списком. Если OCR ошибся и там всё-таки данные смены, это видно
    сразу — файл лежит на диске, текст перед глазами.
    """
    out: List[Tuple[int, str, str]] = []
    for m in load_day(date)["messages"]:
        for item in _photo_meta(m):
            if item.get("verdict") == "foreign":
                out.append((m["index"], item.get("name") or "",
                            (item.get("ocr_text") or "").strip()))
    return out


def day_stats(date: str) -> Tuple[int, int]:
    """(сообщений, фото) за смену."""
    msgs = load_day(date)["messages"]
    return len(msgs), sum(len(m.get("photos") or []) for m in msgs)


def day_topic_tags(date: str) -> Optional[List[str]]:
    """Станки из точных заголовков тем. None — если хоть одно сообщение без тега.

    Нужен защите от станка-призрака (`day_pipeline._drop_invented_machines`):
    выбрасывать станок из разбора ИИ можно, только если источник знал станок
    КАЖДОГО сообщения смены. Хотя бы одно сообщение без заголовка темы — и в
    нём вполне может быть настоящая «98 машина», которой среди тегов нет:
    уберём верный станок вместе с выдуманным.
    """
    tags: List[str] = []
    for m in load_day(date)["messages"]:
        machine = str(m.get("machine") or "").strip()
        if not machine:
            return None
        if machine not in tags:
            tags.append(machine)
    return tags or None


def day_machines(date: str) -> List[str]:
    """Станки в накопленной переписке: сначала точные, потом найденные в тексте.

    «Точные» — это номер из заголовка обсуждения (Мессенджер). Если источник
    его дал, догадка по тексту больше НЕ нужна: она не «не мешает», она вредит,
    потому что список уходит в подсказку ИИ («по каким станкам собирать
    отчёт»). Прогон 26.09.2026: в теме 251-й мастер пишет размер полотна
    «Днпа 187х6-100,0-151,5» и «помощь Петровского О на 151м», догадка по
    тексту родила станок №151, которого в выгрузке нет, и модель собрала его из
    чужих сообщений — с оператором, часами и чужими счётчиками.
    """
    found: List[str] = []
    msgs = load_day(date)["messages"]
    for m in msgs:
        machine = m.get("machine")
        if machine and machine not in found:
            found.append(machine)
    for m in msgs:
        if str(m.get("machine") or "").strip():
            continue
        for machine in scan_text(m.get("text") or "").machines:
            if machine not in found:
                found.append(machine)
    return found


def clear_day(date: str) -> int:
    """Удалить накопленное за смену. Возвращает число удалённых сообщений."""
    count = len(load_day(date)["messages"])
    day_dir = _day_dir(date)
    photo_dir = os.path.join(day_dir, "photos")
    for path in (_day_file(date),):
        try:
            os.remove(path)
        except OSError:
            pass
    if os.path.isdir(photo_dir):
        for name in os.listdir(photo_dir):
            try:
                os.remove(os.path.join(photo_dir, name))
            except OSError:
                pass
        try:
            os.rmdir(photo_dir)
        except OSError:
            pass
    try:
        os.rmdir(day_dir)
    except OSError:
        pass
    return count
