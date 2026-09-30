"""HTTP-слой сайта: маршрутизация, загрузка файлов, отдача готовых файлов.

Только стандартная библиотека. Проверка на 10.09.2026 показала, что тянуть
FastAPI/Flask в проект не за чем: обработчиков ровно столько, сколько кнопок
было у бота, а тяжёлую работу (чтение файлов, разбор переписки, сборку отчёта)
делают `api.py` и `jobs.py`. Серверу остаётся принять запрос и отдать ответ.

Что здесь важно:

* файлы принимаются ТЕЛОМ запроса (`application/octet-stream`), а не multipart:
  парсить multipart руками — лишний код и лишний способ ошибиться;
* снаружи торчит только 127.0.0.1 — доступ к переписке и счётчикам нужен людям
  с этого компьютера, пароля на сайте нет;
* отдаём наружу только то, что сами записали: статика по белому списку, готовые
  файлы — через `api.download()`, который сверяет имя с архивом даты;
* long-путь любой ошибки уходит JSON-ом с внятным текстом: страница показывает
  его человеку, а не трейсбек.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional, Tuple

from config.config import settings
from runtime_paths import bundle_path
from services import file_text
from webapp import api

logger = logging.getLogger(__name__)

# Страница только читается и лежит внутри сборки — в .exe она распакуется
# вместе с программой, а не в папку рядом с ней.
STATIC_DIR = bundle_path("webapp", "static")

# Статика отдаётся по белому списку: никакого «/static/../../config/.env».
STATIC_FILES: Dict[str, Tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

# Тело запроса принимаем не бесконечное: file_text держит свой лимит на сам
# документ, здесь — страховка от мусора.
MAX_BODY_BYTES = file_text.MAX_FILE_BYTES + 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "SecafgreeReport/1.0"
    protocol_version = "HTTP/1.1"

    # ────────────────────────── служебное ──────────────────────────
    def log_message(self, fmt: str, *args) -> None:      # записываем в лог, не в консоль
        logger.info("%s — %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str,
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status=status)

    def _query(self) -> Dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        return {k: (v[0] if v else "")
                for k, v in urllib.parse.parse_qs(parsed.query).items()}

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise api.ApiError("Не понятна длина запроса.")
        if length <= 0:
            return b""
        if length > MAX_BODY_BYTES:
            raise api.ApiError(
                f"Запрос больше {MAX_BODY_BYTES // 1024 // 1024} МБ — "
                f"пришлите файл меньшего размера или частями.")
        return self.rfile.read(length)

    def _json_body(self) -> Dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise api.ApiError(f"Не удалось прочитать запрос: {e}")
        if not isinstance(data, dict):
            raise api.ApiError("Ожидался набор полей, а не одно значение.")
        return data

    # ────────────────────────── диспетчер ──────────────────────────
    def _call(self, fn: Callable[[], object]) -> None:
        """Один вызов обработчика: его JSON или внятная ошибка."""
        try:
            result = fn()
        except api.ApiError as e:
            self._error(e.message, e.status)
            return
        except file_text.FileTextError as e:
            # Так бывает, если приём данных вызвали не через api.upload_file.
            self._error(f"⚠️ {e}", 415)
            return
        except BrokenPipeError:                  # закрыли вкладку во время скачивания
            logger.info("Клиент отключился посреди ответа")
            return
        except Exception as e:                   # noqa: BLE001
            logger.exception("Запрос %s упал", self.path)
            self._error(f"⚠️ Не получилось: {e}", 500)
            return
        if isinstance(result, (dict, list)):
            self._json(result)
        elif result is None:
            self._json({"ok": True})
        else:
            self._json({"ok": True, "result": result})

    def do_GET(self) -> None:                    # noqa: N802 - имя BaseHTTPRequestHandler
        routes = {
            "/api/state": api.state,
            "/api/journal": lambda: api.journal(),
            "/api/archive": api.archive,
            "/api/help": api.help_text,
        }
        path = urllib.parse.urlparse(self.path).path
        if path in routes:
            self._call(routes[path])
            return
        if path == "/api/job":
            self._call(lambda: api.job(self._query().get("id", "")))
            return
        if path == "/api/archive_day":
            self._call(lambda: api.archive_day(self._query().get("date", "")))
            return
        if path == "/api/day_detail":
            self._call(lambda: api.day_detail(self._query().get("date", "")))
            return
        if path == "/api/download":
            self._download()
            return
        self._static(path)

    def do_HEAD(self) -> None:                   # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:                   # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        query = self._query()
        if path == "/api/add_text":
            def text_route():
                body = self._json_body()
                return api.add_text(text=body.get("text", ""),
                                    who=body.get("who", ""),
                                    date=body.get("date", ""),
                                    machine=body.get("machine", ""))
            self._call(text_route)
            return
        if path in ("/api/upload_file", "/api/add_photo"):
            self._upload(path, query)
            return
        if path == "/api/build":
            def build_route():
                body = self._json_body()
                return api.build(date=body.get("date", ""),
                                 who=body.get("who", ""))
            self._call(build_route)
            return
        if path == "/api/rebuild":
            def rebuild_route():
                body = self._json_body()
                return api.rebuild(date=body.get("date", ""),
                                   who=body.get("who", ""))
            self._call(rebuild_route)
            return
        if path == "/api/period":
            def period_route():
                body = self._json_body()
                return api.period(who=body.get("who", ""),
                                  start=body.get("start", ""),
                                  end=body.get("end", ""))
            self._call(period_route)
            return
        if path == "/api/reset":
            def reset_route():
                body = self._json_body()
                return api.reset(date=body.get("date", ""))
            self._call(reset_route)
            return
        self._error("Такого раздела на сайте нет.", 404)

    # ────────────────────────── загрузка и отдача ──────────────────────────
    def _upload(self, path: str, query: Dict[str, str]) -> None:
        def route():
            blob = self._body()
            name = (query.get("name") or "").strip()
            if path == "/api/upload_file":
                return api.upload_file(name=name or "файл", blob=blob,
                                       who=query.get("who", ""),
                                       date=query.get("date", ""),
                                       caption=query.get("caption", ""))
            return api.add_photo(name=name or "фото", blob=blob,
                                 who=query.get("who", ""),
                                 date=query.get("date", ""),
                                 caption=query.get("caption", ""))
        self._call(route)

    def _download(self) -> None:
        query = self._query()
        try:
            found = api.download(query.get("date", ""), query.get("name", ""))
        except api.ApiError as e:
            self._error(e.message, e.status)
            return
        try:
            with open(found["path"], "rb") as fh:
                blob = fh.read()
        except OSError as e:                     # файл могли удалить руками
            logger.exception("Не удалось прочитать готовый файл %s",
                             found["path"])
            self._error(f"⚠️ Файл «{found['name']}» прочитать не удалось: {e}. "
                        f"Соберите отчёт заново.", 500)
            return
        quoted = urllib.parse.quote(found["name"])
        self._send(200, blob, "application/octet-stream",
                   {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})

    def _static(self, path: str) -> None:
        entry = STATIC_FILES.get(urllib.parse.unquote(path))
        if not entry:
            self._error("Страница не найдена.", 404)
            return
        filename, content_type = entry
        try:
            with open(os.path.join(STATIC_DIR, filename), "rb") as fh:
                blob = fh.read()
        except OSError:
            self._error(f"Файл страницы «{filename}» не найден рядом с "
                        f"программой.", 500)
            return
        self._send(200, blob, content_type)


class Server(ThreadingHTTPServer):
    """Один поток на запрос: сборка отчёта и загрузка файла не блокируют страницу."""

    daemon_threads = True
    # На Windows SO_REUSEADDR позволяет поднять ВТОРОЙ сервер на занятом порту,
    # и повторный запуск .exe молча висит рядом с первым вместо честного
    # «сайт уже запущен». На Linux/маке этот же флаг спасает от TIME_WAIT после
    # перезапуска, поэтому снимаем его только на Windows.
    allow_reuse_address = sys.platform != "win32"


def serve(host: Optional[str] = None, port: Optional[int] = None) -> None:
    """Поднять сайт и ждать. Вызывается из main.py."""
    bind = host or settings.web_host
    listen = port or settings.web_port
    httpd = Server((bind, listen), _Handler)
    logger.info("Сайт отчётов: http://%s:%s", bind, listen)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


__all__ = ["Server", "serve", "STATIC_FILES", "MAX_BODY_BYTES"]
