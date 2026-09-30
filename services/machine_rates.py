"""Справочник норм выработки: узлов в минуту по каждому станку.

Зачем: время фактической работы станка считается НЕ по интервалам сообщений,
а из счётчика и нормы выработки — как в ручном «Отчёте производства за смену»:

    Выпуск, узл.      = счётчик_конечное − счётчик_начальное
    Время работы, ч   = Выпуск / (узлов в минуту) / 60

«Узлов в минуту» — это столбец «кол-во отходов в минуту» из отчёта, величина
для конкретного станка. Значения лежат в machine_rates.json рядом с модулем,
чтобы их можно было править без изменения кода.

Список ключей этого файла значит больше, чем просто «где нормы»: это ещё и
список станков парка (`known_machines`) — по нему отчёт дополняет станки, о
которых в переписке не было ни слова, по нему же номер в тексте отличают от
количества («115 шпулей» — не станок). Поэтому станок с нормой «не установ-
лено» (в JSON — `null`, сейчас это №601) остаётся в парке и в отчёте, просто
время по счётчику по нему не считается.

Второй справочник того же файла — `brands`: марка станка (паспортное имя
оборудования, колонка C отчёта). Её в переписке не называют вовсе, разбор ИИ
возвращает `brand = null`, и колонка C была пустой во всех рядах. Марка не
меняется от смены, поэтому она лежит здесь; разбору ИИ она уступает место
(`get_brand` вызывается только когда своего значения нет).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from runtime_paths import bundle_path

logger = logging.getLogger(__name__)

# Справочник только читается, поэтому он внутри сборки (см. runtime_paths).
_RATES_PATH = bundle_path("services", "machine_rates.json")
_cache: Optional[Dict[str, float]] = None
# Станки парка: ВСЕ ключи справочника, в том числе без нормы (null).
_park: Optional[List[str]] = None
# Марки станков (колонка C): читается тем же кэшем, что и нормы.
_brands: Optional[Dict[str, str]] = None


def _norm_key(machine) -> str:
    """Привести номер станка к строковому ключу справочника."""
    if machine is None:
        return ""
    s = str(machine).strip().lower()
    # «№ 251», «251 машина», «машина 251» → «251»; «14тр» оставляем как есть.
    for junk in ("№", "#", "машина", "станок", "машины", "станка"):
        s = s.replace(junk, "")
    s = s.strip()
    # В переписке машинки пишут «14т», а ключ справочника — «14тр»; без
    # приведения заголовок темы «14т машина» давал бы станок «14», которого
    # нет ни в нормах, ни в отчёте.
    if re.fullmatch(r"\d{2,3}т", s):
        s += "р"
    return s


def _load() -> Dict[str, float]:
    global _cache, _park, _brands
    if _cache is not None:
        return _cache
    rates: Dict[str, float] = {}
    park: List[str] = []
    brands: Dict[str, str] = {}
    try:
        with open(_RATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in (data.get("brands") or {}).items():
            key = _norm_key(k)
            if key and v:
                brands[key] = str(v).strip()
        for k, v in (data.get("rates") or {}).items():
            key = _norm_key(k)
            if not key:
                continue
            park.append(key)
            try:
                rates[key] = float(v)
            except (TypeError, ValueError):
                # «Норма не установлена»: станок в парке и в отчёте остаётся,
                # времени по счётчику у него не будет (get_rate → None).
                logger.info("Станок №%s в справочнике без нормы выработки", key)
    except FileNotFoundError:
        logger.warning("Файл норм %s не найден — расчёт по счётчику отключён",
                       _RATES_PATH)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Не удалось прочитать нормы: %s", e)
    _cache = rates
    _park = sorted(dict.fromkeys(park))
    _brands = brands
    return rates


def get_brand(machine) -> Optional[str]:
    """Марка станка из справочника парка (колонка C), если она там есть.

    Вызывается только когда разбора ИИ по марке молчит: в переписке марку не
    называют, и пустая колонка C читалась как «данных по станку нет».
    """
    _load()
    return _brands.get(_norm_key(machine)) or None


def get_rate(machine) -> Optional[float]:
    """Норма (узлов в минуту) для станка или None, если её нет в справочнике.

    None — это и станок, которого в списке нет вовсе, и станок с нормой «не
    установлена» (№601): для расчёта времени они одинаковы.
    """
    return _load().get(_norm_key(machine))


def known_machines() -> list:
    """Номера станков участка из справочника (нормализованные ключи).

    Нужно, чтобы отличать номер станка от количества: «115 шпулей» — не станок.
    Станки без нормы сюда входят: в парке они есть, просто время по счётчику
    по ним не считается.
    """
    _load()
    return list(_park)


def work_hours(machine, counter_start, counter_end) -> Optional[float]:
    """Время работы станка в часах из показаний счётчика и нормы.

    Возвращает None, если нет нормы по станку или счётчики заданы неполно /
    некорректно (конечное меньше начального).
    """
    rate = get_rate(machine)
    if not rate:
        return None
    try:
        start = float(counter_start)
        end = float(counter_end)
    except (TypeError, ValueError):
        return None
    output = end - start
    if output < 0:
        return None
    return output / rate / 60.0


def reload_rates() -> None:
    """Сбросить кэш (после правки machine_rates.json)."""
    global _cache, _park
    _cache = None
    _park = None
