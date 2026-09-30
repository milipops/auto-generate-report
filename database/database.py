"""Доступ к базе: люди, журнал загрузок и журнал сборок отчётов.

Слой тонкий специально: вся логика отчёта живёт в `services/`, а здесь только
запись фактов («приняли файл от Татьяны», «собрали отчёт за 22.09, вышли два
файла») и их чтение для интерфейса.

Сессии открываются и закрываются в каждом методе: сайт работает в несколько
потоков (каждый запрос — свой поток, сборка отчёта — отдельный), и держать
сессию на поток здесь нельзя.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from config.config import settings
from .models import (
    Base,
    ProductionEvent,
    ProductionRecord,
    ReportRun,
    ShiftUpload,
    User,
)

logger = logging.getLogger(__name__)


class Database:
    """Класс для работы с базой данных"""

    def __init__(self, database_url: str):
        self.engine = create_engine(
            database_url,
            # Сборка отчёта идёт в своём потоке, HTTP-запросы — в других.
            connect_args={"check_same_thread": False} if "sqlite" in database_url else {}
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False,
                                         bind=self.engine)

    def create_tables(self) -> None:
        Base.metadata.create_all(bind=self.engine)

    def get_session(self) -> Session:
        return self.SessionLocal()

    # ─────────────────────────────── люди ───────────────────────────────
    def get_or_create_user(self, name: Optional[str]) -> Optional[User]:
        """Пользователь по имени. Пустое имя — None: подписывать некого.

        Имена не регистрозависимые и не «чистятся» от лишнего пробела зря: как
        мастер написал, так и в журнале, — но «татьяна» и «Татьяна» это один
        человек, поэтому ключ — нормализованное написание.
        """
        clean = (name or "").strip()
        if not clean:
            return None
        key = clean.lower()
        session = self.get_session()
        try:
            for user in session.query(User).all():
                if (user.name or "").strip().lower() == key:
                    user.last_seen_at = datetime.now()
                    session.commit()
                    session.refresh(user)
                    return user
            user = User(name=clean)
            session.add(user)
            session.commit()
            session.refresh(user)
            return user
        finally:
            session.close()

    def list_users(self) -> List[User]:
        """Кем можно подписаться (подсказки в поле «кто прислал»)."""
        session = self.get_session()
        try:
            return session.query(User).order_by(User.last_seen_at.desc()).all()
        finally:
            session.close()

    # ─────────────────────────── журнал загрузок ───────────────────────────
    def log_upload(self, *, user: Optional[User], kind: str, source: str,
                   shift_date: str, file_kind: str = "",
                   sections: int = 0, machines: Optional[List[str]] = None,
                   authors: Optional[List[str]] = None,
                   messages_total: int = 0, photos_total: int = 0,
                   duplicate: bool = False, duplicate_of: str = "",
                   error: str = "", warning: str = "") -> int:
        """Записать факт загрузки; возвращает id записи (0 — не записали)."""
        session = self.get_session()
        try:
            row = ShiftUpload(
                user_id=user.id if user else None,
                kind=kind,
                source=(source or "")[:255],
                file_kind=file_kind,
                shift_date=shift_date,
                sections=sections,
                machines_json=json.dumps(machines or [], ensure_ascii=False),
                authors_json=json.dumps(authors or [], ensure_ascii=False),
                messages_total=messages_total,
                photos_total=photos_total,
                duplicate=duplicate,
                duplicate_of=duplicate_of,
                error=error,
                warning=warning,
            )
            session.add(row)
            session.commit()
            return row.id
        except Exception:                       # журнал не должен ломать приём
            logger.exception("Не удалось записать загрузку в журнал")
            session.rollback()
            return 0
        finally:
            session.close()

    def recent_uploads(self, limit: int = 20,
                       shift_date: Optional[str] = None) -> List[ShiftUpload]:
        session = self.get_session()
        try:
            # id — второй ключ: created_at в SQLite хранится с точностью до
            # секунды, и две загрузки в одной секунде иначе выстроились бы в
            # произвольном порядке (журнал показывал бы не свежее сверху).
            query = session.query(ShiftUpload).order_by(
                ShiftUpload.created_at.desc(), ShiftUpload.id.desc())
            if shift_date:
                query = query.filter(ShiftUpload.shift_date == shift_date)
            return query.limit(limit).all()
        finally:
            session.close()

    # ─────────────────────────── журнал отчётов ───────────────────────────
    def start_report(self, *, user: Optional[User], shift_date: str,
                     scope: str = "day", messages: int = 0,
                     photos: int = 0) -> int:
        """Завести запись о сборке; id нужен, чтобы потом её закрыть."""
        session = self.get_session()
        try:
            row = ReportRun(user_id=user.id if user else None,
                            shift_date=shift_date, scope=scope,
                            state="running", messages=messages, photos=photos)
            session.add(row)
            session.commit()
            return row.id
        except Exception:
            logger.exception("Не удалось завести запись о сборке отчёта")
            session.rollback()
            return 0
        finally:
            session.close()

    def finish_report(self, run_id: int, *, ok: bool,
                      files: Optional[List[str]] = None,
                      machines: Optional[List[str]] = None,
                      warnings: Optional[List[str]] = None,
                      note: str = "", error: str = "") -> None:
        if not run_id:
            return
        session = self.get_session()
        try:
            row = session.get(ReportRun, run_id)
            if not row:
                return
            row.state = "error" if error else "done"
            row.ok = ok
            row.files_json = json.dumps(files or [], ensure_ascii=False)
            row.machines_json = json.dumps(machines or [], ensure_ascii=False)
            row.warnings_json = json.dumps(warnings or [], ensure_ascii=False)
            row.note = note or None
            row.error = error or None
            row.finished_at = datetime.now()
            session.commit()
        except Exception:
            logger.exception("Не удалось закрыть запись о сборке отчёта")
            session.rollback()
        finally:
            session.close()

    def recent_reports(self, limit: int = 20) -> List[ReportRun]:
        session = self.get_session()
        try:
            return session.query(ReportRun).order_by(
                ReportRun.started_at.desc()).limit(limit).all()
        finally:
            session.close()

    def abort_stale_runs(self) -> int:
        """Погасить сборки, которые остались «идут» с прежнего запуска.

        Сама сборка живёт в памяти процесса (`webapp/jobs.py`), а запись журнала
        закрывает `finish_report`. Если процесс сняли посреди сборки (перезапуск
        .exe, закрытие окна, пропало питание), строка навсегда остаётся
        `state='running'`, и в журнале висит сборка, которая «идёт» с утра.
        Ничего не теряем: после перезапуска ни одна такая сборка продолжаться не
        может по определению, а готовые файлы на диске она не касается.
        """
        session = self.get_session()
        try:
            stale = session.query(ReportRun).filter(
                ReportRun.state == "running").all()
            for row in stale:
                row.state = "error"
                row.ok = False
                row.error = "сборка прервана вместе с программой"
                row.finished_at = datetime.now()
            session.commit()
            if stale:
                logger.info("Журнал: %d незакрытая сборка(ок) помечены "
                            "прерванными", len(stale))
            return len(stale)
        except Exception:
            logger.exception("Не удалось погасить незакрытые сборки")
            session.rollback()
            return 0
        finally:
            session.close()

    # ───────────────────────── разбор отдельных сообщений ─────────────────────────
    def save_production_record(self, parsed, user_id: int = None,
                               shift_date: str = None) -> ProductionRecord:
        """Сохранить результат parse_production_message и его события в SQLite."""
        session = self.get_session()
        try:
            record = ProductionRecord(
                user_id=user_id,
                shift_date=shift_date,
                machine_number=parsed.machine,
                shift_start=parsed.shift_start,
                shift_end=parsed.shift_end,
                operator=parsed.operator,
                total_counters_json=json.dumps(parsed.total_counters,
                                               ensure_ascii=False),
                slice_counters_json=json.dumps(parsed.slice_counters,
                                               ensure_ascii=False),
                counters_json=json.dumps([item.__dict__ for item
                                          in parsed.counters],
                                         ensure_ascii=False),
                substitutions_json=json.dumps([item.__dict__ for item
                                               in parsed.substitutions],
                                              ensure_ascii=False),
                raw_text=parsed.raw_text,
                warnings_json=json.dumps(parsed.warnings, ensure_ascii=False),
            )
            session.add(record)
            session.flush()
            for event in parsed.events:
                session.add(ProductionEvent(
                    record_id=record.id,
                    start_time=event.start,
                    end_time=event.end,
                    category=event.category,
                    text=event.text,
                ))
            session.commit()
            session.refresh(record)
            return record
        finally:
            session.close()

    def get_production_records(self, machine_number: str = None,
                               limit: int = 20) -> List[ProductionRecord]:
        """Получить последние производственные записи."""
        session = self.get_session()
        try:
            query = (session.query(ProductionRecord)
                     .order_by(ProductionRecord.created_at.desc()))
            if machine_number:
                query = query.filter(
                    ProductionRecord.machine_number == machine_number)
            return query.limit(limit).all()
        finally:
            session.close()


# Создаем глобальный экземпляр базы данных
db = Database(settings.database_url)
