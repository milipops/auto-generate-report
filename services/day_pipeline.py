"""Ядро «выбрал дату → получил отчёт за эту смену».

Зачем отдельный модуль, а не всё в обработчике бота.

Данные о сменах **копятся сами** — каждое сообщение из чата ложится в
`data/shifts/<ДД.ММ.ГГГГ>/day.json` (см. services/shift_store.py). Бот в чате
не умеет «сходить в прошлое» и забрать вчерашний день: он видит только то,
что пришло при нём. Поэтому порядок такой:

    сообщения приходят весь день  →  копятся по датам на диске
    пользователь выбирает дату    →  по накопленному собирается отчёт

Раньше вся эта сборка («взять накопленное за дату → один запрос к ИИ →
посчитать → сделать Excel») жила внутри обработчика Telegram. Теперь она
здесь, и поэтому:

* кнопка «📊 Отчёт за день» (последняя смена) и кнопка «📅 Отчёт за дату»
  (любая накопленная) используют один и тот же код — отчёт не может
  разъехаться между двумя кнопками;
* тот же отчёт можно собрать без Telegram вообще:
  `python -m services.day_pipeline 20.08.2026` — это пригодится, если чат
  придётся менять (Мессенджер, веб-страница) — переписывать нужно будет
  только доставку, а не расчёт.

Модуль ничего не знает про aiogram и не отправляет сообщений: он отдаёт
готовый результат, а как его показать — дело вызывающего.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from . import counter_check
from . import machine_rates
from . import shift_store
from .ai_extract import AIExtractError
from .ai_extract import extract_structured as _default_extract
from .daily_report import build_daily_reports
from .remains_report import build_day_remains
from .remains_report import counter_chronology_warnings
from .shift_report import build_shift_reports_from_structured

logger = logging.getLogger(__name__)

# Сколько фотографий смены отдаём модели картинками за один запрос.
# По умолчанию картинок почти нет: текст с фото снимает бесплатный OCR
# (services/photo_ocr.py), а картинкой уходит только нечитаемое.
PHOTO_LIMIT = 12


def normalize_date(value: str) -> Optional[str]:
    """Привести дату к «ДД.ММ.ГГГГ» или вернуть None, если это не дата.

    Пользователь пишет по-разному: «20.08.2026», «20.8.26», «20/08/2026».
    Всё, что не превращается в реальную дату, отсекаем здесь — иначе именем
    каталога станет мусор из сообщения.
    """
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime(shift_store.DATE_FMT)
        except ValueError:
            continue
    return None


# ─────────────────────────── какие смены накоплены ───────────────────────────

@dataclass
class DaySummary:
    """Одна накопленная смена — для списка и для кнопки выбора даты."""
    date: str
    messages: int
    photos: int
    machines: List[str] = field(default_factory=list)
    updated_at: Optional[str] = None

    @property
    def button_label(self) -> str:
        """Короткая подпись на кнопке: год не пишем, он и так текущий."""
        short = self.date[:5] if len(self.date) >= 5 else self.date
        return f"{short} · {self.messages} сообщ."

    @property
    def line(self) -> str:
        """Строка для списка смен."""
        out = f"{self.date} — {self.messages} сообщ., {self.photos} фото"
        if self.machines:
            out += ", станки: " + ", ".join(self.machines)
        return out


def summary(date: str) -> DaySummary:
    """Что накоплено за дату (без ИИ и без расчётов — только пересчёт)."""
    msgs, photos = shift_store.day_stats(date)
    return DaySummary(date=date, messages=msgs, photos=photos,
                      machines=shift_store.day_machines(date) if msgs else [],
                      updated_at=shift_store.load_day(date).get("updated_at"))


def available_days(limit: Optional[int] = None,
                   newest_first: bool = True) -> List[DaySummary]:
    """Смены, за которые есть данные. По умолчанию свежие — первыми.

    Отсюда бот берёт кнопки выбора даты: показывать имеет смысл только те
    дни, по которым реально что-то накопилось.
    """
    rows = [row for row in shift_store.list_days() if row[1]]
    if newest_first:
        rows = list(reversed(rows))
    if limit:
        rows = rows[:limit]
    return [DaySummary(date=date, messages=msgs, photos=photos,
                       machines=shift_store.day_machines(date),
                       updated_at=updated)
            for date, msgs, photos, updated in rows]


def resolve_date(requested: Optional[str] = None) -> Optional[str]:
    """Дата для отчёта: что попросили, иначе последняя смена с данными."""
    if requested:
        return normalize_date(requested)
    return shift_store.latest_day()


# ──────────────────────────── сборка отчёта за дату ───────────────────────────

@dataclass
class DayResult:
    """Готовый результат по смене — всё, что нужно показать пользователю."""
    date: str
    messages: int = 0
    photos: int = 0
    machines: List[str] = field(default_factory=list)
    # Полные отчёты по станкам (счётчик, часы, простои, балансы).
    reports: List = field(default_factory=list)
    # Запасная упрощённая сводка — если ИИ выключен или не справился.
    simple: List = field(default_factory=list)
    # Распознанный на фото текст не про работу: в отчёт не идёт, но показываем.
    foreign_photos: List = field(default_factory=list)
    note: str = ""            # почему нет полного отчёта
    empty: bool = False       # за эту дату ничего не накоплено
    # Пометки о подстановке начального счётчика с прошлой смены (station→текст).
    carry_notes: List[str] = field(default_factory=list)
    # Все замечания к смене: и «ИИ не разобрал станок», и спорная фамилия
    # оператора, и незаписанный журнал счётчиков, и отброшенный станок-призрак.
    ai_warnings: List[str] = field(default_factory=list)
    # Из них только те, где ИИ не отдал данные по станку: пересборка с ИИ
    # исправляет ровно их. По остальным ai_warnings переспрашивать модель
    # бессмысленно (часы уже в отчёте, а причина — диск, фамилия или phantom).
    ai_parse_warnings: List[str] = field(default_factory=list)
    # Остатки сырья и сдаваемые отходы по машинам (DayRemains). Считаются
    # регулярками из тех же сообщений смены, БЕЗ обращения к ИИ.
    remains: Optional[object] = None

    @property
    def ok(self) -> bool:
        """Есть полный отчёт по станкам (значит, будет и Excel)."""
        return bool(self.reports)

    @property
    def has_remains(self) -> bool:
        """По смене прислали блоки «Конец смены … Сдаваемые отходы»."""
        return bool(self.remains is not None and self.remains.ok)


def _ai_enabled_default() -> bool:
    """Включён ли ИИ по настройкам. Без config (тесты, CLI) считаем, что нет.

    Настройки читаем лениво и в try: config требует pydantic-settings, а этот
    модуль должен работать и там, где зависимостей нет.
    """
    try:
        from config.config import settings
        return bool(settings.ai_enabled)
    except Exception:                       # нет config/pydantic — не падаем
        return False


# ─────────────── перенос начального счётчика с прошлой смены ───────────────
# Счётчик станка сквозной (накопительный), станок работает 7/0 без выходных,
# поэтому конец любой прошлой смены = корректное начало следующей. Если
# утреннее показание в переписку не попало, начальное берём из журнала
# конечных показаний за ближайшую предыдущую накопленную смену. Делаем это
# ДЕТЕРМИНИРОВАННО, в коде: сам ИИ числа подставлять не должен.

def _ledger_path() -> str:
    return os.path.join(shift_store.get_data_dir(), "counter_ledger.json")


def _load_ledger() -> dict:
    import json
    try:
        with open(_ledger_path(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _machine_end_counter(machine: dict):
    """Последнее (наибольшее по порядку) конечное показание станка за смену."""
    end = None
    for line in machine.get("lines") or []:
        val = line.get("counter_end")
        if isinstance(val, (int, float)):
            end = val
    return end


def _machine_start_counter(machine: dict):
    """Начальное показание станка за смену (из первой строки со счётчиком)."""
    for line in machine.get("lines") or []:
        val = line.get("counter_start")
        if isinstance(val, (int, float)):
            return val
    return None


def _save_counter_ledger(date: str, data: dict,
                         dropped: Optional[List[str]] = None,
                         result: Optional["DayResult"] = None) -> None:
    """Запомнить конечные показания станков за смену — на будущее (перенос).

    dropped — станки, которых в переписке этого дня не было и разбор по ним
    отброшен (_drop_invented_machines). Их запись за эту дату из журнала
    убирается: иначе призрак завтра подставится началом смены настоящему
    станку. Прогон 26.09.2026: оплаченный разбор родил №151 из размера полотна
    «151,5», журнал получил для него конец смены, а 27.09 №151 работал по-настоящему.
    """
    import json
    machines = data.get("machines") or []
    ledger = _load_ledger()
    changed = False
    for num in dropped or []:
        if (ledger.get(str(num)) or {}).pop(date, None) is not None:
            changed = True
            logger.warning("Журнал счётчиков: убрана запись №%s за %s — "
                           "станка в переписке смены нет", num, date)
    for m in machines:
        num = str(m.get("machine") or "").strip()
        end = _machine_end_counter(m)
        if not num or end is None:
            continue
        ledger.setdefault(num, {})[date] = end
        changed = True
    if not changed:
        return
    try:
        os.makedirs(os.path.dirname(_ledger_path()), exist_ok=True)
        with open(_ledger_path(), "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=1)
    except OSError:
        logger.warning("Не удалось сохранить журнал счётчиков", exc_info=True)
        # Журнал — единственное, откуда завтра берётся начало смены без
        # утреннего показания. Молча его не записать значит через день
        # выдать выпуск с чужого числа, поэтому предупреждение идёт в ответ.
        if result is not None:
            result.ai_warnings.append(
                f"журнал счётчиков за {date} не записан — файл "
                f"{_ledger_path()} недоступен на запись. Сегодняшний отчёт "
                "верный, но на следующей смене начало смены без утреннего "
                "показания может взяться не оттуда, где надо. Проверьте, "
                "свободно ли место на диске и не закрыт ли сайт другим "
                "процессом, и пересоберите смену без ИИ.")


def _prev_end_entry(machine_num: str, date: str):
    """(конечное показание, дата) предыдущей накопленной смены станка.

    Дата нужна, чтобы отличить СОСЕДНЮЮ смену от записи недельной давности:
    сверять начало смены с показанием десятидневной давности нельзя — за это
    время станок наработал больше одной смены (прогон 14.09.2026: ст.98 дал
    ложную тревогу против записи за 10.09).
    """
    ledger = _load_ledger().get(str(machine_num), {})
    target = normalize_date(date) or date
    try:
        target_dt = datetime.strptime(target, shift_store.DATE_FMT)
    except ValueError:
        return None, None
    best_dt = None
    best_val = None
    best_date = None
    for d, val in ledger.items():
        try:
            dt = datetime.strptime(d, shift_store.DATE_FMT)
        except ValueError:
            continue
        if dt < target_dt and (best_dt is None or dt > best_dt):
            best_dt, best_val, best_date = dt, val, d
    return best_val, best_date


def _is_adjacent_day(prev_date: Optional[str], date: str) -> bool:
    """Предыдущая запись журнала — соседний день (не дальше 2 суток назад)."""
    if not prev_date:
        return False
    try:
        a = datetime.strptime(prev_date, shift_store.DATE_FMT)
        b = datetime.strptime(normalize_date(date) or date, shift_store.DATE_FMT)
    except ValueError:
        return False
    return 0 < (b - a).days <= 2


def _prev_end_counter(machine_num: str, date: str):
    """Конечный счётчик станка за ближайшую ПРЕДЫДУЩУЮ накопленную смену."""
    ledger = _load_ledger().get(str(machine_num), {})
    target = normalize_date(date) or date
    try:
        target_dt = datetime.strptime(target, shift_store.DATE_FMT)
    except ValueError:
        return None
    best_dt = None
    best_val = None
    for d, val in ledger.items():
        try:
            dt = datetime.strptime(d, shift_store.DATE_FMT)
        except ValueError:
            continue
        if dt < target_dt and (best_dt is None or dt > best_dt):
            best_dt, best_val = dt, val
    return best_val


def _save_ai_raw(date: str, data: dict) -> None:
    """Положить СЫРУЮ структуру ИИ рядом с накопителем: data/shifts/<дата>/ai_raw.json.

    Зачем: промпт (services/ai_extract.py) — самая хрупкая часть бота, и без
    сохранённого ответа модели нельзя отличить её ошибку от ошибки расчёта.
    Прогон 10.09.2026 пришлось разбирать вслепую: по выходному файлу 1С видно
    лишь итоговые часы, а КАКИЕ события вернула модель — неизвестно.

    Файл перезаписывается на каждом прогоне (последний разбор важнее) и НЕ
    участвует ни в отчёте, ни в выгрузке — это только диагностическая копия.
    Ошибка записи не должна ронять отчёт.
    """
    import json
    if not date:
        return
    try:
        path = os.path.join(shift_store.get_data_dir(), date, "ai_raw.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        logger.info("Сырой разбор ИИ сохранён: %s", path)
    except Exception:                          # архив не должен ломать отчёт
        logger.exception("Не удалось сохранить сырой разбор ИИ за %s", date)


AI_RAW_NAME = "ai_raw.json"


def ai_raw_path(date: str) -> str:
    return os.path.join(shift_store.get_data_dir(), date, AI_RAW_NAME)


def has_saved_raw(date: str) -> bool:
    """Есть ли за дату сохранённый разбор ИИ — то, что можно пересобрать бесплатно."""
    return os.path.isfile(ai_raw_path(date))


def load_saved_raw(date: str) -> Optional[dict]:
    import json
    path = ai_raw_path(date)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        logger.warning("Не удалось прочитать %s", path, exc_info=True)
        return None


def build_day_local(date: str,
                    progress: Optional[Callable[[str], None]] = None) -> DayResult:
    """Пересобрать отчёт за дату из сохранённого ai_raw.json — БЕЗ запроса к ИИ.

    Платить нужно за то, чего ещё нет: разбор переписки модель уже отдавала, и
    он лежит рядом с накопителем. Прогон 25.09.2026 это показала: после
    перекодировки правил все семь сохранённых смен пересчитались бесплатно, а
    расхождение вышло ровно в тех цифрах, которые правилу и подчинялись.

    Оплаченный разбор при этом не перезаписывается (`save_raw=False`): в нём
    ответ модели каким он пришёл, а не каким его увидел текущий код.
    """
    import copy

    normalized = normalize_date(date) or date
    raw = load_saved_raw(normalized)
    if raw is None:
        raise AIExtractError(
            f"За {normalized} нет сохранённого разбора ИИ "
            f"({AI_RAW_NAME}) — впервые смену нужно собрать через ИИ.")

    def replay(text, images=None, **_kwargs):
        return copy.deepcopy(raw)

    return build_day(normalized, extract=replay, ai_enabled=True,
                     progress=progress, save_raw=False)


def _drop_invented_machines(data: dict, known: List[str],
                            result: "DayResult") -> List[str]:
    """Выбросить из разбора станки, которых нет в накопленной переписке.

    Возвращает список отброшенных номеров — по ним чистится журнал счётчиков
    (_save_counter_ledger), иначе призрак завтра станет началом смены настоящего
    станка.

    Прогон 26.09.2026: в теме 251-й мастер пишет размер полотна
    «Днпа 187х6-100,0-151,5» и просит помощь «на 151м». Модель прочитала из
    этого станок и собрала №151 из ЧУЖИХ сообщений — со своим оператором,
    10,09 ч «наладка (весь день)» и чужими показаниями. Подсказка больше не
    выдаёт номеров, которых нет в темах (shift_store.day_machines), но
    оплаченный разбор уже лежит на диске, и бесплатная пересборка проигрывает
    его как есть — чистим здесь.

    Если тем со станком нет вообще, или ХОТЬ ОДНО сообщение пришло без
    заголовка темы, список станков построен догадкой по тексту и отбрасывать
    по нему нельзя: уберём верный станок (прогон 19/20.08.2026 — смена одним
    текстом, где №251 и №321 названы только в самом тексте). Тогда не чистим
    ничего: `known` для этого случая передают None.
    """
    if not known:
        return []
    allowed = {machine_rates._norm_key(num) for num in known}
    kept: List[dict] = []
    dropped: List[str] = []
    for m in data.get("machines") or []:
        num = str(m.get("machine") or "").strip()
        if num and machine_rates._norm_key(num) not in allowed:
            dropped.append(num)
            continue
        kept.append(m)
    if not dropped:
        return []
    data["machines"] = kept
    for num in dropped:
        result.ai_warnings.append(
            f"станок №{num}: в переписке за смену его нет — ни темы с таким "
            f"номером, ни текста, где мастер называет станок. Разбор ИИ по "
            f"нему отброшен, цифры в отчёт не попали. Если смена на этом "
            f"станке была — пришлите сообщение с заголовком «{num} машина».")
        logger.warning("Станок №%s не из переписки смены — разбор отброшен", num)
    return dropped


def _attach_section_texts(data: dict, date: str) -> None:
    """Добавить каждому станку его переписку: m["section_text"].

    Нужно детерминированному слою: часть того, что решает часы, мастер пишет
    ОТДЕЛЬНЫМ сообщением, и в «причине» события этого уже нет. Прогон
    20.09.2026, ст.151: обед «12:45-13:30- обед», а следом другим сообщением
    «Взял 151 машину под наблюдение на время обеда» — станок вели, простоя не
    было, но без текста раздела обед так и считался бы 0,75 ч простоя.
    """
    cache: dict = {}
    for m in data.get("machines") or []:
        num = str(m.get("machine") or "").strip()
        if not num:
            continue
        if num not in cache:
            cache[num] = _machine_text(num, date)
        if cache[num]:
            m["section_text"] = cache[num]


def _validate_counters(data: dict, date: str, result: "DayResult") -> None:
    """Проверить показания счётчика на опечатки мастера (см. counter_check).

    Идёт ПОСЛЕ переноса с прошлой смены и ДО расчёта: иначе одна опечатка
    разъезжается по всему отчёту и утекает в counter_ledger.json.

    Прогон 14.09.2026, ст.321: мастер написал «Общ сч 1200807» вместо 120807,
    дельта +1 080 054 узла, отчёт выдал «работа машины 3000,15 ч из 12 часов»
    и «!!! РАСХОЖДЕНИЕ: станок +2996,32 ч сверх 12 ч». На ст.151 опечатка была
    в начальном показании («Общ.сч. 7760351» вместо 760351) — работа 0 ч и
    «!!! РАСХОЖДЕНИЕ: станок -11,25 ч». В эталоне пользователя за 14.09 там
    стоит 0,2 ч и 8,7 ч.

    Правим только когда есть доказательство (мода по длине числа среди всех
    показаний смены) и ВСЕГДА сообщаем словами: молча подменять показания
    нельзя, мастер может не заметить, что цифра изменилась.
    """
    text_by_machine: dict = {}
    for m in data.get("machines") or []:
        num = str(m.get("machine") or "").strip()
        lines = m.get("lines") or []
        if not num or not lines:
            continue
        if num not in text_by_machine:
            # Текст своего раздела: в файле секции разложены по станкам
            # (machine/thread в накопителе), поэтому берём сообщения станка.
            text_by_machine[num] = _machine_text(num, date)
        prev_end, prev_date = _prev_end_entry(num, date)
        notes = counter_check.fix_machine_counters(
            num, lines, text=text_by_machine[num], prev_end=prev_end,
            prev_is_adjacent=_is_adjacent_day(prev_date, date),
            operator=(m.get("operator") or "").strip() or None)
        result.carry_notes.extend(notes)


# Подпись автора в выгрузке чата: «Нина Владимировна Дроздова, 07:44:…».
_AUTHOR_SIG_RE = re.compile(
    r"([А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё.]+){0,2}),\s*(\d\d[:.]\d\d)",
    re.MULTILINE)

# Маркеры РАБОТЫ ОТ ПЕРВОГО ЛИЦА: так пишет оператор, который стоит у станка
# («приступаю к навивке», «меняю шпули», «иду за сырьём», «заполняю журнал»).
# Мастер смены, наоборот, пишет сводку безлично: «Общ сч 21431800, Ср. 750,
# Всего 8 срезов, Навито 160 шп», «Состояние полотен в 2 провязах чинка
# помечено белой нитью».
#
# Проверено на прогоне 15.09.2026: Дроздова (автор сводки по ст.98) — 0
# маркеров, Демидов (оператор ст.321) — 6, Арташина (оператор ст.251) — 18.
_FIRST_PERSON_RE = re.compile(
    r"\b(?:я|мной|меня|мне|приступа\w*|меняю|подставля\w*|ид[уё]|жду|ждём|"
    r"получи\w*|принял\w*|принима\w*|продолжа\w*|заполня\w*|убира\w*|"
    r"остановил\w*|выпустил\w*|пригласил\w*|сделал\w*|проверил\w*|"
    r"заменил\w*|навива\w*|развива\w*|заканчива\w*|ставлю|пошёл|пошла|"
    r"вернул\w*|заправил\w*|намота\w*|закончил\w*|срезал\w*|чиню|чинк\w*)",
    re.IGNORECASE)


def _surname_of(full_name: str) -> str:
    """Фамилия из ФИО: «Нина Владимировна Дроздова» → «Дроздова».

    Инициалы фамилией НЕ считаются: в выгрузке чата мастера пишут «Белякова С.»,
    и последнее слово там — «С». Берём последнее слово длиннее одной буквы,
    иначе само поле целиком (для одиночной фамилии «Камбаров»).
    """
    parts = [p.strip(" .,") for p in str(full_name or "").split() if p.strip(" .,")]
    if not parts:
        return ""
    for p in reversed(parts):
        if len(p) > 1:
            return p
    return parts[-1]


def _check_operator_not_author_only(data: dict, date: str,
                                    result: "DayResult") -> None:
    """Фамилия оператора встречается ТОЛЬКО как подпись автора — предупредить.

    Прогон 15.09.2026, ст.98: в переписке фамилии оператора НЕТ вовсе, есть
    только авторы записей («Юлия Черныш, 06:57» — замер счётчика, «Нина
    Владимировна Дроздова, 07:44» — сводка по станку). Модель поставила
    operator = «Дроздова», и в таблицу отчёта производства уехала чужая
    фамилия: у пользователя оператором ст.98 за этот день стоит Симонова
    (операторы на 98-й чередуются по двое суток, Белякова/Симонова, и их
    фамилии в чате может не быть).

    Признак: фамилия оператора найдена в тексте станка ТОЛЬКО внутри подписи
    автора сообщения и нигде больше (не в заголовке «98 машина, Симонова», не в
    «оператор …», не в отчёте о своей работе). Это не приговор — иногда мастер
    смены сам ведёт станок, — поэтому фамилию не стираем, а говорим словами.

    Молчит, когда фамилии оператора нет вовсе или она встречается и вне
    подписей (тогда это настоящий оператор).
    """
    for m in data.get("machines") or []:
        num = str(m.get("machine") or "").strip()
        operator = str(m.get("operator") or "").strip()
        if not num or not operator:
            continue
        surname = _surname_of(operator)
        if len(surname) < 4:
            continue
        text = _machine_text(num, date)
        if not text:
            continue
        all_hits = len(re.findall(re.escape(surname), text, re.IGNORECASE))
        if not all_hits:
            # В секции ЭТОГО станка фамилии нет. Прежде чем тревожиться, ищем
            # её в тексте ВСЕГО дня: мастера часто называют оператора в общем
            # блоке, который при выгрузке прилипает к другой секции.
            # Прогон 16.09.2026, ст.151: раздел подписан «Олег, 07:02», а фамилия
            # «Петровский О» названа в блоке «Данные из чата» про плавающий обед,
            # и тот блок лежит внутри секции ст.98. Без поиска по дню бот
            # печатал ложное «оператор не упоминается ни разу».
            nums = getattr(result, "machines", None) or []
            full = "\n".join(_machine_text(n, date) for n in nums) if nums \
                else text
            if full and full != text:
                hits_full = len(re.findall(re.escape(surname), full,
                                           re.IGNORECASE))
                sig_full = 0
                for sig in _AUTHOR_SIG_RE.finditer(full):
                    if re.search(re.escape(surname), sig.group(1), re.IGNORECASE):
                        sig_full += 1
                if hits_full and hits_full > sig_full:
                    # Фамилия названа в общем блоке (распоряжение, плавающий
                    # обед, «ответственное лицо за станок») — тревога не нужна.
                    continue
                text = full
                all_hits = hits_full
            if not all_hits:
                # Фамилии нет ни в тексте станка, ни во всём дне — модель взяла
                # её из своего знания. Предупреждаем: так в таблицу попадает
                # имя, которого в переписке нет вовсе (прогон 15.09.2026, ст.98:
                # «Дроздова» вместо Симоновой).
                result.ai_warnings.append(
                    f"№{num}: оператор «{operator}» в переписке по этому станку "
                    f"не упоминается ни разу — проверьте, тот ли это человек")
                continue
        # Разбиваем текст на блоки по подписям и считаем, писал ли ЭТОТ автор
        # о своей работе от первого лица. Автор сводки таких маркеров не имеет.
        author_hits = 0
        marks = [(m.start(), m.group(1).strip(), m.group(2))
                 for m in _AUTHOR_SIG_RE.finditer(text)]
        marks.append((len(text), None, None))
        for i in range(len(marks) - 1):
            pos, who, _tm = marks[i]
            if not who or not re.search(re.escape(surname), who, re.IGNORECASE):
                continue
            body = text[pos:marks[i + 1][0]]
            if _FIRST_PERSON_RE.search(body):
                # Человек ведёт станок от первого лица — это настоящий оператор.
                author_hits = 0
                break
            author_hits += 1
        if author_hits and author_hits >= all_hits:
            result.ai_warnings.append(
                f"№{num}: «{operator}» встречается только как ПОДПИСЬ АВТОРА "
                f"сообщений ({author_hits} из {all_hits}) — возможно, это мастер "
                f"смены или автор сводки, а не оператор станка. Проверьте "
                f"фамилию в отчёте производства")


def _machine_text(machine_num: str, date: str) -> str:
    """Переписка ровно ОДНОГО станка за дату. Пустая строка, если выделить нельзя.

    Пустая строка — это не «нет данных», а «не уверены»: rule 1 (мода по длине
    числа) тогда не применяется вовсе, и это правильно. Смешивать показания
    нескольких станков в одну моду КАТЕГОРИЧЕСКИ нельзя: у ст.98 счётчик
    восьмизначный (21420800), а у ст.151 шестизначный (760449) — на общем
    тексте мода вышла 6 цифр, и валидатор «исправил» честные показания 98-й
    на числа 151-й. Проверено на прогоне 14.09.2026.

    Поэтому берём только то, что точно относится к станку:
      * сообщения с machine == ключ (файл разложен по разделам, Яндекс
        Мессенджер с заголовком темы);
      * разделы «–N машина», найденные в тексте (split_sections) — если файл
        сохранён одним сообщением без machine, но заголовки в нём есть.
    Если после этого в тексте остались чужие номера станков — возвращаем "".
    """
    try:
        msgs = shift_store.load_day(normalize_date(date) or date)["messages"]
    except Exception:
        return ""
    key = str(machine_num).strip()
    parts = []
    for m in msgs:
        own = str(m.get("machine") or "").strip()
        text = m.get("text") or ""
        if own:
            if own == key and text:
                parts.append(text)
            continue
        # Сообщение без станка: ищем внутри разделы «–N» и берём свой.
        for sec in shift_store.split_sections(text):
            if sec.machine == key:
                parts.append(sec.text)
    if not parts:
        return ""
    joined = "\n".join(parts)
    # Страховка: если в выделенном тексте всё ещё видны ДРУГИЕ станки из
    # справочника, моду считать нельзя — лучше не трогать показания вовсе.
    others = set(shift_store.machine_rates.known_machines()) - {key}
    for other in others:
        if re.search(r"(?<![\w\d])–\s*" + re.escape(other) + r"\b", joined):
            return ""
    return joined


def _has_unattributed_readings(date: str) -> bool:
    """Есть ли за смену показания, которые ни к одному станку не привязать.

    Старые смены (25.08–09.09.2026) легли в базу нарезанными кусками примерно
    по 4000 знаков: продолжение переписки приходит без заголовка «–N машина»,
    и резервная сводка его просто не видит. Молчать нельзя — показание, которое
    сводка считает итоговым, может оказаться дневным.
    """
    try:
        msgs = shift_store.load_day(normalize_date(date) or date)["messages"]
    except Exception:
        return False
    for m in msgs:
        if str(m.get("machine") or "").strip():
            continue
        # Хвост без заголовка «–N машина» — это продолжение дня, нарезанного
        # по длине сообщения: 31.08.2026 вечерние 260044 и 261740 251-й лежат
        # именно в таком куске, и резервной сводке они недоступны.
        for sec in shift_store.split_sections(m.get("text") or ""):
            if sec.machine:
                continue
            if re.search(r"сч\w*[\s:№–—._-]*\d{3,}", sec.text or "", re.I):
                return True
    return False


def _fallback_reports(text: str, machines: List[str], date: str) -> List:
    """Регуляточная сводка на случай, когда ИИ не дал отчёта.

    Считаем по станкам, а не одним куском по всему дню: в выгрузке
    мессенджера каждая строка начинается с «Автор, время:», и заголовок
    станка между ними не находится. Прогон 24.09.2026 показал исход у
    прежнего способа — один блок «станок не определён», где в роли общего
    счётчика оказалось первое попавшееся число (3083). Выдуманные цифры в
    резервном пути так же недопустимы, как в основном.

    Если тексты станков не разложены (файл без тем и без заголовков),
    отдаём прежнюю сводку одним блоком — молча оставить смену без ответа
    хуже, чем сводка с предупреждением «номер станка не определён».
    """
    out = []
    partial = _has_unattributed_readings(date)
    for num in machines:
        own = _machine_text(num, date)
        if own:
            reports = build_daily_reports(own, machine_hint=num)
            if partial:
                for rep in reports:
                    rep.warnings.append(
                        f"Часть переписки за смену пришла кусками без номера "
                        f"станка №{num}, эти строки сводка не разбирает: показание "
                        f"выше — последнее из надёжно принадлежащих станку, а не "
                        f"обязательно вечернее. Сверьте с листом «Остатки»")
            out.extend(reports)
    return out or build_daily_reports(text)


def _single_reading_needs_carry(num: str, lines: List[dict], date: str) -> bool:
    """Одно показание за смену, записанное и в начало, и в конец.

    Прогон 26.09.2026, ст.98: в теме счётчик упомянут один раз — «Общ. Сч
    21563900». Модель поставила его и в начало, и в конец, и отчёт выдал
    «выпуск 0 / работа 0 ч / простой 12 ч» по станку, который закрыл срез
    («1450 из 1450») и дал 7 срезов. Нулевой выпуск здесь — не факт, а
    отсутствие данных: мастер не записал утреннее показание.

    Перенос с прошлой смены разрешаем только по фактам, выдумывать число
    здесь нельзя:
      * показаний за смену РОВНО ОДНО различное значение (иначе разбивку по
        ассортиментам трогать нечего);
      * прошлая смена станка — соседняя (не недельной давности);
      * счётчик вырос (новое показание больше прежнего) и рост влезает в
        физический предел смены по норме станка.
    Нет нормы по станку — не переносим: доказать предел нечем, честнее
    оставить 0.
    """
    values = set()
    for ln in lines:
        for key in ("counter_start", "counter_end"):
            val = ln.get(key)
            if isinstance(val, (int, float)):
                values.add(float(val))
    if len(values) != 1:
        return False
    end = values.pop()
    prev_end, prev_date = _prev_end_entry(num, date)
    if prev_end is None or not _is_adjacent_day(prev_date, date):
        return False
    try:
        growth = float(end) - float(prev_end)
    except (TypeError, ValueError):
        return False
    limit = counter_check.max_output(num)
    return 0 < growth <= (limit or 0.0)


def _carry_over_counters(data: dict, date: str, result: "DayResult") -> None:
    """Заполнить пропущенное начальное показание счётчика с прошлой смены.

    Срабатывает ТОЛЬКО когда утреннего показания в переписке нет (start пуст),
    а конечное есть — иначе не трогаем то, что дал ИИ. Подстановку показываем
    пользователю словами: если перенос неверен, он дошлёт настоящее число.

    Второй случай — единственное показание, записанное и в начало, и в конец
    (см. _single_reading_needs_carry): «выпуск 0» по такой паре выдуман, и
    станок, проработавший смену, ложится в отчёт простаивавшим 12 часов.
    """
    for m in data.get("machines") or []:
        num = str(m.get("machine") or "").strip()
        lines = m.get("lines") or []
        if not num or not lines:
            continue
        start = _machine_start_counter(m)
        end = _machine_end_counter(m)
        if start is not None and end is not None and start == end \
                and _single_reading_needs_carry(num, lines, date):
            for ln in lines:
                if ln.get("counter_start") is not None:
                    ln["counter_start"] = None
                    break
            start = None
            result.carry_notes.append(
                f"станок {num}: показание {int(end)} записано в переписке "
                f"единственным — и в начало, и в конец смены, утреннего "
                f"показания нет")
        if start is not None or end is None:
            continue                          # старт есть или считать нечего
        prev = _prev_end_counter(num, date)
        if prev is None:
            continue                          # переносить неоткуда
        # Первую строку станка «открываем» перенесённым показанием.
        first = lines[0]
        first["counter_start"] = prev
        result.carry_notes.append(
            f"станок {num}: начальное показание в переписке не найдено, взято "
            f"с прошлой смены ({prev}). Если неверно — пришлите утреннее "
            f"показание счётчика {num}.")
        logger.info("Перенос счётчика %s: start=%s (с прошлой смены)", num, prev)


def build_day(date: str,
              extract: Optional[Callable] = None,
              ai_enabled: Optional[bool] = None,
              photo_limit: int = PHOTO_LIMIT,
              progress: Optional[Callable[[str], None]] = None,
              save_raw: bool = True) -> DayResult:
    """Собрать отчёт по накопленному за дату. Один вызов ИИ, дальше — код.

    Функция синхронная (запрос к ИИ идёт через urllib), поэтому в боте её
    надо звать через `asyncio.to_thread`, иначе бот «замрёт» на время
    запроса.

    extract — чем разбирать переписку; по умолчанию настоящий клиент ИИ.
    Параметром сделано ради тестов и ради возможности подменить модель, не
    трогая расчёт.

    progress — куда сообщать текущий шаг сборки (сайт показывает её человеку:
    станок за станком это минуты молчания, и без шагов сборка выглядит
    зависшей). Старые вызовы без него работают как раньше.

    save_raw=False — не трогать ai_raw.json. Нужно бесплатной пересборке
    (`build_day_local`): разбор уже лежит на диске, и перезаписывать оплаченный
    оригинал выводом текущего кода смысла нет.
    """
    def say(text: str) -> None:
        if progress:
            try:
                progress(text)
            except Exception:                   # прогресс не должен ломать отчёт
                logger.debug("Не удалось сообщить прогресс", exc_info=True)

    normalized = normalize_date(date) or date
    msgs, photos = shift_store.day_stats(normalized)
    if not msgs:
        return DayResult(date=normalized, empty=True)

    result = DayResult(date=normalized, messages=msgs, photos=photos,
                       machines=shift_store.day_machines(normalized))
    say(f"читаю накопленное за смену: сообщений {msgs}, фото {photos}, "
        f"станков {len(result.machines)}")
    text = shift_store.day_text(normalized)
    images = shift_store.day_photos(normalized, limit=photo_limit)
    result.foreign_photos = shift_store.day_foreign_photos(normalized)

    if ai_enabled is None:
        ai_enabled = _ai_enabled_default()

    data = None
    if not ai_enabled:
        result.note = ("⚠️ ИИ выключен (AI_ENABLED=false) — подробный отчёт по "
                       "счётчикам и Excel собрать не могу.")
    else:
        try:
            say("разбираю переписку через ИИ")
            fn = extract or _default_extract
            data = (fn(text, images, progress=progress) if progress
                    else fn(text, images))
        except AIExtractError as e:
            logger.warning("ИИ-разбор смены %s не удался: %s", normalized, e)
            result.note = ("⚠️ Не удалось разобрать переписку через ИИ. "
                           f"Причина: {e}")

    if data is not None:
        say("считаю часы, выпуск и балансы")
        if not data.get("date"):
            # Дату берём из накопителя, а не из догадки модели: каталог смены
            # — единственный надёжный источник, по нему данные и лежат.
            data["date"] = normalized
        # Сырой ответ модели — на диск, для отладки промпта (см. _save_ai_raw).
        if save_raw:
            _save_ai_raw(normalized, data)
        # Станки, которых в переписке нет, — из отчёта вон (см.
        # _drop_invented_machines): после сохранения сырого разбора, чтобы
        # оплаченный ответ модели оставался как пришёл. Сравниваем с ТОЧНЫМИ
        # заголовками тем — `day_topic_tags` сам вернёт None, если хоть одно
        # сообщение пришло без тега и список построен догадкой.
        dropped = _drop_invented_machines(
            data, shift_store.day_topic_tags(normalized), result)
        # Переписку станка кладём ВНУТРЬ структуры: детерминированный слой
        # должен видеть слова мастера, а не только то, что модель сочла
        # «причиной» события. Идёт после _save_ai_raw, чтобы ai_raw.json
        # оставался чистым ответом модели (тексты и так лежат в day.json).
        _attach_section_texts(data, normalized)
        # Перенос счётчика с прошлой смены, если утреннего показания нет.
        _carry_over_counters(data, normalized, result)
        # Проверка показаний на опечатки мастера — ДО расчёта отчёта.
        _validate_counters(data, normalized, result)
        # Проверка, что оператор — не просто автор сводки по станку.
        _check_operator_not_author_only(data, normalized, result)
        result.reports = build_shift_reports_from_structured(data)
        # Предупреждения разбора (упавший или пустой ответ по станку) молчать
        # нельзя: иначе потерянный станок выглядит как простаивавший.
        for w in data.get("warnings") or []:
            if w not in result.ai_parse_warnings:
                result.ai_parse_warnings.append(str(w))
            if w not in result.ai_warnings:
                result.ai_warnings.append(str(w))
        # Запоминаем конечные показания станков — пригодятся завтра. Отброшенных
        # станков в журнале быть не должно (см. _save_counter_ledger).
        _save_counter_ledger(normalized, data, dropped, result)

    if not result.reports:
        # Смена не должна остаться совсем без ответа: отдаём хотя бы
        # регулярочную сводку по накопленному тексту.
        result.simple = _fallback_reports(text, result.machines, normalized)

    # Остатки сырья и сдаваемые отходы. Считаются ВСЕГДА и БЕЗ ИИ: блок
    # мастера шаблонный, читается регулярками, и учёт отходов не должен
    # зависеть ни от доступности модели, ни от того, собрался ли полный отчёт.
    try:
        result.remains = build_day_remains(
            normalized,
            messages=shift_store.load_day(normalized).get("messages"),
            reports=result.reports)
    except Exception:
        logger.exception("Не удалось собрать остатки и отходы за %s", normalized)
    if result.remains is not None:
        # Общий счётчик сверяем с хронологией сохранённых смен (правило и
        # оговорки — в remains_report.counter_chronology_warnings). Отдельно от
        # сборки остатков: сбой чтения накопителя не должен ронять сами остатки.
        try:
            result.remains.warnings.extend(
                counter_chronology_warnings(normalized, result.remains))
        except Exception:
            logger.exception("Не удалось сверить счётчики со сменами за %s",
                             normalized)
    return result


# ──────────────────────────────── выгрузка ────────────────────────────────

def xlsx_filename(date: str) -> str:
    """Имя файла выгрузки — ровно как называет свои файлы пользователь."""
    return f"Отчет производства за смену {date.replace('/', '.')}.xlsx"


def export_xlsx(result: DayResult, path: str) -> str:
    """Выгрузить отчёты смены в мой бланк Excel. Возвращает путь к файлу."""
    # Импорт внутри функции: без openpyxl пакет подставляет заглушку с
    # понятным текстом ошибки, и бот при этом остаётся работоспособным.
    from . import export_shift_reports
    export_shift_reports(result.reports, path, date=result.date,
                         remains=result.remains)
    return path


def onec_filename(date: str) -> str:
    """Имя файла-выгрузки для 1С. По умолчанию — ПОЛНЫЙ JSON (всё, что в Excel).

    JSON основной, потому что пользователь сам пишет код в 1С (учебная версия,
    конфигуратор открыт), а `ЧтениеJSON` в 8.3.27 разбирает вложенные объекты —
    так в 1С попадают ВСЕ поля обоих листов и справочники, а не только плоские
    11 колонок CSV.
    """
    from .onec_export import json_filename
    return json_filename(date)


def export_onec(result: DayResult, path: str) -> str:
    """Выгрузка смены для загрузки в 1С. Формат — по расширению пути.

    .json  → ПОЛНЫЙ слепок Excel-отчёта (оба листа) + справочники — под
             собственный код чтения в 1С (основной формат).
    .xlsx  → плоская таблица книгой Excel (штатная загрузка без кода).
    иначе  → плоский CSV (UTF-8 с BOM, разделитель «;»).

    В выгрузку идёт ПОЛНЫЙ парк — как в бланке смены, — чтобы в 1С попали и
    станки без данных нулевыми строками с пометкой.
    """
    from .onec_export import export_csv, export_json, export_xlsx as _xlsx
    from .shift_report import fill_park_machines
    reports = fill_park_machines(result.reports, result.date)
    low = path.lower()
    if low.endswith(".json"):
        return export_json(reports, path, date=result.date,
                           remains=result.remains)
    if low.endswith(".xlsx"):
        return _xlsx(reports, path, date=result.date, remains=result.remains)
    return export_csv(reports, path, remains=result.remains)


# ──────────────────────── сводный файл за несколько смен ──────────────────────

def build_days(dates: Optional[List[str]] = None,
               extract: Optional[Callable] = None,
               ai_enabled: Optional[bool] = None,
               photo_limit: int = PHOTO_LIMIT,
               progress: Optional[Callable[[str], None]] = None
               ) -> List[DayResult]:
    """Собрать отчёты за несколько накопленных смен (по вызову ИИ на смену).

    dates=None → все накопленные смены (по возрастанию даты, как в эталонном
    файле пользователя «Отчет_производства_18-25.08.2026.xlsx»). Каждая смена
    — отдельный запрос к ИИ, поэтому в боте вызывать через asyncio.to_thread.

    progress получает строки уже с префиксом даты: без него в сводном сборщике
    было бы непонятно, какой день сейчас разбирается и сколько их осталось.
    """
    if dates is None:
        dates = [d.date for d in available_days(newest_first=False)]
    total = len(dates)
    results: List[DayResult] = []
    for index, date in enumerate(dates, start=1):
        if progress:
            try:
                progress(f"смена {date} — {index} из {total}")
            except Exception:                 # прогресс не должен ронять отчёт
                logger.debug("Не удалось сообщить прогресс", exc_info=True)

        def for_day(text: str, _date: str = date) -> None:
            if progress:
                try:
                    progress(f"{_date}: {text}")
                except Exception:
                    logger.debug("Не удалось сообщить прогресс", exc_info=True)

        results.append(build_day(date, extract=extract, ai_enabled=ai_enabled,
                                 photo_limit=photo_limit,
                                 progress=for_day if progress else None))
    return results


def export_period_xlsx(results: List[DayResult], path: str) -> str:
    """Один Excel, лист на каждую смену с полным отчётом. Возвращает путь.

    Порядок листов — по возрастанию даты (за это отвечает export_day_sheets).
    Смены без полного отчёта (ИИ не справился/выключен) в файл не попадают —
    иначе появится пустой лист.
    """
    from . import export_day_sheets

    def _key(d: str):
        parts = (d or "").replace("/", ".").split(".")
        try:
            return tuple(int(p) for p in reversed(parts))
        except ValueError:
            return (0,)

    days = {}
    remains = {}
    for r in sorted(results, key=lambda x: _key(x.date)):
        if r.reports:
            days[r.date] = r.reports
            if getattr(r, "remains", None) is not None:
                remains[r.date] = r.remains
    if not days:
        raise ValueError("Ни за одну смену не собрался полный отчёт для выгрузки")
    export_day_sheets(days, path, remains=remains or None)
    return path


def period_xlsx_filename(results: List[DayResult]) -> str:
    """Имя сводного файла — «Отчет производства ДД.ММ-ДД.ММ.ГГГГ.xlsx»."""
    dates = sorted((r.date for r in results if r.reports),
                   key=lambda d: tuple(int(p) for p in
                                       reversed(d.replace("/", ".").split("."))
                                       if p.isdigit()) or (0,))
    if not dates:
        return "Отчет производства.xlsx"
    if len(dates) == 1:
        return xlsx_filename(dates[0])
    a, b = dates[0], dates[-1]
    year = b.split(".")[-1] if "." in b else ""
    return f"Отчет производства {a[:5]}-{b[:5]}.{year}.xlsx".replace("..", ".")


# ──────────────────────────── запуск без Telegram ────────────────────────────

def _cli(argv: List[str]) -> int:
    """`python -m services.day_pipeline [дата|--list]` — отчёт из терминала."""
    from .shift_report import format_shift_report
    from .daily_report import format_daily_report

    if not argv or argv[0] in ("-l", "--list"):
        days = available_days()
        if not days:
            print("Накопленных смен нет.")
            return 0
        print("Накопленные смены (свежие сверху):")
        for day in days:
            print("  " + day.line)
        return 0

    date = normalize_date(argv[0])
    if not date:
        print(f"Не похоже на дату: {argv[0]!r}. Формат: 20.08.2026")
        return 2
    result = build_day(date)
    if result.empty:
        print(f"За смену {date} ничего не накоплено.")
        return 1
    print(f"Смена {result.date}: {result.messages} сообщ., "
          f"{result.photos} фото, станки: "
          f"{', '.join(result.machines) or 'не названы'}")
    if result.note:
        print(result.note)
    for report in result.reports:
        print()
        print(format_shift_report(report))
    for report in result.simple:
        print()
        print(format_daily_report(report))
    for index, name, text in result.foreign_photos:
        print(f"\n[постороннее с фото, сообщение №{index}, {name}]: {text}")
    if result.ok and len(argv) > 1:
        path = os.path.abspath(argv[1])
        export_xlsx(result, path)
        print(f"\nExcel: {path}")
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    import sys
    sys.exit(_cli(sys.argv[1:]))
