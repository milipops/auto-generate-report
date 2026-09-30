"""Выгрузка смены в файл для ручной загрузки в 1С.

Зачем отдельный формат, а не тот же «бланк смены».

Файл «Отчет производства за смену …» свёрстан ДЛЯ ЧЕЛОВЕКА: вертикальные
блоки, объединённые ячейки, пояснения словами. 1С такой лист штатно загрузить
не может — ей нужна ПЛОСКАЯ прямоугольная таблица: одна строка = одна запись,
колонки с постоянными заголовками, числа числами.

Что за 1С у пользователя (уточнено 31.08.2026): УЧЕБНАЯ бесплатная
конфигурация, платформа 8.3.27. У неё ОТКРЫТ конфигуратор — пользователь сам
1С-ник и готов ПИСАТЬ КОД на встроенном языке, который читает загруженный файл
и раскладывает данные по справочникам/документам. Автоматический ежедневный
«забор» на стороне 1С учебная версия штатно не тянет (файловый режим, лимиты,
лицензия под обучение), поэтому загрузка — ручная, один раз в день.

Отсюда три формата, от простого к полному:

  * CSV UTF-8 с BOM, разделитель «;» — грузится штатной «Загрузкой данных из
    табличного документа» вообще без кода. Плоская таблица, 11 колонок.
  * XLSX с той же плоской таблицей — если удобнее сопоставлять колонки из книги.
  * JSON (`export_json`) — ПОЛНАЯ выгрузка: в ней есть ВСЁ, что есть в Excel-
    отчёте (оба листа: вертикальные блоки И таблица-Книга1), плюс ключи
    справочников (станки с маркой и нормой T, операторы). Это формат под
    собственный код в 1С: платформа 8.3.27 умеет `ЧтениеJSON`/`ПрочитатьJSON`,
    поэтому пользователь читает объект и сам решает, что в какой справочник/
    документ/регистр положить. Именно этот формат отвечает требованию «всё, что
    есть в Excel, должно быть в выгрузке 1С».

Числа: в CSV/XLSX — в русской локали (запятая), т.к. 1С так ждёт их при ручной
загрузке; в JSON — настоящими числами (float), т.к. `ЧтениеJSON` разбирает их
сам, а с точкой-разделителем кодить в 1С проще.

Модуль ничего не считает: он только раскладывает уже готовые
ShiftMachineReport по строкам/полям. Вся арифметика остаётся в shift_report.py.
"""
from __future__ import annotations

import csv
import json
from typing import List, Optional

# Арифметику и правила колонок не дублируем: берем их из того же кода, который
# читает Excel (shift_report — чистый stdlib, без openpyxl, поэтому выгрузка
# работает и там, где Excel не собрать).
from .shift_report import (_fmt_h, discrepancy_text, extra_works, submen_fio,
                           submen_hours)

# Заголовки колонок плоской таблицы. Порядок фиксирован — на него будет
# настроено сопоставление при загрузке в 1С, поэтому менять его нельзя без
# синхронной правки настройки загрузки на стороне пользователя.
COLUMNS = [
    "Дата",
    "Станок",
    "Оператор",
    "Часы работы станка",
    "Часы оператора",
    "Норма оператора",
    "Привлечённые часы",
    "Недостаток оператора",
    "Превышение оператора",
    "Простои (причины)",
    "Примечание",
]

# Колонки учёта отходов (просьба 09.09.2026). Дописаны В КОНЕЦ намеренно:
# сопоставление первых 11 колонок при загрузке в 1С уже настроено, и вставка
# в середину сломала бы его. Новые колонки 1С может просто не сопоставлять.
WASTE_COLUMNS = [
    "Отходы: от навивки шпулей, кг",
    "Отходы: от развивки шпулей, кг",
    "Отходы: наладочные, кг",
    "Отходы: от протяжки узлов, кг",
    "Отходы: при подмене шпулярника, кг",
    "Отходы: прочие, кг",
    "Отходы всего, кг",
    "Причина прочих отходов",
    "Состояние машины и полотна",
]


def _num(value) -> str:
    """Число в русской локали: 11.25 → «11,25». Пустое — пустая строка."""
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def _downtime_text(report) -> str:
    """Простои одной строкой: «наладка/переналадка 2,13; чинка/ремонт 1,5»."""
    try:
        rows = report.downtime_by_reason()
    except Exception:                              # у пустого станка причин нет
        return ""
    parts = [f"{label} {_num(hours)}"
             for label, hours, *_ in rows if hours and hours > 0.01]
    return "; ".join(parts)


def _note(report) -> str:
    """Пометка для человека: потерянный ИИ станок или «нет данных»."""
    if getattr(report, "not_parsed", False):
        return "ИИ не разобрал станок — данных нет"
    if report.balance_unknown:
        return "нет данных о работе" if not report.work_hours else \
            report.balance_unknown
    return ""


