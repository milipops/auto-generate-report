"""Выгрузка сменного отчёта в Excel в блочном формате пользователя.

Один файл, на каждый день — отдельный лист (имя листа = дата «ДД.ММ»).
На листе по каждому станку идёт вертикальный блок ровно как в ручном отчёте
пользователя «Отчет_производства_ДД.ММ.xlsx»:

    <дата>
    № машины                              | <N>
    Работа оператора всего        | | <сумма> | из 11,25 часа | [примечание]
       <ч> | <что делал оператор>
    Работа привлекаемого персонала всего  | | <сумма> |
       <ч> | <кто — что делал>
    Работа машины всего           | | <работа станка> | из 12 часов |
       <ч> | простой <причина>

Часы считает детерминированный код (services/shift_report.py); здесь только
раскладка по блокам и объединение одинаковых операций для читаемости.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .shift_report import (LUNCH_FULL_H, MACHINE_SHIFT_HOURS,
                           OPERATOR_SHIFT_HOURS, TOLERANCE_H,
                           ShiftMachineReport, discrepancy_text,
                           extra_works_text, fill_park_machines, submen_fio,
                           submen_hours)
from . import machine_rates as _machine_rates

# Оставлено для обратной совместимости со старыми тестами/импортами.
SHEET_MAIN = "Отчет за смену"
SHEET_STOPS = "Простои по причинам"
SHEET_TOTALS = "Итоги смены"
UNEXPLAINED = "причина не указана"

FONT = "Arial"
# Максимум технических остановок: смена 12 ч минус обед оператора (45 мин) —
# ровно так в ручном файле пользователя у станков, стоявших весь день.
_TECH_STOPS_SHIFT_H = round(MACHINE_SHIFT_HOURS - LUNCH_FULL_H, 2)
_MACHINE_FILL = PatternFill("solid", fgColor="D9E1F2")   # синий — № машины
_SECTION_FILL = PatternFill("solid", fgColor="E2EFDA")   # зелёный — итоги блока
_WARN_FILL = PatternFill("solid", fgColor="FFF2CC")      # жёлтый — примечание
_RED_FILL = PatternFill("solid", fgColor="FFC7CE")       # красный — расхождение
_RED_FONT_COLOR = "9C0006"
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _fmt(value: float) -> float:
    """Часы к 2 знакам (в ячейку кладём число, а не строку)."""
    return round(float(value or 0.0), 2)


def _discrepancy_text(r) -> str:
    """Текст расхождения по станку/оператору («!!! …») или '' если сходится.

    Считаем расхождением недостаток/превышение времени станка (норма 12 ч) и
    недостаток/переработку оператора (норма его смены). Если баланс посчитать
    нельзя (нет нормы/счётчика) — расхождения не показываем, чтобы не пугать
    ложным «недостатком».

    Правило — в shift_report.discrepancy_flags (его же читает выгрузка для 1С);
    здесь остаётся только формат чисел бланка.
    """
    return discrepancy_text(r, _num_str)



def _machine_cell(machine):
    """«98» → 98 числом, «14тр» оставляем строкой."""
    try:
        return int(str(machine).strip())
    except (TypeError, ValueError):
        return machine


def _order_date_cell(value):
    """Дата заявки (колонка H) — кладём в файл ДАТОЙ, как в ручном файле
    пользователя: текстом дата не сортируется и сроки по колонке не считаются.
    Пустая клетка остаётся пустой: даты в переписке станка не было —
    выдумывать её нельзя.
    """
    if not value:
        return ""
    try:
        return datetime.strptime(str(value).strip(), "%d.%m.%Y")
    except ValueError:
        return value


def _label_cell(ws, coord, text, *, bold=False, fill=None, size=10, italic=False):
    c = ws[coord]
    c.value = text
    c.font = Font(name=FONT, size=size, bold=bold, italic=italic)
    if fill is not None:
        c.fill = fill
    c.border = _BORDER
    c.alignment = Alignment(vertical="center", wrap_text=True)
    return c


def _hours_row(ws, row, hours, descr):
    """Строка-деталь: часы в A, описание в B (B:E объединяем)."""
    a = ws[f"A{row}"]
    a.value = _fmt(hours)
    a.number_format = "0.00"
    a.font = Font(name=FONT, size=10)
    a.border = _BORDER
    a.alignment = Alignment(horizontal="center", vertical="center")
    b = ws[f"B{row}"]
    b.value = descr
    b.font = Font(name=FONT, size=10)
    b.alignment = Alignment(vertical="center", wrap_text=True)
    ws.merge_cells(f"B{row}:E{row}")
    for col in ("B", "C", "D", "E"):
        ws[f"{col}{row}"].border = _BORDER


def _section_row(ws, row, title, total, norm_text, note=""):
    """Итоговая строка блока: A=title, C=сумма, D=норма, E=примечание."""
    _label_cell(ws, f"A{row}", title, bold=True, fill=_SECTION_FILL)
    ws.merge_cells(f"A{row}:B{row}")
    ws[f"B{row}"].fill = _SECTION_FILL
    ws[f"B{row}"].border = _BORDER
    c = ws[f"C{row}"]
    c.value = _fmt(total)
    c.number_format = "0.00"
    c.font = Font(name=FONT, size=10, bold=True)
    c.fill = _SECTION_FILL
    c.border = _BORDER
    c.alignment = Alignment(horizontal="center", vertical="center")
    d = ws[f"D{row}"]
    d.value = norm_text
    d.font = Font(name=FONT, size=9)
    d.fill = _SECTION_FILL
    d.border = _BORDER
    d.alignment = Alignment(horizontal="center", vertical="center")
    e = ws[f"E{row}"]
    e.value = note
    e.font = Font(name=FONT, size=9, italic=True)
    e.fill = _WARN_FILL if note else _SECTION_FILL
    e.border = _BORDER
    e.alignment = Alignment(vertical="center", wrap_text=True)


def _fmt_num(value) -> str:
    """Число в виде «11,25» / «8,58» — как в отчёте пользователя."""
    try:
        return f"{float(value):.2f}".rstrip("0").rstrip(".").replace(".", ",") or "0"
    except (TypeError, ValueError):
        return str(value)


def _downtime_descr(label: str) -> str:
    """Причина простоя → строка блока станка («простой …» / «отсутствие …»)."""
    low = label.lower()
    if low.startswith("отсутствие") or low.startswith("ожидание") \
            or "инженерн" in low or low.startswith("внешние"):
        return label
    if "обед" in low:
        return "простой — обед 45 мин (станок стоит)"
    return "простой — " + label


def _overlap_note(r: ShiftMachineReport) -> str:
    """Сколько часов простоя снято со строк как повторные минуты — и почему.

    Станок не может стоять в одних и тех же минутах по двум причинам: строки,
    которые мастер описал одновременно («машина в наладке» и «чинка пропусков»),
    складываются ровно в одну смену — повтор снят со строк простоя (см.
    _deduped_nonabsence). Но без этой приписки лист показывает меньше часов, чем
    мастер их назвал (25.09.2026, ст.111: чинки 4,47 ч, а в строке 1,5), и
    человек не понимает, куда делось остальное.
    """
    deduped = getattr(r, "cross_label_deduped", 0.0)
    if deduped <= TOLERANCE_H:
        return ""
    detail = ", ".join(f"{lab} {_fmt_num(h)} ч"
                       for lab, h in r.cross_label_dedup_detail)
    return (f"из часов простоя {_fmt_num(deduped)} ч — одни и те же минуты, "
            f"названные двумя причинами; станок в них стоял один раз, повтор "
            f"снят со строк: {detail}")


def _operator_extra_note(r: ShiftMachineReport) -> str:
    """Часы оператора сверх его смены — теми же словами, что и в чате.

    Аудит отчёта за 25.09.2026 (ст.98): в чате бот писал «переработка 0,69 ч:
    станок по счётчику работал 10,28 ч — это время у оператора не урезаем», а
    в Excel у строки «Работа оператора всего» примечания не было вовсе. Человек,
    который смотрит в файл, видел 11,94 ч при смене 11,25 ч без всякого
    объяснения и решал, что бот считает неверно.

    Правило 03.09.2026 остаётся в силе: переработку, выросшую из времени работы
    станка по счётчику, красным расхождением НЕ считаем и часы не режем — но
    сказать о ней в листе обязаны. Обычную переработку (не объяснённую
    счётчиком) печатать здесь не нужно: про неё уже говорит красная строка
    расхождения под блоком.
    """
    parts = []
    if getattr(r, "operator_excess", 0) > TOLERANCE_H \
            and getattr(r, "excess_from_machine_time", False):
        parts.append(
            f"переработка {_fmt_num(r.operator_excess)} ч сверх смены "
            f"{_fmt_num(r.operator_norm or OPERATOR_SHIFT_HOURS)} ч: станок по "
            f"счётчику работал {_fmt_num(r.work_hours)} ч, часы оператора не "
            "режем")
    clip = getattr(r, "operator_ceiling_clip", 0.0)
    if clip > 0.01:
        # Откуда взялся срез — из филлера или из названных мастером операций:
        # когда одни названные работы длиннее смены, «контроля за машиной» в
        # обрезке нет вовсе, и прежняя фраза про него врёт.
        from_rows = getattr(r, "operator_rows_over_shift", 0.0)
        from_filler = round(clip - from_rows, 2)
        why = []
        if from_filler > 0.01:
            why.append(f"{_fmt_num(from_filler)} ч «контроля за работой машины»")
        if from_rows > 0.01:
            why.append(f"{_fmt_num(from_rows)} ч названных мастером операций")
        parts.append(
            f"по записям у оператора {_fmt_num(r.operator_hours + clip)} ч, "
            f"а смена 07:00–19:00 — только {_fmt_num(MACHINE_SHIFT_HOURS)} ч: "
            f"{', '.join(why) if why else _fmt_num(clip) + ' ч'} в итог не вошли")
    clip_desc, clip_hours = getattr(r, "operator_rows_clip", ("", 0.0))
    if clip_hours > 0.01:
        parts.append(
            f"строки работы оператора длиннее смены: со строки «{clip_desc}» "
            f"снято {_fmt_num(clip_hours)} ч — окна в переписке пересеклись, "
            "разберите их вручную")
    span_clip = getattr(r, "operator_span_clip", 0.0)
    if span_clip > 0.01:
        parts.append(
            f"станок по счётчику работал {_fmt_num(r.work_hours)} ч, а оператор у "
            f"станка был {_fmt_num(r.operator_shift_span)} ч: "
            f"{_fmt_num(span_clip)} ч «контроля за работой машины» в его часы не "
            "вошли — времени у станка сверх присутствия не было, сверьте, кто вёл "
            "станок в эти часы")
    return "; ".join(parts)


def _machine_note(r: ShiftMachineReport) -> str:
    """Примечание к строке «Работа машины всего» (колонка E)."""
    if getattr(r, "not_parsed", False):
        base = "!!! ИИ НЕ РАЗОБРАЛ СТАНОК — данных нет в отчёте"
    elif r.balance_unknown:
        base = "нет данных о работе"
    else:
        base = ""
    return " ".join(x for x in (base, _overlap_note(r)) if x)


def _write_machine_block(ws, r: ShiftMachineReport, row: int) -> int:
    """Записать блок одного станка, вернуть следующую свободную строку."""
    # Заголовок: № машины
    _label_cell(ws, f"A{row}", "№ машины", bold=True, fill=_MACHINE_FILL)
    b = ws[f"B{row}"]
    b.value = _machine_cell(r.machine)
    b.font = Font(name=FONT, size=11, bold=True)
    b.fill = _MACHINE_FILL
    b.border = _BORDER
    b.alignment = Alignment(horizontal="center", vertical="center")
    if r.brand:
        _label_cell(ws, f"C{row}", r.brand, fill=_MACHINE_FILL, size=9)
        ws.merge_cells(f"C{row}:E{row}")
        for col in ("D", "E"):
            ws[f"{col}{row}"].fill = _MACHINE_FILL
            ws[f"{col}{row}"].border = _BORDER
    row += 1

    # 1) Работа оператора всего
    op_rows = r.operator_work_by_reason()
    note = getattr(r, "operator_block_note", None)
    if note is None:      # старые отчёты без свойства
        note = "" if r.has_operator and r.operator_hours > 0.01 \
            else "отсутствие оператора"
    # Норму смены пишем только там, где человек на станке ожидался. Станок в
    # наладке/инженерной службе без фамилии и станок без данных нормы не имеют —
    # в эталоне пользователя (02.09: 111/121, 601, 14тр/15тр) там стоит только
    # пояснение, без «из 11,25».
    if getattr(r, "no_operator_all_shift", False) or getattr(
            r, "no_shift_data", False):
        norm_txt = ""
    elif r.has_operator:
        # Норму берём ИЗ РАСЧЁТА, а не константой 11,25: смена бывает короче
        # (стажёр, ранний уход). Прогон 15.09.2026, ст.321: оператор Демидов
        # отработал до 16:20, его норма 8,58 ч, а в отчёте напечаталось
        # «9,9 из 11,25 часа» — в эталоне пользователя «8,25 из 8,25 часa».
        norm = r.operator_norm or OPERATOR_SHIFT_HOURS
        norm_txt = f"из {_fmt_num(norm)} часа"
    else:
        # Человека на станке нет, а смена по расписанию была — печатаем норму
        # смены, чтобы «0 из 11,25 — отсутствие оператора» читался как
        # недостаток, а не как «станок не планировался». Число берём из общего
        # правила, а не литералом: иначе потолок смены (25.09.2026) заведётся в
        # двух местах.
        norm_txt = f"из {_fmt_num(OPERATOR_SHIFT_HOURS)}"
    _section_row(ws, row, "Работа оператора всего", r.operator_hours,
                 norm_txt,
                 " ".join(x for x in (note, _operator_extra_note(r)) if x))
    row += 1
    for descr, hours in op_rows:
        _hours_row(ws, row, hours, descr)
        row += 1

    # 2) Работа привлекаемого персонала всего (если есть)
    attr = r.attracted_by_person()
    if attr:
        _section_row(ws, row, "Работа привлекаемого персонала всего",
                     r.attracted_total, "")
        row += 1
        for descr, hours in attr:
            _hours_row(ws, row, hours, descr)
            row += 1

    # 3) Работа машины всего
    if r.engineering_all_shift:
        _section_row(ws, row, "Работа машины всего", 0.0, "из 12 часов",
                     "машина в инженерной службе")
        row += 1
        for label, hours, _by in r.downtime_by_reason():
            _hours_row(ws, row, hours, _downtime_descr(label))
            row += 1
    else:
        _section_row(ws, row, "Работа машины всего", r.work_hours,
                     "из 12 часов", _machine_note(r))
        row += 1
        if r.work_hours > 0.01:
            _hours_row(ws, row, r.work_hours, "работа машины")
            row += 1
        for label, hours, _by in r.downtime_by_reason():
            _hours_row(ws, row, hours, _downtime_descr(label))
            row += 1
        if r.unexplained_downtime > 0.01 and not r.balance_unknown:
            _hours_row(ws, row, r.unexplained_downtime,
                       "простой — " + getattr(r, "unexplained_label",
                                              UNEXPLAINED))
            row += 1

    # Отдельная строка расхождения (недостаток/превышение) — красным.
    disc = _discrepancy_text(r)
    if disc:
        ws.merge_cells(f"A{row}:E{row}")
        c = ws[f"A{row}"]
        c.value = disc
        c.font = Font(name=FONT, size=10, bold=True, color=_RED_FONT_COLOR)
        c.alignment = Alignment(vertical="center", wrap_text=True)
        for col in ("A", "B", "C", "D", "E"):
            cell = ws[f"{col}{row}"]
            cell.fill = _RED_FILL
            cell.border = _BORDER
        row += 1

    row += 1   # пустая строка-разделитель между станками
    return row


# ── лист-таблица в формате «Книга1» (одна строка на станок/ассортимент) ──

_TABLE_HEADERS = [
    ("A", "Фамилия", 14), ("B", "№ машины", 10), ("C", "Марка машины", 12),
    ("D", "Ассортимент", 30), ("E", "Кол-во полотен", 9), ("F", "Клиент", 16),
    ("G", "№ заявки", 9), ("H", "Дата заявки", 12), ("I", "подмены, ч", 10),
    ("J", "фио на подменах", 20), ("K", "Кол-во срезов", 9),
    ("L", "Счётчик начальное", 14), ("M", "Счётчик конечное", 14),
    ("N", "Выпуск фактический, узл.", 13), ("O", "Время, ч", 9),
    ("P", "Доп. работы (шт, кг)", 16), ("Q", "Тех. остановки: Время, ч", 22),
    ("R", "Тех. остановки: Причина", 30), ("S", "Кол-во подмен", 9),
    ("T", "Отходов в минуту (T)", 12),
    ("U", "Расхождение (недостаток времени)", 28),
]
_TABLE_HEAD_FILL = PatternFill("solid", fgColor="D9E1F2")


def _num_str(value: float) -> str:
    """Число с запятой-десятичным (как в ручных файлах пользователя)."""
    return ("%g" % round(float(value or 0.0), 2)).replace(".", ",")


def _stops_columns(r: ShiftMachineReport):
    """(время_ч, причина) для колонок «Технические остановки» в стиле Книга1.

    Несколько остановок — сумма-строкой «1+0,18+2,13» и причины «чинка+…».
    Инженерная служба — своя единственная строка.

    Обед и подмены в эту колонку НЕ идут: у них свои места («подмены, ч»,
    «фио на подменах»), а обед — строка простоя в листе «Пояснение». Так в
    ручном файле пользователя за 22.09.2026: ст.98 с двумя подменами и обедом
    имеет пустую колонку, ст.321 — «2,7+5,95 | наладка+чинка».

    Но «не идут» только те часы, которые колонка «подмены, ч» действительно
    напечатала. Строку может называть подменой слово в ярлыке («замена
    шпулярника (станок стоит)»), а окна с подменщиком мастер не называл вовсе —
    тогда в I пусто, и раньше такие часы молча исчезали из бланка при том, что
    лист «Пояснение» и выгрузка для 1С их видели. Прогон 27.09.2026, ст.111:
    у него «1,58+0,43+0,15+0,43+0,48+2,03» = 5,10 ч, у нас было 4,22 — ровно на
    спрятанные 0,88 ч «подстановки бобин». С 29.09.2026 строка остаётся в
    технических остановках словами мастера.
    """
    from .shift_report import is_lunch_label, is_submen_label, submen_hours

    if getattr(r, "engineering_all_shift", False):
        return _TECH_STOPS_SHIFT_H, "инженерная служба"
    if getattr(r, "naladka_all_shift_no_operator", False):
        return _TECH_STOPS_SHIFT_H, "наладка"
    # Фон «всю смену» (замена шпулярника в течении всей смены) остаётся в
    # технических остановках, хотя в ярлыке есть слово «шпулярник»: это окно
    # никогда не было подменой с часами в колонке «подмены, ч», и иначе его
    # часы исчезали из бланка (ст.91 за 24.09: 2,78 ч).
    shiftlong = getattr(r, "shiftlong_swap_label", "")
    subs_in_blank = submen_hours(r) > 0.01
    rows = [(lab, h) for lab, h, _a in r.downtime_by_reason()
            if h and h > 0.01
            and not is_lunch_label(lab)
            and (not subs_in_blank
                 or not is_submen_label(lab) or lab == shiftlong)]
    if not rows:
        return "", ""
    total = round(sum(h for _lab, h in rows), 2)
    if total > _TECH_STOPS_SHIFT_H and r.work_hours <= 0.01:
        # Станок стоял всю смену: 12 ч за вычетом обеда (0,75) — это 11,25 ч
        # технических остановок, как в ручном файле (ст.121, 601, 321, 251).
        return _TECH_STOPS_SHIFT_H, "+".join(lab for lab, _h in rows)
    if len(rows) == 1:
        return round(rows[0][1], 2), rows[0][0]
    return ("+".join(_num_str(h) for _lab, h in rows),
            "+".join(lab for lab, _h in rows))


def _subs_hours_cell(r: ShiftMachineReport):
    """Колонка «подмены, ч»: окно простоя станка на подмене.

    Сумма по людям (attracted_subs_total) для этой колонки не годится: трое на
    одном окне дают в ней 17,46 ч при смене 12 ч (прогон 22.09.2026, ст.151).
    Правило считает shift_report.submen_hours — тот же код читает и выгрузка
    для 1С, чтобы две выгрузки одного дня не разъезжались.

    Запасной путь «если правило дало ноль, взять r.subs_hours» здесь был именно
    таким разъездом: он печатал цифру ИИ там, где правило сознало клетку пустой.
    Прогон 27.09.2026, ст.111: «16:29 Стоп на подмену. Сошла шпуля №7» — партия
    шпуль, а не человек у машины, в его ручном файле клетка пустая.
    """
    hours = submen_hours(r)
    return hours if hours > 0.01 else ""


def _result_columns(r: ShiftMachineReport, ln) -> dict:
    """Срезы, счётчики и число подмен: вчерашняя вводная — не результат смены.

    «Кол-во подмен» (S) не заполняется вовсе — приказ владельца 30.09.2026 на
    вопрос «считать туда цифру из строки мастера или не заполнять»: «не заполняй
    колонку». В его файле за 29.09 клетка пуста у всех десяти станков, в том
    числе там, где подмены были (№98 1,66 ч, №111 1,5 ч).
    """
    blank = {"K": "", "L": None, "M": None, "S": ""}
    if getattr(r, "nothing_produced", False):
        return blank
    return {
        "K": (r.cuts if r.cuts is not None else ""),
        "L": getattr(ln, "counter_start", None) if ln else None,
        "M": getattr(ln, "counter_end", None) if ln else None,
        "S": "",
    }


def _subs_fio(r: ShiftMachineReport) -> str:
    """ФИО тех, кто был на подменах (привлечённый персонал / записанный fio).

    Только подмены: наладчик сюда НЕ попадает (решение пользователя 27.08.2026)
    — наладка идёт лишь в простой машины, а не в столбец «фио на подменах».

    Самого оператора тоже перечисляем, когда подмену делала он(а) — в ручном
    файле пользователя за 22.09.2026: ст.98 «Белякова», ст.111 «Гудкова Сеньков
    Клинов», ст.151 «Арташина Сеньков Клинов». При этом оператор, который всю
    смену отсутствовал (ст.321/21.09 — «подмена 1 из 6» из утреннего блока), в
    колонке не появляется: признак — строка отсутствия на всю смену
    (`operator_absent_all_shift`), а не только «своего часа подмены не нашлось»:
    тот час мог придумать ИИ, перенеся окно с чужого станка.

    Час подмен берём ТЕ ЖЕ, что и в колонке «подмены, ч» (окно простоя станка,
    а не сумма по людям): иначе строки разъезжаются — прогон 14.09.2026, ст.251
    показал 1,13 ч подмен при пустом «фио на подменах», потому что своего
    оператора мы включали по другому признаку (r.subs_hours).

    Само правило живёт в shift_report.submen_fio — ту же колонку читает выгрузка
    для 1С, иначе одна и та же смена давала «Белякова» в Excel и пустую клетку
    в файле для 1С.
    """
    return submen_fio(r)


def _write_table_sheet(ws, reports: List[ShiftMachineReport],
                       date: Optional[str]) -> None:
    """Лист-таблица «Отчёт производства за смену» в колонках файла Книга1."""
    for col, _title, width in _TABLE_HEADERS:
        ws.column_dimensions[col].width = width
    ws["D1"] = "Отчет производства за смену"
    ws["D1"].font = Font(name=FONT, size=12, bold=True)
    if date:
        ws["F1"] = date
        ws["F1"].font = Font(name=FONT, size=11, bold=True)
    ws["G1"] = f"{OPERATOR_SHIFT_HOURS} часов".replace(".", ",")
    ws["G1"].font = Font(name=FONT, size=10)
    for col, title, _w in _TABLE_HEADERS:
        c = ws[f"{col}2"]
        c.value = title
        c.font = Font(name=FONT, size=9, bold=True)
        c.fill = _TABLE_HEAD_FILL
        c.border = _BORDER
        c.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=True)
    row = 3
    _center = ("B", "E", "K", "L", "M", "N", "O", "S", "T")
    for r in reports:
        lines = r.lines or [None]
        stop_time, stop_reason = _stops_columns(r)
        fio = _subs_fio(r)
        disc = _discrepancy_text(r)
        for idx, ln in enumerate(lines):
            first = idx == 0
            t_line = None
            if ln is not None:
                t_line = _machine_rates.work_hours(
                    r.machine, getattr(ln, "counter_start", None),
                    getattr(ln, "counter_end", None))
            res = _result_columns(r, ln)
            vals = {
                "A": (r.operator or "") if first else "",
                "B": _machine_cell(r.machine) if first else "",
                "C": (r.brand or "") if first else "",
                "D": getattr(ln, "assortment", None) if ln else None,
                "E": getattr(ln, "panels", None) if ln else None,
                "F": getattr(ln, "client", None) if ln else None,
                "G": getattr(ln, "order_no", None) if ln else None,
                "H": _order_date_cell(getattr(ln, "order_date", None)) if ln else "",
                "I": _subs_hours_cell(r) if first else "",
                "J": fio if first else "",
                "K": res["K"] if first else "",
                "L": res["L"],
                "M": res["M"],
                "N": getattr(ln, "output", None) if ln else None,
                "O": round(t_line, 2) if t_line else (
                    r.work_hours if (first and not r.lines) else None),
                "P": extra_works_text(r) if first else "",
                "Q": stop_time if first else "",
                "R": stop_reason if first else "",
                "S": res["S"] if first else "",
                "T": r.rate if first else "",
                "U": disc if first else "",
            }
            for col, val in vals.items():
                cell = ws[f"{col}{row}"]
                cell.value = val
                cell.font = Font(name=FONT, size=9)
                cell.border = _BORDER
                cell.alignment = Alignment(
                    vertical="center", wrap_text=col in ("D", "R", "J", "U"),
                    horizontal="center" if col in _center else "left")
                if col == "U" and val:
                    cell.font = Font(name=FONT, size=9, bold=True,
                                     color=_RED_FONT_COLOR)
                    cell.fill = _RED_FILL
                if col == "H" and hasattr(val, "strftime"):
                    cell.number_format = "DD.MM.YYYY"
                    cell.alignment = Alignment(horizontal="center",
                                               vertical="center")
            row += 1
    ws.freeze_panes = "A3"


def _write_day_sheet(ws, reports: List[ShiftMachineReport],
                     date: Optional[str]) -> None:
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 46
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 15
    ws.column_dimensions["E"].width = 30
    row = 1
    if date:
        c = ws[f"A{row}"]
        c.value = date
        c.font = Font(name=FONT, size=12, bold=True)
        row += 2
    for r in reports:
        row = _write_machine_block(ws, r, row)


# ───────────────── лист «Остатки и отходы» (учёт по каждой машине) ─────────────────
# Просьба пользователя 09.09.2026: мастера в конце смены присылают по каждой
# машине блок «Конец(начало) смены … Сдаваемые отходы …», и по нему нужен
# ОТДЕЛЬНЫЙ лист — учёт отходов по каждой машине. Структура выбрана так, чтобы
# читалось без объяснений: слева станок и оператор, дальше показания «как
# написал мастер», дальше килограммы по видам, последняя колонка — итог по
# станку. Внизу — строка ИТОГО и расшифровка «сколько какого вида за смену».

# (ключ, подпись, ширина). Ключи показаний/отходов берём из remains_report.
_REMAINS_HEAD_FILL = PatternFill("solid", fgColor="FCE4D6")   # оранжевый — отходы
_REMAINS_COUNT_FILL = PatternFill("solid", fgColor="DDEBF7")  # синий — показания


def _remains_columns():
    """Колонки листа: (буква, подпись, ширина, группа)."""
    from .remains_report import COUNTER_FIELDS, WASTE_KINDS
    cols = [("A", "№ машины", 10, ""), ("B", "Оператор", 16, ""),
            ("C", "Замер", 9, "")]
    letters = [chr(ord("D") + i) for i in range(24)]
    it = iter(letters)
    for _key, label, _has_of in COUNTER_FIELDS:
        cols.append((next(it), label, 14, "counters"))
    for _key, label in WASTE_KINDS:
        cols.append((next(it), label, 17, "waste"))
    cols.append((next(it), "Всего сдано, кг", 13, "total"))
    cols.append((next(it), "Причина прочих отходов", 26, ""))
    cols.append((next(it), "Состояние машины и полотна", 40, ""))
    return cols


def _remains_cell(ws, coord, value, *, bold=False, fill=None, size=9,
                  align="left", wrap=False, number=False):
    c = ws[coord]
    c.value = value
    c.font = Font(name=FONT, size=size, bold=bold)
    if fill is not None:
        c.fill = fill
    c.border = _BORDER
    c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    if number:
        c.number_format = "0.###"
    return c


def _write_remains_sheet(ws, day, reports: List[ShiftMachineReport],
                         date: Optional[str]) -> None:
    """Лист учёта остатков сырья и сдаваемых отходов по каждой машине."""
    from .remains_report import (COUNTER_FIELDS, WASTE_KINDS,
                                 machines_without_remains)
    cols = _remains_columns()
    # Оператора мог не заполнить сборщик остатков (его зовут и без отчётов
    # смены), поэтому фамилию берём из отчёта по тому же станку.
    by_machine = {}
    for rep in reports or []:
        num = str(getattr(rep, "machine", "") or "").strip()
        if num and getattr(rep, "operator", None):
            by_machine.setdefault(_machine_rates._norm_key(num), rep.operator)
    for letter, _label, width, _grp in cols:
        ws.column_dimensions[letter].width = width

    n_count = len(COUNTER_FIELDS)
    n_waste = len(WASTE_KINDS)
    first_count = cols[3][0]
    last_count = cols[2 + n_count][0]
    first_waste = cols[3 + n_count][0]
    last_waste = cols[2 + n_count + n_waste][0]
    total_col = cols[3 + n_count + n_waste][0]
    reason_col = cols[4 + n_count + n_waste][0]
    state_col = cols[5 + n_count + n_waste][0]

    ws["A1"] = "Остатки сырья и сдаваемые отходы по машинам"
    ws["A1"].font = Font(name=FONT, size=12, bold=True)
    if date:
        ws[f"{first_count}1"] = date
        ws[f"{first_count}1"].font = Font(name=FONT, size=11, bold=True)
    ws["A2"] = ("Цифры — как их прислал мастер в блоке «Конец(начало) смены»; "
                "пустая клетка «—» значит, что поле не заполнили (это не ноль).")
    ws["A2"].font = Font(name=FONT, size=9, italic=True)

    # Шапка в две строки: группа + сами колонки.
    _remains_cell(ws, f"{first_count}3", "Показания на конец (начало) смены",
                  bold=True, fill=_REMAINS_COUNT_FILL, align="center")
    ws.merge_cells(f"{first_count}3:{last_count}3")
    _remains_cell(ws, f"{first_waste}3", "Сдаваемые отходы, кг", bold=True,
                  fill=_REMAINS_HEAD_FILL, align="center")
    ws.merge_cells(f"{first_waste}3:{last_waste}3")
    for letter, label, _w, group in cols:
        fill = (_REMAINS_COUNT_FILL if group == "counters"
                else _REMAINS_HEAD_FILL if group in ("waste", "total")
                else _TABLE_HEAD_FILL)
        _remains_cell(ws, f"{letter}4", label, bold=True, fill=fill,
                      align="center", wrap=True)
    row = 5
    for block in day.machines:
        _remains_cell(ws, f"A{row}", _machine_cell(block.machine)
                      if block.machine else "не указан", bold=True,
                      align="center")
        _remains_cell(ws, f"B{row}", block.operator
                      or by_machine.get(
                          _machine_rates._norm_key(block.machine or ""), ""))
        _remains_cell(ws, f"C{row}",
                      {"конец": "конец смены", "начало": "начало смены"}
                      .get(block.moment, ""), align="center")
        for i, (key, _label, _has_of) in enumerate(COUNTER_FIELDS):
            _remains_cell(ws, f"{cols[3 + i][0]}{row}",
                          block.counter_text(key), align="center")
        for i, (key, _label) in enumerate(WASTE_KINDS):
            val = block.waste.get(key)
            _remains_cell(ws, f"{cols[3 + n_count + i][0]}{row}",
                          val if isinstance(val, (int, float)) else "—",
                          align="center", number=True)
        # Станок без блока отходов: «—», а не 0. Ноль значит «сдал ноль».
        _remains_cell(ws, f"{total_col}{row}",
                      block.waste_total if block.has_waste else "—", bold=True,
                      fill=_REMAINS_HEAD_FILL, align="center", number=True)
        _remains_cell(ws, f"{reason_col}{row}", block.other_reason or "",
                      wrap=True)
        _remains_cell(ws, f"{state_col}{row}", block.state or "", wrap=True,
                      fill=_WARN_FILL if block.state else None)
        row += 1

    # Станки смены, по которым блок не присылали: строка-напоминание, без нулей.
    missing = machines_without_remains(day, reports)
    for num in missing:
        _remains_cell(ws, f"A{row}", _machine_cell(num), bold=True,
                      align="center")
        _remains_cell(ws, f"B{row}", by_machine.get(
            _machine_rates._norm_key(num), ""))
        _remains_cell(ws, f"C{row}", "", align="center")
        for i in range(n_count + n_waste + 1):
            _remains_cell(ws, f"{cols[3 + i][0]}{row}", "—", align="center")
        _remains_cell(ws, f"{reason_col}{row}", "")
        _remains_cell(ws, f"{state_col}{row}",
                      "блок «Сдаваемые отходы» за смену не присылали",
                      wrap=True, fill=_WARN_FILL)
        row += 1

    # ИТОГО по видам отходов.
    totals = day.totals()
    _remains_cell(ws, f"A{row}", "ИТОГО за смену", bold=True,
                  fill=_SECTION_FILL)
    ws.merge_cells(f"A{row}:C{row}")
    for col in ("B", "C"):
        ws[f"{col}{row}"].fill = _SECTION_FILL
        ws[f"{col}{row}"].border = _BORDER
    for i in range(n_count):
        _remains_cell(ws, f"{cols[3 + i][0]}{row}", "", fill=_SECTION_FILL)
    for i, (key, _label) in enumerate(WASTE_KINDS):
        _remains_cell(ws, f"{cols[3 + n_count + i][0]}{row}", totals.get(key, 0.0),
                      bold=True, fill=_SECTION_FILL, align="center", number=True)
    _remains_cell(ws, f"{total_col}{row}", day.waste_total, bold=True,
                  fill=_SECTION_FILL, align="center", number=True)
    _remains_cell(ws, f"{reason_col}{row}", "", fill=_SECTION_FILL)
    _remains_cell(ws, f"{state_col}{row}", "", fill=_SECTION_FILL)
    row += 2

    # Расшифровка «какого вида сколько» — чтобы не считать глазами по колонкам.
    _remains_cell(ws, f"A{row}", "Сдано за смену по видам отходов", bold=True,
                  fill=_REMAINS_HEAD_FILL)
    ws.merge_cells(f"A{row}:C{row}")
    row += 1
    for key, label in WASTE_KINDS:
        _remains_cell(ws, f"A{row}", label, wrap=True)
        ws.merge_cells(f"A{row}:B{row}")
        _remains_cell(ws, f"C{row}", totals.get(key, 0.0), align="center",
                      number=True)
        row += 1
    _remains_cell(ws, f"A{row}", "Всего, кг", bold=True, fill=_SECTION_FILL)
    ws.merge_cells(f"A{row}:B{row}")
    _remains_cell(ws, f"C{row}", day.waste_total, bold=True,
                  fill=_SECTION_FILL, align="center", number=True)
    row += 2

    for text in (day.warnings or []):
        _remains_cell(ws, f"A{row}", "⚠️ " + str(text), wrap=True,
                      fill=_WARN_FILL)
        ws.merge_cells(f"A{row}:{first_waste}{row}")
        row += 1
    ws.freeze_panes = "D5"


def _safe_sheet_title(base: str, used: set) -> str:
    """Имя листа: Excel не любит длинные и повторяющиеся имена."""
    base = (base or "смена").replace("/", ".")[:31] or "смена"
    title, i = base, 2
    while title in used:
        title = f"{base} ({i})"[:31]
        i += 1
    used.add(title)
    return title


def _sheet_dates(date: Optional[str]) -> tuple:
    """Короткая дата «ДД.ММ» для имён листов пояснения и таблицы."""
    base = (date or "смена").replace("/", ".")
    parts = base.split(".")
    short = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else base
    return short


def export_day_sheets(days: Dict[str, List[ShiftMachineReport]],
                      path: str,
                      remains: Optional[Dict] = None) -> str:
    """Один файл, на каждый день ТРИ листа: «Пояснение», таблица и «Остатки».

    По каждой смене:
      1) «Пояснение ДД.ММ» — вертикальный блочный разбор по станкам;
      2) «ДД.ММ» — таблица в колонках файла пользователя «Книга1»;
      3) «Остатки ДД.ММ» — учёт остатков сырья и сдаваемых отходов по машинам
         (только если по смене прислали блоки «Сдаваемые отходы»).
    Порядок дней — по возрастанию даты (как в эталонном файле пользователя).

    remains — {дата: DayRemains}. Необязательный: старые вызовы (и период без
    накопителя) работают как раньше, просто без третьего листа.
    """
    if not days:
        raise ValueError("Нет отчётов для выгрузки")
    wb = Workbook()
    wb.remove(wb.active)
    used: set = set()

    def _key(d: str):
        parts = (d or "").replace("/", ".").split(".")
        try:
            return tuple(int(p) for p in reversed(parts))
        except ValueError:
            return (0,)

    for date in sorted(days, key=_key):
        reports = fill_park_machines(days[date], date)
        short = _sheet_dates(date)
        ws_block = wb.create_sheet(_safe_sheet_title(f"Пояснение {short}", used))
        _write_day_sheet(ws_block, reports, date)
        ws_table = wb.create_sheet(_safe_sheet_title(short, used))
        _write_table_sheet(ws_table, reports, date)
        day_remains = (remains or {}).get(date)
        # Пустой лист не создаём: если блоков «Сдаваемые отходы» за смену не
        # присылали, лишняя вкладка только мешает читать файл.
        if day_remains is not None and getattr(day_remains, "ok", False):
            ws_rem = wb.create_sheet(_safe_sheet_title(f"Остатки {short}", used))
            _write_remains_sheet(ws_rem, day_remains, reports, date)
    wb.save(path)
    return path


def export_shift_reports(reports: List[ShiftMachineReport], path: str,
                         date: Optional[str] = None,
                         remains=None) -> str:
    """Сохранить отчёты одной смены в блочном формате. Возвращает путь.

    remains — DayRemains этой смены (лист «Остатки»), необязательно.
    """
    if not reports:
        raise ValueError("Нет отчётов для выгрузки")
    date = date or next((r.date for r in reports if r.date), None)
    key = date or "смена"
    return export_day_sheets({key: reports}, path,
                             remains={key: remains} if remains else None)

