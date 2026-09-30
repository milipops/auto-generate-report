# -*- coding: utf-8 -*-
"""Сборка сайта производственных отчётов в один .exe без окна консоли.

Зачем это нужно: человеку в цеху не объяснишь, что такое `py -3.11 main.py`,
окно консоли и Ctrl+C. Один файл, по которому двойной клик, — объяснять не
надо.

Команда сборки (из корня проекта, тем же Python'ом, где стоят зависимости):

    py -3.11 -m PyInstaller --noconfirm --distpath . production-reports.spec

`--distpath .` кладёт готовый `production-reports.exe` в корень проекта — рядом
с `.env`, `data/` и `logs/`, которые программа ищет по соседству с собой (см.
`runtime_paths.py`). Поэтому запуск сразу работает с теми же сменами и той же
базой, что и `python main.py`.

Что важно в этой сборке:

* `console=False` — окно консоли не показывается; логи пишутся в `logs/web.log`,
  а ошибка запуска показывается окном сообщения;
* страница сайта и справочник норм укладываются ВНУТРЬ сборки (`datas`): их
  читают, а вот переписку смены, готовые файлы и базу — пишут рядом с .exe;
* `onefile`, а не папка: один файл сложнее потерять или недокопировать. Плата —
  первые секунды идут на распаковку во временный каталог, потому и решено, что
  в нём храниться не должно ничего;
* ключ ИИ в .exe НЕ вкладывается: он читается из `.env` рядом с программой, и
  переносимый файл можно отдать другому человеку без секрета внутри.
"""
import os

# pytesseract и Pillow здесь нужны обязательно: без них фото на приёме не
# разбираются вовсе. openpyxl — Excel-бланк смены; pypdf — приём выгрузок PDF.
hiddenimports = [
    "runtime_paths",
    "openpyxl",
    "pytesseract",
    "PIL",
    "PIL.Image",
    "pypdf",
]

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath(SPEC)],
    binaries=[],
    datas=[
        (os.path.join("webapp", "static"), os.path.join("webapp", "static")),
        (os.path.join("services", "machine_rates.json"), "services"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="production-reports",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
