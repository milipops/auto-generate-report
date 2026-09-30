"""Parser for free-form production shift messages."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


MACHINE_RE = re.compile(
    r"(?:^|[–—-]\s*)?(?:станок\s*)?(\d{2,4})(?:\s*станок)?\b",
    re.I | re.M,
)
TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})\s*[:.;]\s*(?P<minute>\d{2})"
)
LOOSE_TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})\s*[-–—]\s*(?P<minute>\d{2})"
)
RANGE_RE = re.compile(
    r"(?P<start>\d{1,2})\s*[:.;]\s*(?P<smin>\d{2})\s*"
    r"(?:[-–—]|\bдо\b)\s*"
    r"(?P<end>\d{1,2})\s*[:.;]\s*(?P<emin>\d{2})"
)
LOOSE_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2})\s*[-–—]\s*(?P<smin>\d{2})\s+до\s+"
    r"(?P<end>\d{1,2})\s*[-–—]\s*(?P<emin>\d{2})",
    re.I,
)
HOUR_ONLY_RE = re.compile(r"(?P<hour>\d{1,2})\s*ч(?:\.|ас(?:а|ов)?)?", re.I)
COUNTER_RE = re.compile(
    r"(?:об\.?\s*)?(?:общ(?:ий|\.)?\s*)?"
    r"сч(?:[её]тчик|\.)?\s*[:№-]?\s*(\d{3,})",
    re.I,
)
SLICE_COUNTER_RE = re.compile(
    r"(?:срезов(?:ой)?|ср\.)\s*сч(?:[её]тчик|\.)?\s*[:№-]?\s*(\d{2,})",
    re.I,
)
FRACTION_RE = re.compile(
    r"(?P<label>срез(?:ов)?|подмен(?:а|ы)?|"
    r"(?:протянуто\s+)?узл(?:ов|а)?|"
    r"шпул(?:я|и)?\s*(?:навито|на)?|"
    r"привязано(?:\s+узл(?:ов)?)?)\s*[:—-]?\s*"
    r"(?P<current>\d+)\s*(?:из|/)\s*(?P<target>\d+)",
    re.I,
)
NUMBERED_COUNT_RE = re.compile(
    r"(?P<label>шпул(?:я|и)?|узл(?:ов|а)?)\s*"
    r"(?P<value>\d+)",
    re.I,
)
NODES_BEFORE_RE = re.compile(r"(?P<value>\d+)\s*узл(?:ов|а)?\b", re.I)
SLICE_COUNT_BEFORE_LABEL_RE = re.compile(
    r"(?P<value>\d+)\s*срез(?:а|ов)?\b",
    re.I,
)
GENERIC_FRACTION_RE = re.compile(r"(?P<current>\d+)\s*(?:из|/)\s*(?P<target>\d+)", re.I)
SPOOLS_TOTAL_RE = re.compile(
    r"шпул(?:я|и)?[^\d]{0,30}(?P<value>\d+)\s*шт\.?",
    re.I,
)
TIED_NODES_RE = re.compile(
    r"привязано\s*(?P<value>\d+)\s*(?:узл(?:ов|а)?\.?)?",
    re.I,
)


@dataclass
class CounterRecord:
    kind: str
    current: Optional[int] = None
    target: Optional[int] = None
    value: Optional[int] = None


@dataclass
class SubstitutionRecord:
    start: Optional[str]
    end: Optional[str]
    operator: str
    quantity: Optional[int] = None


@dataclass
class EventRecord:
    text: str
    start: Optional[str] = None
    end: Optional[str] = None
    category: str = "note"


@dataclass
class ProductionRecord:
    machine: Optional[str] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    operator: Optional[str] = None
    total_counters: list[int] = field(default_factory=list)
    slice_counters: list[int] = field(default_factory=list)
    counters: list[CounterRecord] = field(default_factory=list)
    substitutions: list[SubstitutionRecord] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise_time(hour: str, minute: str) -> str:
    hour_value, minute_value = int(hour), int(minute)
    if not 0 <= hour_value <= 23 or not 0 <= minute_value <= 59:
        raise ValueError("Некорректное время")
    return f"{hour_value:02d}:{minute_value:02d}"


def _extract_time_range(text: str) -> tuple[Optional[str], Optional[str]]:
    match = RANGE_RE.search(text) or LOOSE_RANGE_RE.search(text)
    if not match:
        return None, None
    try:
        return (
            _normalise_time(match["start"], match["smin"]),
            _normalise_time(match["end"], match["emin"]),
        )
    except ValueError:
        return None, None


def _extract_single_time(text: str) -> Optional[str]:
    match = TIME_RE.search(text) or LOOSE_TIME_RE.search(text)
    if match:
        try:
            return _normalise_time(match["hour"], match["minute"])
        except ValueError:
            return None
    hour_match = HOUR_ONLY_RE.search(text)
    if hour_match:
        try:
            return _normalise_time(hour_match["hour"], "00")
        except ValueError:
            return None
    return None


def _category(text: str) -> str:
    lowered = text.lower()
    if "останов" in lowered or "запуск" in lowered:
        return "machine_status"
    if "подмен" in lowered:
        return "substitution"
    if "нет данных" in lowered or "отсутств" in lowered or "пропуск" in lowered:
        return "warning"
    if "конец смены" in lowered:
        return "shift_end"
    if "срез" in lowered or "сч" in lowered:
        return "counter"
    return "note"


def parse_production_message(text: str) -> ProductionRecord:
    """Extract structured fields while retaining every source line as an event."""
    record = ProductionRecord(raw_text=text.strip())
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        record.warnings.append("Пустое сообщение")
        return record

    machine = MACHINE_RE.search(lines[0])
    if machine:
        record.machine = machine.group(1)
    else:
        machine_match = re.search(r"(?:^|[–—-])\s*(\d{2,4})\b", lines[0])
        if machine_match:
            record.machine = machine_match.group(1)

    for line in lines:
        lowered = line.lower()
        start, end = _extract_time_range(line)
        if start is None:
            start = _extract_single_time(line)

        if record.shift_start is None and ("начало" in lowered or "начал" in lowered):
            record.shift_start = start
        if "конец смены" in lowered and record.shift_end is None:
            record.shift_end = start

        slice_values_on_line = set()
        for match in SLICE_COUNTER_RE.finditer(line):
            value = int(match.group(1))
            record.slice_counters.append(value)
            slice_values_on_line.add(value)
        for match in SLICE_COUNT_BEFORE_LABEL_RE.finditer(line):
            value = int(match.group("value"))
            record.slice_counters.append(value)
            slice_values_on_line.add(value)
        for match in COUNTER_RE.finditer(line):
            value = int(match.group(1))
            # Не дублировать срезовой счетчик (напр. "Ср. сч.:1362") как общий.
            if value in slice_values_on_line:
                continue
            record.total_counters.append(value)

        for match in FRACTION_RE.finditer(line):
            label = re.sub(r"\s+", " ", match.group("label").lower()).strip()
            if "срез" in label:
                kind = "slice"
            elif "подмен" in label:
                kind = "substitution"
            elif "шпул" in label:
                kind = "spools"
            elif "привяз" in label:
                kind = "tied_nodes"
            else:
                kind = "other"
            record.counters.append(CounterRecord(kind=kind, current=int(match["current"]), target=int(match["target"])))

        if not FRACTION_RE.search(line):
            for match in GENERIC_FRACTION_RE.finditer(line):
                current, target = int(match["current"]), int(match["target"])
                if "срез" in lowered:
                    kind = "slice"
                elif "шпул" in lowered:
                    kind = "spools"
                elif "узл" in lowered or "привяз" in lowered:
                    kind = "tied_nodes"
                else:
                    kind = "other"
                record.counters.append(CounterRecord(kind=kind, current=current, target=target))

        for match in NUMBERED_COUNT_RE.finditer(line):
            label = match.group("label").lower()
            if "шпул" in label:
                kind = "spools"
            else:
                kind = "tied_nodes"
            record.counters.append(
                CounterRecord(kind=kind, value=int(match.group("value")))
            )

        for match in SPOOLS_TOTAL_RE.finditer(line):
            value = int(match.group("value"))
            if not any(
                item.kind == "spools" and item.value == value
                for item in record.counters
            ):
                record.counters.append(CounterRecord(kind="spools", value=value))

        for match in TIED_NODES_RE.finditer(line):
            value = int(match.group("value"))
            if not any(
                item.kind == "tied_nodes" and item.value == value
                for item in record.counters
            ):
                record.counters.append(CounterRecord(kind="tied_nodes", value=value))
        for match in NODES_BEFORE_RE.finditer(line):
            value = int(match.group("value"))
            if not any(
                item.kind == "tied_nodes" and item.value == value
                for item in record.counters
            ):
                record.counters.append(CounterRecord(kind="tied_nodes", value=value))

        if "оператор" in lowered:
            operator = re.split(r"оператор\s*", line, flags=re.I, maxsplit=1)[-1].strip(" :—-")
            if operator:
                record.operator = operator

        if "подмен" in lowered:
            names_part = re.split(r"подмен[аы]?\s*", line, flags=re.I, maxsplit=1)[-1]
            for name_chunk in re.split(r"[,;]", names_part):
                name_chunk = name_chunk.strip(" .:-")
                if not name_chunk or name_chunk.lower().startswith(("пока", "на ")):
                    continue
                quantity_match = re.search(r"(?P<name>[^-]+?)[-–—]\s*(?P<qty>\d+)\s*(?:шт\.?|узл\.?)?", name_chunk, re.I)
                if quantity_match:
                    name = quantity_match.group("name").strip()
                    quantity = int(quantity_match.group("qty"))
                else:
                    name, quantity = name_chunk, None
                if name and len(name) > 1:
                    record.substitutions.append(SubstitutionRecord(start, end, name, quantity))

        event_text = line
        if line.startswith("–") or line.startswith("—"):
            continue
        record.events.append(EventRecord(text=event_text, start=start, end=end, category=_category(line)))

    if any(
        "нет данных" in line.lower()
        or "отсутств" in line.lower()
        or "пропуск" in line.lower()
        for line in lines
    ):
        record.warnings.append("В сообщении отмечена отсутствующая информация")
    if not record.machine:
        record.warnings.append("Номер станка не найден")
    if not record.total_counters and not record.slice_counters and not record.counters:
        record.warnings.append("Счетчики не найдены")
    return record


__all__ = ["parse_production_message", "ProductionRecord"]
