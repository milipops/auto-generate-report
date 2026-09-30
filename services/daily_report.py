"""Суточная агрегация сообщений мастеров в единый отчёт по станку.

На вход подаётся весь текст обсуждения за смену (много сообщений от мастеров).
На выходе — один структурированный отчёт по каждому станку: счётчики,
дроби (срез/подмена/навито), хронология событий с временем, операторы,
предупреждения. Смена по умолчанию 07:00–19:00.

Позже сюда же можно будет подавать текст, автоматически выгруженный из
Яндекс Мессенджера/360 — интерфейс функций остаётся тем же.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, List

DEFAULT_SHIFT_START = "07:00"
DEFAULT_SHIFT_END = "19:00"

# --- Регулярные выражения ---------------------------------------------------
DATE_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})")
TIME_RANGE_RE = re.compile(
    r"(\d{1,2})[:.;](\d{2})\s*[-–—]\s*(\d{1,2})[:.;](\d{2})"
)
TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[:.;](\d{2})(?!\d)")

MACHINE_HEADER_RE = re.compile(
    r"^\s*(?:[–—-]\s*|√\s*|№\s*|#\s*)?(\d{2,4})\s*(?:машина|станок|маш\.)",
    re.I,
)
MACHINE_DASH_RE = re.compile(r"^\s*(?:[–—-]|√)\s*(\d{2,4})\b")
# Голый номер после тире — заголовок станка ТОЛЬКО если на строке больше
# ничего нет, кроме самого номера, слова «машина» и, возможно, фамилии в
# скобках. Иначе «-12 шт. годные в работу, навитые во время наладки»
# (15.09.2026, тема 251-й) превращалось в несуществующий станок №12, который
# утаскивал у настоящей 251-й весь вечерний блок счётчиков.
_MACHINE_LINE_TAIL_RE = re.compile(
    r"^(?:\s*(?:машина|станок|маш\.?))?\s*[.,;:]?\s*"
    r"(?:\(?[А-ЯЁ][а-яё]+\)?)?\s*[.,;:]?\s*$",
    re.I,
)

# «Общий счётчик 223351», «Общ.сч. -223351» и «Об. Сч.: 790782» — одна и та же
# клетка бланка: мастер пишет метку сокращённо, и между словами стоит точка.
# Без этого резервная сводка (без ИИ) оставалась и без счётчика, и с
# предупреждением «Счётчики за смену не распознаны» — на 151-й за 24.09.2026.
# Огляду назад нужен, иначе «об» находится внутри чужих слов.
#
# После метки стоят БУКВЫ, а не «любые символы слова»: «\w» захватывает и
# цифры, и на «Общ сч21431800» (14.09.2026, ст.98 — мастер не поставил пробел)
# из восьмизначного счётчика оставалось «800».
# Подчёркивание — такой же разделитель клетки бланка, как пробел: вечерний
# итог мастер пишет «Общ.сч. -_217622_» (21.09.2026, ст.111), и без «_»
# резервная сводка видела только дневное показание 216460.
GENERAL_COUNTER_RE = re.compile(
    r"(?<![а-яё])общ?[а-яё]*\.?\s*сч[а-яё]*[\s:№–—._-]*(\d{3,})", re.I | re.S)
# …и это НЕ показание, если сразу за числом идёт «из»: у накопительного
# одометра плана не бывает, дробь под его меткой — соседний счётчик (21.09.2026,
# ст.98: «Со.сч.1000 из 1450», где 1450 — план срезового). То же правило в
# листе «Остатки» — см. remains_report._BARE_FRACTION_VALUE_RE.
_GENERAL_PLAN_TAIL_RE = re.compile(r"[\s_]*[\d\s.,_]*из(?![а-яёa-z])", re.I)
# Метка пишется и «срезовой счётчик» (25.08.2026, ст.321), и коротко
# «Срез. Сч 381 из 4» (26.08.2026, ст.321) — «о» и «в» здесь необязательны.
SLICE_COUNTER_RE = re.compile(
    r"(?<![а-яё])срезо?в?[а-яё]*\.?\s*сч[а-яё]*[\s:№–—._-]*(\d+)"
    r"[\s_]*(?:из|/)[\s_]*(\d+)",
    re.I | re.S)
# Пустые клетки бланка мастер заполняет подчёркиваниями («навито - _0____ из
# _759___»), поэтому «_» здесь такой же разделитель, как пробел или тире.
LABEL_FRACTION_RE = re.compile(
    r"(?P<label>шпул\w*(?:\s*навито)?|навито|срез|подмен\w*|привязано|узл\w*)"
    r"\s*[-–—:]*[\s_]*(?P<cur>\d+)[\s_]*(?:из|/)[\s_]*(?P<tot>\d+)",
    re.I,
)

_CATEGORY_RULES = [
    ("конец смены", ("конец смены", "конца смены", "конец работы")),
    ("начало смены", ("начало смены", "начало работы")),
    ("перерыв", ("обед", "перерыв")),
    ("простой", ("стоп", "останов", "простой")),
    ("пуск", ("пуск", "пущен", "запущ")),
    ("подмена", ("подмен",)),
    ("переналадка", ("чинк", "переведен", "снят", "переход",
                     "замена шпуляр", "перемотк", "наладоч", "дозаправ")),
    ("навивка", ("навив", "навито", "довива", "шпул", "бобин", "бабин")),
]
_SKIP_RE = re.compile(
    r"прош[ую]|пожалуйста|указывайте|в формате|^принято|спасибо|благодар", re.I
)

OPERATOR_RE = re.compile(r"оператор\w*\s+([А-ЯЁ][а-яё]+)", re.I)
OPERATOR_HANDOVER_RE = re.compile(r"оператору\s+([А-ЯЁ][а-яё]+)", re.I)


# --- Структуры данных -------------------------------------------------------
@dataclass
class DailyEvent:
    start: Optional[str]
    end: Optional[str]
    category: str
    text: str


@dataclass
class DailyReport:
    machine: Optional[str] = None
    date: Optional[str] = None
    shift_start: str = DEFAULT_SHIFT_START
    shift_end: str = DEFAULT_SHIFT_END
    operators: List[str] = field(default_factory=list)
    general_counter: Optional[int] = None
    slice_counter: Optional[Tuple[int, int]] = None
    fractions: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    events: List[DailyEvent] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    message_lines: int = 0


# --- Вспомогательные функции ------------------------------------------------
def _extract_time(line: str) -> Tuple[Optional[str], Optional[str]]:
    """Вернуть (начало, конец) времени из строки. Даты игнорируются."""
    clean = DATE_RE.sub(" ", line)
    m = TIME_RANGE_RE.search(clean)
    if m:
        start = f"{int(m.group(1)):02d}:{m.group(2)}"
        end = f"{int(m.group(3)):02d}:{m.group(4)}"
        return start, end
    m = TIME_RE.search(clean)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}", None
    return None, None


def _classify(lower: str) -> Optional[str]:
    for cat, kws in _CATEGORY_RULES:
        for k in kws:
            # \b перед корнем: "пуск" не сработает внутри "пропуска",
            # но "останов" по-прежнему поймает "остановлена".
            if re.search(r"\b" + k, lower):
                return cat
    return None


def _strip_leading_time(text: str) -> str:
    """Убрать время в начале строки — оно уже показано отдельной колонкой."""
    m = re.match(
        r"\s*\d{1,2}[:.;]\d{2}(?:\s*[-–—]\s*\d{1,2}[:.;]\d{2})?\s*[,-]?\s*",
        text,
    )
    if m:
        rest = text[m.end():].strip(" .,-–—")
        if rest:
            return rest
    return text


def _is_fraction_line(line: str) -> bool:
    if LABEL_FRACTION_RE.search(line):
        return True
    return bool(re.search(r"сч\w*[\s:№–—-]*\d{3,}", line, re.I))


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip(" .,-–—")


def _add_operator(report: DailyReport, name: str) -> None:
    name = name.strip()
    if name and name not in report.operators:
        report.operators.append(name)


def split_machine_blocks(text: str) -> List[dict]:
    """Разбить переписку на блоки по станкам (по строкам-заголовкам)."""
    blocks: List[dict] = []
    current: Optional[dict] = None
    for line in text.splitlines():
        m = MACHINE_HEADER_RE.match(line)
        if m is None:
            dash = MACHINE_DASH_RE.match(line)
            if dash and _MACHINE_LINE_TAIL_RE.match(line[dash.end():] or ""):
                m = dash
        if m:
            if current:
                blocks.append(current)
            current = {"machine": m.group(1), "lines": [line]}
        else:
            if current is None:
                current = {"machine": None, "lines": [line]}
            current["lines"].append(line)
    if current:
        blocks.append(current)
    return blocks


def aggregate_block(machine: Optional[str], lines: List[str]) -> DailyReport:
    """Собрать один суточный отчёт из строк одного станка."""
    report = DailyReport(machine=machine)
    block_text = "\n".join(lines)

    # Дата смены
    dm = DATE_RE.search(block_text)
    if dm:
        year = dm.group(3)
        if len(year) == 2:
            year = "20" + year
        report.date = f"{int(dm.group(1)):02d}.{int(dm.group(2)):02d}.{year}"

    # Счётчики (по всему блоку, т.к. значение может быть на следующей строке).
    # Берём ПОСЛЕДНЕЕ совпадение: итог смены главнее приёмки — утреннюю клетку
    # мастер перечитывает вечером, и лист «Остатки» делает ровно так же.
    gm = [m for m in GENERAL_COUNTER_RE.finditer(block_text)
          if not _GENERAL_PLAN_TAIL_RE.match(
              block_text[m.end():m.end() + 16] or "")]
    if gm:
        report.general_counter = int(gm[-1].group(1))
    sm = SLICE_COUNTER_RE.findall(block_text)
    if sm:
        report.slice_counter = (int(sm[-1][0]), int(sm[-1][1]))

    # Дроби (срез/подмена/навито и т.п.)
    for fm in LABEL_FRACTION_RE.finditer(block_text):
        label = re.sub(r"\s+", " ", fm.group("label").strip().lower())
        report.fractions[label] = (int(fm.group("cur")), int(fm.group("tot")))

    # Операторы
    for name in OPERATOR_RE.findall(block_text):
        _add_operator(report, name)
    for name in OPERATOR_HANDOVER_RE.findall(block_text):
        _add_operator(report, name)
    if lines:
        hm = re.search(r"\(([А-ЯЁ][а-яё]+)", lines[0])
        if hm:
            _add_operator(report, hm.group(1))

    # События и предупреждения
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        report.message_lines += 1
        lower = line.lower()
        if _SKIP_RE.search(line):
            continue
        if any(w in lower for w in ("нет данных", "пропуск", "отсутств")):
            report.warnings.append(_clean(line))
        start, end = _extract_time(line)
        category = _classify(lower)
        if start is None and end is None:
            if category is None or _is_fraction_line(line):
                continue
        report.events.append(
            DailyEvent(start, end, category or "событие", _strip_leading_time(_clean(line)))
        )

    report.events.sort(key=lambda e: (e.start is None, e.start or ""))
    _finalize_warnings(report, block_text.lower())
    return report


def _finalize_warnings(report: DailyReport, lower: str) -> None:
    if report.machine is None:
        report.warnings.append("Номер станка не определён")
    if report.general_counter is None and report.slice_counter is None:
        report.warnings.append(
            "Счётчики за смену не распознаны — проверьте вручную"
        )


def build_daily_reports(text: str,
                        machine_hint: Optional[str] = None) -> List[DailyReport]:
    """Главная функция: весь текст переписки -> список отчётов по станкам.

    machine_hint — номер станка, известный вне текста (тема обсуждения в
    выгрузке мессенджера). Заголовок «–111 машина» мастер пишет не в каждой
    строке, и без подсказки блок оставался «станок не определён». Нашёл
    номер сам в тексте — приоритет у текста.
    """
    # Разметка мессенджера снимается тем же каналом нормализации, что и у
    # листа «Остатки»: жирный бланк («**Общ.сч**. -223351______») маски не
    # узнаёт, и станок оставался совсем без показаний.
    # См. remains_report.normalize_text.
    from .remains_report import normalize_text

    reports = []
    for block in split_machine_blocks(normalize_text(text)):
        if not any(l.strip() for l in block["lines"]):
            continue
        reports.append(aggregate_block(block["machine"] or machine_hint,
                                       block["lines"]))
    return reports


def build_daily_reports_from_structured(data: dict) -> List[DailyReport]:
    """Суточные отчёты из структуры, разобранной ИИ ({date, machines:[...]}).

    ИИ уже отделил станки и выкинул мусор чата, поэтому здесь только
    перекладываем счётчики, операторов и события в DailyReport.
    """
    date = data.get("date")
    reports: List[DailyReport] = []
    for m in data.get("machines", []):
        report = DailyReport(machine=m.get("machine"), date=date)
        if m.get("operator"):
            _add_operator(report, m["operator"])
        for c in m.get("counters", []):
            name = (c.get("name") or "").lower()
            value, target = c.get("value"), c.get("target")
            if value is None:
                continue
            if "общ" in name and report.general_counter is None:
                report.general_counter = int(value)
            elif "срез" in name and report.slice_counter is None and target:
                report.slice_counter = (int(value), int(target))
            elif target:
                report.fractions[name or "счётчик"] = (int(value), int(target))
        for ev in m.get("events", []):
            report.events.append(DailyEvent(
                ev.get("start"), ev.get("end"),
                ev.get("label") or ev.get("category") or "событие",
                ev.get("text") or "",
            ))
        report.message_lines = len(m.get("events", []))
        report.events.sort(key=lambda e: (e.start is None, e.start or ""))
        if report.machine is None:
            report.warnings.append("Номер станка не определён")
        reports.append(report)
    return reports



def _fmt_time(e: DailyEvent) -> str:
    if e.start and e.end:
        return f"{e.start}–{e.end}"
    if e.start:
        return e.start
    return "--:--"


def format_daily_report(r: DailyReport) -> str:
    """Собрать читаемый текстовый отчёт для отправки в Telegram."""
    out: List[str] = []
    head = f"📋 Отчёт по станку №{r.machine or '—'}"
    if r.date:
        head += f" — смена {r.date}"
    out.append(head)
    out.append(f"🕖 Смена: {r.shift_start}–{r.shift_end} (по умолчанию)")
    if r.operators:
        out.append(f"👷 Операторы: {', '.join(r.operators)}")

    out.append("")
    out.append("📊 Счётчики:")
    if r.general_counter is not None:
        out.append(f"  • Общий счётчик: {r.general_counter}")
    if r.slice_counter:
        out.append(
            f"  • Срезовой счётчик: {r.slice_counter[0]} / {r.slice_counter[1]}"
        )
    for label, (cur, tot) in r.fractions.items():
        out.append(f"  • {label.capitalize()}: {cur} / {tot}")
    if r.general_counter is None and not r.slice_counter and not r.fractions:
        out.append("  • данные по счётчикам не найдены")

    out.append("")
    out.append("🕒 События за смену:")
    if r.events:
        for e in r.events:
            out.append(f"  {_fmt_time(e):<11}  [{e.category}] {e.text}")
    else:
        out.append("  событий не зафиксировано")

    if r.warnings:
        out.append("")
        out.append("⚠️ Предупреждения:")
        for w in r.warnings:
            out.append(f"  • {w}")

    out.append("")
    out.append(f"Обработано строк: {r.message_lines}")
    return "\n".join(out)


