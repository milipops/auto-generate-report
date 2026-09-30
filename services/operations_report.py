"""Подсчёт длительности операций в часах по каждому станку.

Идея: механики пишут сообщения со временем — либо интервалом
(«09:00-12:00 навивка»), либо просто отметкой времени («11:00 пуск»,
«13:00 остановлена»). Длительность операции считается:
  * если указан интервал — как (конец − начало);
  * если указано только время — операция длится до следующей отметки
    времени (состояние машины держится, пока не придёт новое сообщение).

Каждая операция классифицируется в одну из групп:
  • работа оператора        (подмена, навивка, контроль, пуск, работа)
  • работа привлекаемого персонала (наладчик и подмены при отсутствии оператора)
  • простой машины          (простой, стоп, наладка-простой, отсутствие оператора)
  • перерыв                 (обед) — в итоги не входит

Норма смены: машина 12 ч (07:00–19:00), оператор 11,25 ч.
Модуль не зависит от источника текста — позже его можно кормить
выгрузкой из Яндекс API. Основа для будущей месячной статистики по станку.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from .daily_report import (
    split_machine_blocks,
    DATE_RE,
    TIME_RANGE_RE,
    TIME_RE,
    _SKIP_RE,
)

# Длины смены берёт ОДИН код по всему проекту (services.shift_report): здесь они
# только для печатных знаменателей «из 12 ч» / «из 11,25 ч». Свои копии этих
# чисел разъезжаются с правилом потолка часов оператора (25.09.2026).
from .shift_report import MACHINE_SHIFT_HOURS, OPERATOR_SHIFT_HOURS

SHIFT_START_MIN = 7 * 60      # 07:00
SHIFT_END_MIN = 19 * 60       # 19:00

CAT_OPERATOR = "operator"
CAT_PERSONNEL = "personnel"
CAT_DOWNTIME = "downtime"
CAT_BREAK = "break"

# Явно указанные часы без времени: «2 ч», «6,6 часа», «7,25ч»
EXPLICIT_HOURS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:ч(?:ас\w*)?)\b", re.I)
NAME_RE = re.compile(r"\b([А-ЯЁ][а-яё]{3,})\b")
_NAME_STOP = {
    "Машина", "Оператор", "Смена", "Общий", "Срез", "Срезовой", "Подмена",
    "Навивка", "Навито", "Простой", "Работа", "Замена", "Начало", "Конец",
    "Переход", "Информация", "Стоп", "Пуск", "Обед",
}


@dataclass
class Operation:
    hours: float
    category: str
    label: str          # короткое имя операции (для группировки/сумм)
    text: str           # исходный текст
    performer: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None


@dataclass
class OperationsReport:
    machine: Optional[str] = None
    date: Optional[str] = None
    operator_absent: bool = False
    operations: List[Operation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def _sum(self, category: str) -> float:
        return round(sum(o.hours for o in self.operations if o.category == category), 2)

    @property
    def operator_total(self) -> float:
        return self._sum(CAT_OPERATOR)

    @property
    def personnel_total(self) -> float:
        return self._sum(CAT_PERSONNEL)

    @property
    def downtime_total(self) -> float:
        return self._sum(CAT_DOWNTIME)

    @property
    def machine_work(self) -> float:
        return round(max(0.0, MACHINE_SHIFT_HOURS - self.downtime_total), 2)


def _fmt_hours(value: float) -> str:
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") or "0"


def _to_min(hh: int, mm: int) -> int:
    return hh * 60 + mm


def _label_for(lower: str, category: str, performer: Optional[str] = None) -> str:
    if category == CAT_BREAK:
        return "перерыв (обед)"
    if category == CAT_DOWNTIME:
        if "отсутствие оператор" in lower:
            return "отсутствие оператора"
        if "протяжк" in lower:
            return "простой (протяжка узлов)"
        if "налад" in lower or "чинк" in lower:
            return "простой (наладка)"
        if "подмен" in lower:
            return "простой (подмена шпулей)"
        return "простой"
    if category == CAT_PERSONNEL:
        if "налад" in lower or "чинк" in lower:
            base = "работа наладчика"
        elif "подмен" in lower:
            base = "подмена шпулей (персонал)"
        else:
            base = "работа привлекаемого персонала"
        return f"{base} ({performer})" if performer else base
    # работа оператора
    if "подмен" in lower:
        return "подмена шпулей"
    if "навив" in lower or "навито" in lower:
        return "навивка шпулей"
    if "контрол" in lower:
        return "контроль за работой машины"
    if "пуск" in lower:
        return "работа (пуск)"
    return "работа по контролю за работой машины"


def _classify(lower: str, operator_absent: bool, performer: Optional[str]) -> str:
    """Определить группу операции по её тексту."""
    if any(k in lower for k in ("отсутствие оператор", "оператор отсут")):
        return CAT_DOWNTIME
    if re.search(r"\bпростой", lower) or re.search(r"\bстоп", lower) \
            or re.search(r"\bостанов", lower) or "протяжк" in lower:
        return CAT_DOWNTIME
    if "обед" in lower or "перерыв" in lower:
        return CAT_BREAK
    if "наладчик" in lower or "привлек" in lower or "наладка" in lower or "чинк" in lower:
        return CAT_PERSONNEL
    # Подмена/навивка/работа: если оператор отсутствует и её делает
    # названный человек — это привлекаемый персонал, иначе работа оператора.
    if operator_absent and performer:
        return CAT_PERSONNEL
    return CAT_OPERATOR


def _performer(text: str) -> Optional[str]:
    for m in NAME_RE.finditer(text):
        name = m.group(1)
        if name not in _NAME_STOP:
            return name
    return None


def _extract_time_min(line: str) -> Tuple[Optional[int], Optional[int]]:
    clean = DATE_RE.sub(" ", line)
    rm = TIME_RANGE_RE.search(clean)
    if rm:
        return (_to_min(int(rm.group(1)), int(rm.group(2))),
                _to_min(int(rm.group(3)), int(rm.group(4))))
    sm = TIME_RE.search(clean)
    if sm:
        return _to_min(int(sm.group(1)), int(sm.group(2))), None
    return None, None


def _min_to_hhmm(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return f"{value // 60:02d}:{value % 60:02d}"


def _is_boundary(lower: str) -> bool:
    return any(k in lower for k in (
        "конец смены", "конца смены", "конец работы",
        "начало смены", "начало работы",
    ))


def aggregate_operations(machine: Optional[str], lines: List[str],
                         date: Optional[str] = None) -> OperationsReport:
    report = OperationsReport(machine=machine, date=date)
    block_lower = "\n".join(lines).lower()
    report.operator_absent = ("отсутствие оператор" in block_lower
                              or "оператор отсут" in block_lower)

    # 1. Собираем отметки со временем (для расчёта интервалов).
    timed = []          # (start_min, end_explicit, text, lower, performer, boundary)
    unscheduled = []    # операции с явными часами без времени
    for raw in lines:
        line = raw.strip()
        if not line or _SKIP_RE.search(line):
            continue
        lower = line.lower()
        start_min, end_min = _extract_time_min(line)
        performer = _performer(line)
        if start_min is not None:
            timed.append([start_min, end_min, line, lower, performer,
                          _is_boundary(lower)])
            continue
        hm = EXPLICIT_HOURS_RE.search(line)
        if hm and not _is_boundary(lower):
            hours = float(hm.group(1).replace(",", "."))
            cat = _classify(lower, report.operator_absent, performer)
            unscheduled.append(Operation(hours, cat,
                                         _label_for(lower, cat, performer),
                                         line, performer))

    # 2. Интервалы: событие длится до следующей отметки времени.
    timed.sort(key=lambda e: e[0])
    for i, ev in enumerate(timed):
        start_min, end_explicit, text, lower, performer, boundary = ev
        if end_explicit is not None and end_explicit >= start_min:
            end_min = end_explicit
        elif i + 1 < len(timed):
            end_min = timed[i + 1][0]
        else:
            end_min = SHIFT_END_MIN
        if boundary:
            continue  # маркер начала/конца смены: границу держит, но не операция
        hours = max(0.0, (end_min - start_min) / 60.0)
        if hours <= 0:
            continue
        cat = _classify(lower, report.operator_absent, performer)
        report.operations.append(Operation(
            round(hours, 2), cat, _label_for(lower, cat, performer),
            text, performer,
            _min_to_hhmm(start_min), _min_to_hhmm(end_min),
        ))

    report.operations.extend(unscheduled)
    _ops_warnings(report)
    return report


def _ops_warnings(report: OperationsReport) -> None:
    if not report.operations:
        report.warnings.append(
            "Не удалось вычислить часы: в сообщениях нет времени операций"
        )
    if report.downtime_total > MACHINE_SHIFT_HOURS:
        report.warnings.append(
            "Суммарный простой превышает 12 ч — проверьте отметки времени"
        )


def build_operations_reports(text: str) -> List[OperationsReport]:
    reports = []
    for block in split_machine_blocks(text):
        if not any(l.strip() for l in block["lines"]):
            continue
        dm = DATE_RE.search("\n".join(block["lines"]))
        date = None
        if dm:
            year = dm.group(3)
            year = "20" + year if len(year) == 2 else year
            date = f"{int(dm.group(1)):02d}.{int(dm.group(2)):02d}.{year}"
        reports.append(aggregate_operations(block["machine"], block["lines"], date))
    return reports


def _hhmm_to_min(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    m = re.match(r"\s*(\d{1,2})[:.;](\d{2})", value)
    if not m:
        return None
    return _to_min(int(m.group(1)), int(m.group(2)))


def aggregate_operations_structured(machine: dict,
                                    date: Optional[str] = None) -> OperationsReport:
    """Посчитать часы из СТРУКТУРЫ, разобранной ИИ (см. services.ai_extract).

    Категории и подписи берём от ИИ, а длительность считаем детерминированно:
    интервал (start–end) либо от отметки до следующего события. Так арифметика
    остаётся точной и не зависит от «фантазии» модели.
    """
    report = OperationsReport(machine=machine.get("machine"), date=date)
    report.operator_absent = bool(machine.get("operator_absent"))

    events = []
    for ev in machine.get("events", []):
        start = _hhmm_to_min(ev.get("start"))
        if start is None:
            continue  # без времени длительность не посчитать
        events.append((start, _hhmm_to_min(ev.get("end")), ev))
    events.sort(key=lambda e: e[0])

    for i, (start_min, end_explicit, ev) in enumerate(events):
        if end_explicit is not None and end_explicit >= start_min:
            end_min = end_explicit
        elif i + 1 < len(events):
            end_min = events[i + 1][0]
        else:
            end_min = SHIFT_END_MIN
        hours = max(0.0, (end_min - start_min) / 60.0)
        if hours <= 0:
            continue
        cat = ev.get("category") or CAT_OPERATOR
        performer = ev.get("performer")
        label = ev.get("label") or _label_for(
            (ev.get("text") or "").lower(), cat, performer)
        report.operations.append(Operation(
            round(hours, 2), cat, label, ev.get("text") or "",
            performer, _min_to_hhmm(start_min), _min_to_hhmm(end_min),
        ))

    _ops_warnings(report)
    return report


def build_operations_reports_from_structured(data: dict) -> List[OperationsReport]:
    """Список отчётов по часам из структуры ИИ ({date, machines:[...]})."""
    date = data.get("date")
    return [aggregate_operations_structured(m, date)
            for m in data.get("machines", [])]



def _grouped(ops: List[Operation], category: str):
    """Сгруппировать операции категории по названию, суммируя часы."""
    order: List[str] = []
    sums: dict = {}
    for o in ops:
        if o.category != category:
            continue
        if o.label not in sums:
            sums[o.label] = 0.0
            order.append(o.label)
        sums[o.label] += o.hours
    return [(label, round(sums[label], 2)) for label in order]


def format_operations_report(r: OperationsReport) -> str:
    out: List[str] = []
    head = f"⏱ Часы по станку №{r.machine or '—'}"
    if r.date:
        head += f" — {r.date}"
    out.append(head)
    out.append("")

    out.append(
        f"👷 Работа оператора всего: {_fmt_hours(r.operator_total)} "
        f"из {_fmt_hours(OPERATOR_SHIFT_HOURS)} ч"
        + ("  (отсутствие оператора)" if r.operator_absent else "")
    )
    for label, hours in _grouped(r.operations, CAT_OPERATOR):
        out.append(f"   • {_fmt_hours(hours)} ч — {label}")

    personnel = _grouped(r.operations, CAT_PERSONNEL)
    if personnel:
        out.append("")
        out.append(f"🛠 Работа привлекаемого персонала всего: "
                   f"{_fmt_hours(r.personnel_total)} ч")
        for label, hours in personnel:
            out.append(f"   • {_fmt_hours(hours)} ч — {label}")

    out.append("")
    out.append(f"⚙️ Работа машины всего: {_fmt_hours(r.machine_work)} "
               f"из {_fmt_hours(MACHINE_SHIFT_HOURS)} ч")
    downtime = _grouped(r.operations, CAT_DOWNTIME)
    if downtime:
        out.append("   Простои:")
        for label, hours in downtime:
            out.append(f"   • {_fmt_hours(hours)} ч — {label}")

    breaks = _grouped(r.operations, CAT_BREAK)
    if breaks:
        total_break = round(sum(h for _, h in breaks), 2)
        out.append("")
        out.append(f"⏸ Перерывы: {_fmt_hours(total_break)} ч")

    if r.warnings:
        out.append("")
        out.append("⚠️ Предупреждения:")
        for w in r.warnings:
            out.append(f"   • {w}")
    return "\n".join(out)
