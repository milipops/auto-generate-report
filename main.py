"""Точка входа локального сайта производственных отчётов.

Запуск из исходников: `python main.py`. Запуск для человека без консоли:
двойной клик по собранному .exe (см. `production-reports.spec`). В обоих
случаях поднимется сервер на адресе из .env (по умолчанию
http://127.0.0.1:8080) и откроется страница.

Что здесь происходит перед запуском и почему не короче:

* таблицы базы создаются на старте: схема простая (`users`, журнал загрузок,
  журнал сборок), и alembic под неё не нужен — при переезде на новую базу
  достаточно просто удалить файл;
* необязательные библиотеки проверяются, а не требуются: без openpyxl сайт
  live и собирает текстовые отчёты, без Tesseract фотографии уходят в ИИ
  картинкой. Падать из-за этого на ровном месте — значит оставить цех без
  отчётов в тот момент, когда их можно отдать хоть так.

Про .exe без окна консоли. У него нет ни Ctrl+C, ни stderr, поэтому:

* в консоль пишем только когда она действительно есть, иначе логгер падает на
  каждом сообщении; всё то же самое уходит в `logs/web.log`;
* повторный двойной клик — не ошибка, а «открой страницу ещё раз»: занятый
  порт ловится и трактуется именно так;
* фатальный сбой показывается окном сообщения, потому что иначе человек
  увидит, как по двойному клику «ничего не произошло».
"""
import logging
import os
import sys
import webbrowser

from config import settings
from database import db
from runtime_paths import FROZEN, app_path
from webapp import server


def console_stream():
    """Куда писать человеку, или None, если такого места нет.

    У .exe, запущенного двойным кликом, потоков вывода нет вовсе. У запуска
    через перенаправление (`main.py > log.txt`) поток есть, но в кодировке
    Windows, и эмодзи в нём — UnicodeEncodeError прямо на старте. Поэтому
    поток сначала приучается заменять то, что не влезает, и только потом
    считается пригодным.
    """
    stream = sys.stdout if sys.stdout is not None else sys.stderr
    if stream is None:
        return None
    try:
        stream.reconfigure(errors="replace")     # pythonw/NullWriter его не имеют
    except Exception:                            # noqa: BLE001 - пробуем как умеем
        pass
    try:
        stream.write("")
    except Exception:                            # noqa: BLE001 - поток мёртв
        return None
    return stream


def configure_logging() -> None:
    """Лог и в консоль, и в файл: сборка отчёта идёт минут десять, и по файлу
    потом видно, что именно отвалилось. Файл — обязательная часть: у .exe
    консоли нет, и без него запуск стал бы неотлаживаемым."""
    handlers: list = []
    stream = console_stream()
    if stream is not None:
        handlers.append(logging.StreamHandler(stream))
    try:
        os.makedirs(os.path.dirname(settings.log_file) or ".", exist_ok=True)
        handlers.append(logging.FileHandler(settings.log_file, encoding="utf-8"))
    except OSError:                       # диск или права — не повод не запускать
        pass
    if not handlers:
        handlers.append(logging.NullHandler())
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
    )


def check_optional_deps() -> None:
    log = logging.getLogger(__name__)
    try:
        import openpyxl                       # noqa: F401
    except ImportError:
        log.warning("openpyxl не установлен — Excel-отчёт собран не будет, "
                    "текстовая часть и файл для 1С останутся. %s",
                    "Пересоберите .exe на компьютере с установленным openpyxl."
                    if FROZEN else
                    "Установка: py -m pip install -r requirements.txt")


def open_page(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:                        # на сервере браузера может не быть
        logging.getLogger(__name__).debug("Браузер сам не открылся", exc_info=True)


def _port_taken(exc: OSError) -> bool:
    """98 — «порт занят» на Linux, 10048 — то же на Windows."""
    codes = {98, 10048}
    return (getattr(exc, "winerror", None) in codes
            or getattr(exc, "errno", None) in codes
            or "уже используется" in str(exc))


def startup_lines(url: str) -> list:
    """Что сказать человеку перед тем, как уйти ждать запросы.

    Реплики идут через логгер: в консоли их видно, в `.exe` без консоли они
    остаются в `logs/web.log`.
    """
    lines = [f"📊 Сайт производственных отчётов: {url}"]
    if settings.web_host not in ("127.0.0.1", "localhost"):
        lines.append("⚠️ Сайт открыт наружу, а пароля на нём нет — доступ к "
                     "переписке и счётчикам получит любой в сети.")
    if not settings.ai_enabled:
        lines.append("⚠️ ИИ выключен (AI_ENABLED=false): отчёт соберётся "
                     "упрощённо, без расчёта часов по счётчику и без Excel.")
    else:
        # Модель в шапке — не для красоты: `.env` читается один раз при старте,
        # и пока живёт прежний процесс, правка AI_MODEL до отчёта не доходит.
        # Без этой строки человек меняет модель, видит в логе прежнюю и не
        # понимает, что страница работает на старом запуске (24.09.2026 именно
        # так и искали несуществующую модель).
        lines.append(f"🧠 ИИ: модель {settings.ai_model} "
                     f"(формат {settings.ai_api_format}, адрес "
                     f"{settings.ai_base_url}).")
        lines.append("⚠️ Если поменяешь `.env`, перезапусти программу: настройки "
                     "читаются один раз при запуске.")
    if FROZEN:
        lines.append(f"Данные смены и готовые отчёты: {app_path('data')}, "
                     f"лог: {settings.log_file}.")
        lines.append("Остановить — снять задачу программы в диспетчере задач; "
                     "повторный запуск просто откроет страницу.")
    else:
        lines.append("Остановить — Ctrl+C. Данные смены: data/shifts, "
                     "готовые файлы: data/reports.")
    return lines


def main() -> None:
    configure_logging()
    log = logging.getLogger(__name__)
    check_optional_deps()
    db.create_tables()
    # Сборка не может «идти» из прежнего запуска процесса — в журнале такие
    # обрываются и помечаются прерванными, иначе висит вечное «идёт».
    db.abort_stale_runs()

    url = f"http://{settings.web_host}:{settings.web_port}/"
    for line in startup_lines(url):
        log.info(line)

    open_page(url)
    try:
        server.serve()
    except OSError as e:
        if not _port_taken(e):
            raise
        # Порт занят прежним запуском (человек кликнул второй раз). Страницу мы
        # выше уже открыли, и её обслуживает тот, прежний процесс, — ругаться
        # не на что, этот просто завершится.
        log.info(f"Порт {settings.web_port} занят — сайт уже запущен, "
                 f"страница открыта им.")


def alert(text: str) -> None:
    """Показать беду человеку, у которого консоли нет."""
    log = logging.getLogger(__name__)
    log.error(text)
    if not FROZEN:
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f"{text}\n\nПодробности — в файле лога: {settings.log_file}",
            "Сайт отчётов", 0x10)
    except Exception:                        # noqa: BLE001 - показываем как умеем
        log.warning("Окно с текстом ошибки показать не удалось", exc_info=True)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as exc:                 # noqa: BLE001 - чтобы не молча
        alert(f"Сайт не запустился: {type(exc).__name__}: {exc}")
        if console_stream() is not None:
            logging.getLogger(__name__).exception("Сайт не запустился")
        raise SystemExit(1)
