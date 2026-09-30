"""Модели базы данных локального сайта отчётов.

Почему база вообще нужна. Данные смены живут файлами (`data/shifts/<дата>/
day.json`, фото рядом) — этот формат проверен половиной тестов, и переносить
его в SQLite смысла нет. Базе досталось то, чего в файлах нет:

* **кто** работает с данными. В Telegram это давал аккаунт отправителя, на
  сайте человека видит только поле «кто прислал» — его и заводим в таблице
  `users` по имени;
* **журнал** загрузок и сборок: когда приняли файл, сколько разделов разложили
  по станкам, был ли это повтор, когда собирали отчёт и какие файлы вышли.
  По этому журналу понятно, чьё сообщение испортило отчёт, и не потерялась ли
  выгрузка;
* разборы отдельных сообщений (`production_records`/`production_events`) —
  быстрые, без ИИ, чтобы по станку можно было смотреть историю показаний.

Схему НЕ мигрируем: под сайтом новая база (`web_database.db`), старый файл
`bot_database.db` остаётся как есть.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class User(Base):
    """Человек, который работает с сайтом (по имени, без пароля).

    Вход на сайте не нужен — он локальный и рабочий, — но подписывать, кто
    прислал переписку и кто собрал отчёт, нужно: это единственное, чем
    отличается запись мастера от записи наладчика в одном дне.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    last_seen_at = Column(DateTime, default=datetime.now)

    uploads = relationship("ShiftUpload", back_populates="user")
    reports = relationship("ReportRun", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, name={self.name!r})>"


class ShiftUpload(Base):
    """Одна загрузка данных: файл, текст или фото."""
    __tablename__ = "shift_uploads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # file | text | photo
    kind = Column(String(10), nullable=False, default="file")
    # Что прислали: имя файла или первые строки текста — чтобы журнал читался
    # без открытия day.json.
    source = Column(String(255), nullable=True)
    # Как файл прочитали («документ Word», «плоская выгрузка»), из
    # services/file_text.py.
    file_kind = Column(String(60), nullable=True)
    shift_date = Column(String(10), nullable=False, index=True)
    # Сколько секций/станков разложили и сколько сообщений стало в смене.
    sections = Column(Integer, nullable=False, default=0)
    machines_json = Column(Text, nullable=False, default="[]")
    authors_json = Column(Text, nullable=False, default="[]")
    messages_total = Column(Integer, nullable=False, default=0)
    photos_total = Column(Integer, nullable=False, default=0)
    # Повтор того же файла (см. shift_store.add_document) и откуда дубликат.
    duplicate = Column(Boolean, default=False)
    duplicate_of = Column(String(255), nullable=True)
    # Ошибка чтения или предупреждение о качестве текста — пустая строка, если
    # всё прочиталось нормально.
    error = Column(Text, nullable=True)
    warning = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("User", back_populates="uploads")

    def __repr__(self):
        return (f"<ShiftUpload(id={self.id}, kind={self.kind}, "
                f"date={self.shift_date}, source={self.source!r})>")


class ReportRun(Base):
    """Одна сборка отчёта: за какую дату, кем, чем кончилась.

    `day_pipeline.build_day` может думать минут десять (ИИ по всем станкам), и
    повторять это ради того, чтобы посмотреть файл, нельзя — поэтому в журнале
    лежат и имена готовых файлов: «Готовые файлы» на сайте отдаёт их отсюда, не
    дёргая модель второй раз.
    """
    __tablename__ = "report_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    shift_date = Column(String(10), nullable=False, index=True)
    # day — одна смена, period — сводный Excel за все смены.
    scope = Column(String(10), nullable=False, default="day")
    # running | done | error
    state = Column(String(10), nullable=False, default="running")
    # Есть ли полный отчёт по станкам (то есть был ли Excel).
    ok = Column(Boolean, default=False)
    messages = Column(Integer, nullable=False, default=0)
    photos = Column(Integer, nullable=False, default=0)
    files_json = Column(Text, nullable=False, default="[]")
    machines_json = Column(Text, nullable=False, default="[]")
    warnings_json = Column(Text, nullable=False, default="[]")
    note = Column(Text, nullable=True)        # почему полного отчёта нет
    error = Column(Text, nullable=True)       # Exception, если сборка упала
    started_at = Column(DateTime, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="reports")

    def __repr__(self):
        return (f"<ReportRun(id={self.id}, date={self.shift_date}, "
                f"state={self.state}, ok={self.ok})>")


class ProductionRecord(Base):
    """Разобранный отчет оператора по станку и смене."""
    __tablename__ = "production_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    shift_date = Column(String(10), nullable=True, index=True)
    machine_number = Column(String(50), nullable=True, index=True)
    shift_start = Column(String(10), nullable=True)
    shift_end = Column(String(10), nullable=True)
    operator = Column(String(255), nullable=True)
    total_counters_json = Column(Text, nullable=False, default="[]")
    slice_counters_json = Column(Text, nullable=False, default="[]")
    counters_json = Column(Text, nullable=False, default="[]")
    substitutions_json = Column(Text, nullable=False, default="[]")
    raw_text = Column(Text, nullable=False)
    warnings_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("User")
    events = relationship("ProductionEvent", back_populates="record",
                          cascade="all, delete-orphan")


class ProductionEvent(Base):
    """Отдельная строка/событие производственного отчета."""
    __tablename__ = "production_events"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("production_records.id"),
                       nullable=False)
    start_time = Column(String(10), nullable=True)
    end_time = Column(String(10), nullable=True)
    category = Column(String(50), nullable=False, default="note")
    text = Column(Text, nullable=False)

    record = relationship("ProductionRecord", back_populates="events")
