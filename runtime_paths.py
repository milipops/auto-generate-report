"""Где лежат файлы: одинаково при запуске из исходников и из собранного .exe.

Зачем отдельный модуль. Одиночный .exe (PyInstaller) при запуске распаковывает
модули во временный каталог и стирает его при выходе. Если считать «папкой
проекта» каталог рядом с модулем (`services/shift_store.py`), переписка смены
и готовые отчёты начнут писаться во временный каталог — и исчезнут вместе с
ним, не успев ни в одну базу. Поэтому каталога два:

* `app_path(...)` — куда пишем: `.env`, база, `data/shifts`, `data/reports`,
  `logs/`. Это папка рядом с .exe (или корень проекта при обычном запуске);
* `bundle_path(...)` — что только читаем: страница сайта, справочник норм.
  Это сама сборка (временный каталог .exe).

При `python main.py` оба совпадают с корнем проекта, и поведение не меняется.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Собираемся ли мы в .exe (PyInstaller задаёт sys.frozen).
FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # Рядом с .exe: сюда кладутся .env, база, data/ и logs/.
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

# Каталог с файлами самой программы: при .exe это временная распаковка.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", str(APP_DIR))).resolve()


def app_path(*parts: str) -> str:
    """Путь к файлу данных рядом с программой (строка для open() и os.path)."""
    return str(APP_DIR.joinpath(*parts))


def bundle_path(*parts: str) -> str:
    """Путь к файлу, который лежит внутри программы и только читается."""
    return str(BUNDLE_DIR.joinpath(*parts))