def _waste_cells(block) -> List[str]:
    """Хвост строки: килограммы по видам отходов + итог + причина + состояние.

    block — MachineRemains по этому станку или None. Пусто ≠ ноль: если мастер
    поле не заполнил, в 1С уедет пустая клетка, а не выдуманный нуль.
    """
    from .remains_report import WASTE_KEYS
    if block is None:
        return ["" for _ in WASTE_COLUMNS]
    cells = [_num(block.waste.get(key)) for key in WASTE_KEYS]
    cells.append(_num(block.waste_total) if block.has_waste else "")
    cells.append(block.other_reason or "")
    cells.append(block.state or "")
    return cells


def report_to_row(report, remains=None) -> List[str]:
    """Одна строка плоской таблицы из отчёта по станку.

    remains — DayRemains смены; из него берём отходы этого станка.
    """
    unknown = bool(report.balance_unknown)
    row = [
        report.date or "",
        report.machine or "",
        report.operator or "",
        _num(report.work_hours),
        _num(report.operator_hours) if report.has_operator else "",
        _num(report.operator_norm) if report.has_operator else "",
        _num(report.attracted_total),
        "" if unknown else _num(report.operator_shortfall),
        "" if unknown else _num(report.operator_excess),
        _downtime_text(report),
        _note(report),
    ]
    block = remains.by_machine(report.machine) if remains is not None else None
    return row + _waste_cells(block)


def build_rows(reports: List, remains=None) -> List[List[str]]:
    """Плоская таблица: заголовок + строка на каждый станок.

    Станки, по которым прислали отходы, но которых нет в отчётах (например, ИИ
    их не разобрал), тоже попадают в таблицу — иначе килограммы потеряются.
    """
    rows = [list(COLUMNS) + list(WASTE_COLUMNS)]
    seen = set()
    for r in reports:
        rows.append(report_to_row(r, remains))
        seen.add(str(getattr(r, "machine", "") or "").strip())
    if remains is not None:
        for block in remains.machines:
            num = str(block.machine or "").strip()
            if num in seen:
                continue
            head = [remains.date or "", num, block.operator or ""] + \
                [""] * (len(COLUMNS) - 4) + ["только сдаваемые отходы"]
            rows.append(head + _waste_cells(block))
    return rows


def export_csv(reports: List, path: str, remains=None) -> str:
    """CSV UTF-8-BOM, разделитель «;» — для штатной загрузки в базовую 1С."""
    rows = build_rows(reports, remains)
    # utf-8-sig: BOM обязателен, иначе базовая 1С в русской Windows прочитает
    # кириллицу кракозябрами.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerows(rows)
    return path


