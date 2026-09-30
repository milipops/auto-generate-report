"""Архив финальных файлов по дням: собрал отчёт один раз — забираю сколько нужно.

Просьба пользователя 04.09.2026, дословно: «можно теперь сделать так чтобы были
команды в боте с помощью которой я бы нажимал ее и выдавались все финальные
файлы по каждым дням скажем так типо история финальных файлов по дням… нажимаю
эту кнопку идет выбор даты и по ней финальные файлы».

Раньше Excel и файл для 1С собирались в `tempfile.TemporaryDirectory()` и после
отправки УДАЛЯЛИСЬ: чтобы получить их снова, приходилось заново гонять смену
через ИИ (деньги и минута ожидания). Теперь готовые файлы остаются на диске:

    data/reports/<ДД.ММ.ГГГГ>/Отчет производства за смену ДД.ММ.ГГГГ.xlsx
    data/reports/<ДД.ММ.ГГГГ>/<выгрузка для 1С>.json
    data/reports/<ДД.ММ.ГГГГ>/meta.json      ← когда собран, какие станки

Выдача из архива ИИ не вызывает вообще — просто отправляет файлы.

**Хранится только последняя версия за дату** (его решение: «Хранить только
последний»). Пересобрали смену — новые файлы легли на место старых.

Порядок работы такой, чтобы неудачная пересборка не съела уже готовые файлы:

    path = slot(date, filename)   # куда писать (каталог создан)
    ...экспорт пишет прямо туда, перезаписывая одноимённый файл...
    finish(date, [filename, ...]) # meta.json + убрать лишнее от прошлых версий

Если экспорт упал, `finish` не вызывается и прошлый файл остаётся на месте.

Модуль на чистой стандартной библиотеке (как services/shift_store.py) — его
можно проверить тестом там, где нет ни aiogram, ни openpyxl.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from runtime_paths import app_path

logger = logging.getLogger(__name__)

DATE_FMT = "%d.%m.%Y"

_DEFAULT_DIR = os.environ.get(
    "FORWORKTEST_REPORTS_DIR", app_path("data", "reports"))
_data_dir = _DEFAULT_DIR

# Служебный файл каталога — в список файлов пользователю не идёт.
META_NAME = "meta.json"

# Как называем файл в чате по расширению.
_KINDS = {".xlsx": "Excel", ".xls": "Excel", ".json": "1С", ".csv": "1С"}


def set_data_dir(path: str) -> None:
    """Сменить каталог архива (используется в тестах)."""
    global _data_dir
    _data_dir = path


def get_data_dir() -> str:
    return _data_dir


def day_dir(date: str) -> str:
    """Каталог архива за смену. Слэши в имени каталога недопустимы."""
    return os.path.join(_data_dir, (date or "").replace("/", "."))


def _files_word(count: int) -> str:
    """«1 файл», «2 файла», «5 файлов» — подпись на кнопке читает человек."""
    tail = count % 100
    if 11 <= tail <= 14:
        return "файлов"
    tail %= 10
    if tail == 1:
        return "файл"
    if 2 <= tail <= 4:
        return "файла"
    return "файлов"


# ───────────────────────────── что лежит в архиве ─────────────────────────────

@dataclass
class ArchivedFile:
    """Один готовый файл: путь на диске, имя для скачивания, размер."""
    path: str
    name: str
    size: int = 0

    @property
    def kind(self) -> str:
        """«Excel», «1С» или «файл» — для подписи в чате."""
        return _KINDS.get(os.path.splitext(self.name)[1].lower(), "файл")

    @property
    def size_text(self) -> str:
        if self.size >= 1024 * 1024:
            return f"{self.size / 1024 / 1024:.1f} МБ".replace(".", ",")
        return f"{max(1, round(self.size / 1024))} КБ"


@dataclass
class ArchivedDay:
    """Смена, за которую уже собраны финальные файлы."""
    date: str
    files: List[ArchivedFile] = field(default_factory=list)
    saved_at: Optional[str] = None
    machines: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.files)

    @property
    def kinds(self) -> List[str]:
        """Виды файлов без повторов, в порядке отправки: [«Excel», «1С»]."""
        out: List[str] = []
        for f in self.files:
            if f.kind not in out:
                out.append(f.kind)
        return out

    @property
    def saved_at_text(self) -> str:
        """«04.09.2026 12:50» из ISO-времени; пусто, если время неизвестно."""
        if not self.saved_at:
            return ""
        try:
            return datetime.fromisoformat(self.saved_at).strftime(
                "%d.%m.%Y %H:%M")
        except ValueError:
            return self.saved_at

    @property
    def button_label(self) -> str:
        """Подпись на кнопке выбора даты: год не пишем, он и так текущий."""
        short = self.date[:5] if len(self.date) >= 5 else self.date
        return f"{short} · {len(self.files)} {_files_word(len(self.files))}"

    @property
    def line(self) -> str:
        """Строка для списка дат в чате."""
        out = f"{self.date} — " + (", ".join(self.kinds) or "файлов нет")
        when = self.saved_at_text
        if when:
            out += f", собран {when}"
        if self.machines:
            out += ", станки: " + ", ".join(self.machines)
        return out


# ──────────────────────────────── запись ────────────────────────────────

def slot(date: str, filename: str) -> str:
    """Путь, по которому писать финальный файл за эту дату (каталог создан).

    Имя файла детерминированное («Отчет производства за смену 20.08.2026.xlsx»),
    поэтому повторная сборка просто перезаписывает прошлый файл — в архиве
    остаётся только последняя версия.
    """
    target = day_dir(date)
    os.makedirs(target, exist_ok=True)
    return os.path.join(target, os.path.basename(filename))


def finish(date: str, filenames: List[str],
           machines: Optional[List[str]] = None,
           now: Optional[datetime] = None) -> None:
    """Закрыть сборку: записать meta.json и убрать файлы прошлых версий.

    `filenames` — что записано только что. Всё остальное (кроме meta.json) из
    каталога удаляется: иначе после переименования выгрузки в архиве копились
    бы два «последних» файла и в чат уходил бы старый.
    """
    target = day_dir(date)
    if not filenames:
        return
    keep = {os.path.basename(n) for n in filenames} | {META_NAME}
    # В этой сборке получились только такие форматы. Файл другого формата из
    # прошлой сборки НЕ трогаем: если Excel на этой сборке упал (openpyxl,
    # место на диске), в архиве должен остаться прежний — правило HANDOFF
    # «Excel обязателен», а не «Excel был когда-то».
    written_exts = {os.path.splitext(n)[1].lower() for n in filenames}
    try:
        for name in os.listdir(target):
            if name in keep:
                continue
            path = os.path.join(target, name)
            try:
                if os.path.isfile(path):
                    if os.path.splitext(name)[1].lower() not in written_exts:
                        continue
                    os.remove(path)
                else:
                    shutil.rmtree(path)
            except OSError as e:
                logger.warning("Не удалось убрать старый файл %s: %s", path, e)
    except OSError as e:
        logger.warning("Не удалось прочитать каталог архива %s: %s", target, e)
        return

    meta: Dict = {
        "date": date,
        "saved_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "files": [os.path.basename(n) for n in filenames],
        "machines": list(machines or []),
    }
    try:
        with open(os.path.join(target, META_NAME), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except OSError as e:                       # архив не должен ломать отчёт
        logger.warning("Не удалось записать %s: %s", META_NAME, e)


# ──────────────────────────────── чтение ────────────────────────────────

def _load_meta(date: str) -> Dict:
    """meta.json за дату. Нет или битый — пустой словарь (файлы важнее меты)."""
    try:
        with open(os.path.join(day_dir(date), META_NAME),
                  "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Не удалось прочитать мету архива за %s: %s", date, e)
        return {}


def _order_key(name: str) -> tuple:
    """Excel — первым, файл для 1С — вторым, прочее — потом."""
    kind = _KINDS.get(os.path.splitext(name)[1].lower(), "файл")
    return ({"Excel": 0, "1С": 1}.get(kind, 2), name.lower())


def day_files(date: str) -> List[ArchivedFile]:
    """Готовые файлы за смену: Excel, потом выгрузка для 1С. Нет — пустой список.

    Порядок берём из meta.json (в нём — порядок отправки), но опираемся на то,
    что реально лежит на диске: файл могли удалить руками.
    """
    target = day_dir(date)
    try:
        on_disk = [n for n in os.listdir(target)
                   if n != META_NAME and os.path.isfile(os.path.join(target, n))
                   and os.path.splitext(n)[1].lower() in _KINDS]
    except OSError:
        return []
    wanted = [n for n in (_load_meta(date).get("files") or []) if n in on_disk]
    wanted += sorted((n for n in on_disk if n not in wanted), key=_order_key)
    out = []
    for name in wanted:
        path = os.path.join(target, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        out.append(ArchivedFile(path=path, name=name, size=size))
    return out


def has_files(date: str) -> bool:
    return bool(day_files(date))


def archived_day(date: str) -> ArchivedDay:
    """Что готово за дату: файлы, когда собраны, по каким станкам."""
    meta = _load_meta(date)
    files = day_files(date)
    saved_at = meta.get("saved_at")
    if not saved_at and files:
        # Меты нет (старый каталог, ручное копирование) — берём время файла.
        try:
            newest = max(os.path.getmtime(f.path) for f in files)
            saved_at = datetime.fromtimestamp(newest).isoformat(
                timespec="seconds")
        except OSError:
            saved_at = None
    return ArchivedDay(date=date, files=files, saved_at=saved_at,
                       machines=list(meta.get("machines") or []))


def available_days(limit: Optional[int] = None,
                   newest_first: bool = True) -> List[ArchivedDay]:
    """Даты, за которые в архиве есть файлы. По умолчанию свежие — первыми."""
    try:
        names = os.listdir(_data_dir)
    except OSError:
        return []
    days = [archived_day(name) for name in names
            if os.path.isdir(os.path.join(_data_dir, name))]
    days = [d for d in days if d.ok]

    def _key(day: ArchivedDay):
        try:
            return datetime.strptime(day.date, DATE_FMT)
        except ValueError:
            return datetime.min

    days.sort(key=_key, reverse=newest_first)
    return days[:limit] if limit else days


def clear_day(date: str) -> int:
    """Удалить готовые файлы за дату. Возвращает, сколько файлов убрано."""
    files = day_files(date)
    for f in files:
        try:
            os.remove(f.path)
        except OSError as e:
            logger.warning("Не удалось удалить %s: %s", f.path, e)
    if files:
        try:
            os.remove(os.path.join(day_dir(date), META_NAME))
        except OSError:
            pass
    return len(files)