def export_xlsx(reports: List, path: str, date: Optional[str] = None,
                remains=None) -> str:
    """XLSX с той же плоской таблицей (один лист «Выгрузка»)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except Exception as e:                         # без openpyxl — понятно молчим
        raise RuntimeError(
            "Для выгрузки в XLSX нужен openpyxl (pip install openpyxl)") from e

    rows = build_rows(reports, remains)
    wb = Workbook()
    ws = wb.active
    ws.title = "Выгрузка"
    for r in rows:
        ws.append(r)
    for cell in ws[1]:                             # шапку — жирным
        cell.font = Font(bold=True)
    # Ширину колонок подгоняем по содержимому, чтобы файл открывался читаемым.
    for i, name in enumerate(list(COLUMNS) + list(WASTE_COLUMNS), start=1):
        width = max([len(str(name))]
                    + [len(str(row[i - 1])) for row in rows[1:]] or [0])
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = \
            min(max(width + 2, 10), 40)
    wb.save(path)
    return path


def csv_filename(date: str) -> str:
    return f"1С выгрузка {str(date).replace('/', '.')}.csv"


def xlsx_filename(date: str) -> str:
    return f"1С выгрузка {str(date).replace('/', '.')}.xlsx"


# ─────────────────── полная выгрузка в JSON (всё, что в Excel) ───────────────────
# CSV/XLSX выше — плоская таблица для загрузки БЕЗ кода. JSON ниже — ПОЛНЫЙ
# слепок Excel-отчёта (оба листа) плюс справочники, под собственный код в 1С.
# Числа тут настоящие (float), а не строки с запятой: `ЧтениеJSON` в 8.3.27
# разбирает их сам, а точку-разделитель в 1С обработать проще запятой.

FORMAT_VERSION = 1


def _f(value):
    """Число → float (округл. до 2) или None. Пустое/нечисло → None."""
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _pairs(rows) -> List[dict]:
    """[(описание, часы)] → [{"описание":…, "часы":…}] для JSON-блоков."""
    out = []
    for row in rows or []:
        label = row[0]
        hours = row[1] if len(row) > 1 else None
        item = {"описание": label, "часы": _f(hours)}
        if len(row) > 2:                      # downtime_by_reason: +оператор_работал
            item["оператор_работал"] = bool(row[2])
        out.append(item)
    return out


def _discrepancy(report) -> str:
    """Текст расхождения по станку/оператору — слово в слово как в Excel.

    Правило живёт в shift_report.discrepancy_flags: раньше здесь забывали
    про переработку оператора, выросшую из времени работы станка по счётчику
    (правило 03.09.2026), и 1С получала «!!! РАСХОЖДЕНИЕ: оператор +0,69 ч»
    там, где бланк молчал.

    Число печатает _fmt_h — тем же стилем, что и бланк («−1,9 ч», а не
    «−1,90 ч»): тест паритета сверяет строки посимвольно, и лишние нули
    разъезжают два файла (прогон 28.09.2026, ст.98 за 10.09).
    """
    return discrepancy_text(report, _fmt_h)


def _subs_fio(report) -> str:
    """ФИО на подменах (без наладчика) — та же функция, что у колонки Excel.

    В Excel с 22.09.2026 своего оператора тоже перечисляют (ст.98 «Белякова»);
    здесь его не было, и в 1С уезжала пустая клетка при той же смене.
    """
    return submen_fio(report)


def _line_dict(report, line) -> dict:
    """Одна строка ассортимента станка со всеми полями листа-таблицы Книга1."""
    from . import machine_rates
    stale = bool(getattr(report, "nothing_produced", False))
    cs = None if stale else getattr(line, "counter_start", None)
    ce = None if stale else getattr(line, "counter_end", None)
    t_line = machine_rates.work_hours(report.machine, cs, ce)
    return {
        "ассортимент": getattr(line, "assortment", None),
        "клиент": getattr(line, "client", None),
        "номер_заявки": getattr(line, "order_no", None),
        "кол_полотен": getattr(line, "panels", None),
        "счётчик_начало": _f(cs),
        "счётчик_конец": _f(ce),
        "выпуск_фактический": _f(getattr(line, "output", None) if not stale
                                  else None),
        "время_ч": _f(t_line),
    }


def remains_dict(block) -> dict:
    """Блок остатков/отходов станка для 1С. None → пустой словарь.

    Килограммы отдаём числами, незаполненные виды — null: 1С должна видеть
    разницу между «сдал 0 кг» и «мастер поле не заполнил».
    """
    from .remains_report import COUNTER_FIELDS, WASTE_KEYS, WASTE_LABELS
    if block is None:
        return {}
    counters = {}
    for key, _label, has_of in COUNTER_FIELDS:
        item = {"значение": _f(block.counter(key))}
        if has_of:
            item["из"] = _f(block.counter_of(key))
        counters[key] = item
    return {
        "замер": block.moment or "",
        "показания": counters,
        "отходы_кг": {key: _f(block.waste.get(key)) for key in WASTE_KEYS},
        "отходы_всего_кг": _f(block.waste_total) if block.has_waste else None,
        "причина_прочих": block.other_reason or "",
        "состояние_машины_и_полотна": block.state or "",
        "виды_отходов_подписи": dict(WASTE_LABELS),
        "источники_сообщения": list(block.sources),
    }


def machine_dict(report, remains=None) -> dict:
    """Полный слепок станка: оба листа Excel + всё, что нужно для справочников."""
    from . import machine_rates
    has_op = report.has_operator
    unknown = bool(report.balance_unknown)
    block = remains.by_machine(report.machine) if remains is not None else None
    return {
        # ——— остатки сырья и сдаваемые отходы (лист «Остатки») ———
        "остатки_и_отходы": remains_dict(block),
        # ——— шапка / ключи справочников ———
        "дата": report.date or "",
        "станок": report.machine or "",
        "марка": report.brand or "",
        "оператор": report.operator or "",
        "норма_T_отходов_в_минуту": _f(machine_rates.get_rate(report.machine)),
        "нет_разбора": bool(getattr(report, "not_parsed", False)),
        "кол_срезов": (None if getattr(report, "nothing_produced", False)
                        else report.cuts),
        # Колонка P бланка («Доп. работы (шт, кг)») — дословные слова мастера
        # о деле вне своей машины (приказ 29.09.2026). Часы при этом остаются в
        # блоке оператора: ключ называет работу, а время не перекладывает.
        "доп_работы": extra_works(report),
        # ——— строки ассортимента (лист-таблица Книга1) ———
        "строки": [_line_dict(report, ln) for ln in (report.lines or [])],
        # ——— блок «Работа оператора всего» (вертикальный лист) ———
        "оператор_блок": {
            "часы": _f(report.operator_hours) if has_op else None,
            "норма": _f(report.operator_norm) if has_op else None,
            "недостаток": None if unknown else _f(report.operator_shortfall),
            "превышение": None if unknown else _f(report.operator_excess),
            "операции": _pairs(report.operator_work_by_reason()),
        },
        # ——— блок «Работа привлекаемого персонала всего» ———
        "привлечённый_персонал": {
            "часы_всего": _f(report.attracted_total),
            "подмены_часы": _f(report.attracted_subs_total),
            # То, что стоит в колонке «подмены, ч» бланка: ОКНО простоя станка
            # на подмене, а не сумма часов по людям. Без этого ключа Excel-
            # колонка в «полной выгрузке» отсутствовала вовсе.
            "подмены_окно_станка_часы": _f(submen_hours(report)),
            # Пусто по приказу владельца 30.09.2026 («не заполняй колонку» про
            # «Кол-во подмен»). Excel-колонка S в бланке молчит по этому же
            # приказу — две выгрузки одного дня не должны разъезжаться.
            "кол_подмен": None,
            "фио_на_подменах": _subs_fio(report),
            "строки": _pairs(report.attracted_by_person()),
        },
        # ——— блок «Работа машины всего» ———
        "машина_блок": {
            "часы_работы": _f(report.work_hours),
            "недостаток": _f(report.machine_shortfall),
            "превышение": _f(report.machine_excess),
            "необъяснённый_простой": _f(report.unexplained_downtime),
            "инженерная_служба_всю_смену": bool(report.engineering_all_shift),
            "простои": _pairs(report.downtime_by_reason()),
        },
        # ——— итоги для человека ———
        "расхождение": _discrepancy(report),
        "примечание": _note(report),
    }


def _catalogs(reports: List) -> dict:
    """Справочники: уникальные станки (марка, норма T) и операторы/люди."""
    from . import machine_rates
    machines: dict = {}
    people: List[str] = []

    def add_person(name) -> None:
        n = (name or "").strip()
        if n and n not in people:
            people.append(n)

    for r in reports:
        num = str(r.machine or "").strip()
        if num and num not in machines:
            machines[num] = {
                "код": num,
                "марка": r.brand or "",
                "норма_T_отходов_в_минуту": _f(machine_rates.get_rate(r.machine)),
            }
        add_person(r.operator)
        add_person(getattr(r, "operator2", None))
        try:
            for s in r.attracted_all():
                add_person(getattr(s, "performer", None))
        except Exception:
            pass
    return {
        "станки": list(machines.values()),
        "операторы": [{"фио": p} for p in people],
    }


def _waste_totals(remains) -> dict:
    """Итоги по видам отходов за смену + всего, кг."""
    from .remains_report import WASTE_KEYS, WASTE_LABELS
    if remains is None:
        return {}
    totals = remains.totals()
    return {
        "по_видам": {key: _f(totals.get(key)) for key in WASTE_KEYS},
        "по_видам_подписи": {key: WASTE_LABELS[key] for key in WASTE_KEYS},
        "всего_кг": _f(remains.waste_total),
        "станков_с_отходами": sum(1 for m in remains.machines if m.has_waste),
        "замечания": list(remains.warnings or []),
    }


def build_document(reports: List, date: Optional[str] = None,
                   remains=None) -> dict:
    """Верхний объект JSON-выгрузки: метаданные + справочники + станки.

    remains — DayRemains смены: попадает и в каждый станок
    («остатки_и_отходы»), и отдельным итогом «сдаваемые_отходы_итого».
    """
    if date is None and reports:
        date = reports[0].date
    doc = {
        "выгрузка": "Отчёт производства за смену",
        "дата": date or "",
        "версия_формата": FORMAT_VERSION,
        "справочники": _catalogs(reports),
        "станки": [machine_dict(r, remains) for r in reports],
    }
    if remains is not None and remains.ok:
        doc["сдаваемые_отходы_итого"] = _waste_totals(remains)
        # Станки, по которым отходы прислали, а в отчётах их нет: иначе
        # килограммы просто исчезнут из выгрузки.
        known = {str(r.machine or "").strip() for r in reports}
        extra = [{"станок": str(b.machine or ""), "оператор": b.operator or "",
                  "остатки_и_отходы": remains_dict(b)}
                 for b in remains.machines
                 if str(b.machine or "").strip() not in known]
        if extra:
            doc["станки_только_отходы"] = extra
    return doc


def export_json(reports: List, path: str, date: Optional[str] = None,
                remains=None) -> str:
    """Полная выгрузка смены в JSON (UTF-8) — под собственный код в 1С."""
    doc = build_document(reports, date, remains)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return path


def json_filename(date: str) -> str:
    return f"1С выгрузка {str(date).replace('/', '.')}.json"
