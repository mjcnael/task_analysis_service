"""Периодическая синхронизация с Bitrix24.

Дополняет push-механизм исходящих вебхуков, закрывая два пробела:
  1) catch-up после простоя сервиса (Bitrix не ретраит исходящие вебхуки бесконечно);
  2) первичный импорт задач, существующих в группе до подключения сервиса.

Правило уведомлений:
  - Первый цикл sync (когда `sync_initial_done` ещё не выставлен) проходит МОЛЧА:
    БД заполняется, но в Telegram ничего не шлётся. После успешного прохода
    флаг `sync_initial_done` выставляется в "1".
  - Все последующие циклы шлют уведомления в Telegram по тем же правилам,
    что и push-вебхук:
      * новая задача → "created" (кратко) + "created_full" (полная информация)
      * смена стадии → "status_changed"
      * удаление задачи → "deleted"
      * срабатывание правила тега → "sub_tag_setted"
  - Сбросить флаг (для повторного "молчаливого" импорта) можно через
    `Настройки → Приложение → Сбросить состояние синхронизации`.
"""
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from database import Database
from database.models import Ticket, Status, TagRule
from bitrix import get_task_api, get_projects_api
from bitrix.client import BitrixApiError
from config_manager import get as cfg_get, set as cfg_set


logging.getLogger("apscheduler").setLevel(logging.WARNING)

_scheduler: Optional[AsyncIOScheduler] = None
_job = None
_default_interval = 60
_stage_cache: Dict[str, Dict[str, str]] = {}  # group_id -> {stage_id: stage_name}


def _bx_id(d: Dict[str, Any]) -> Optional[str]:
    for k in ("id", "ID"):
        v = d.get(k)
        if v is not None:
            return str(v)
    return None


def _bx(d: Dict[str, Any], *keys) -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _bx_tags(d: Dict[str, Any]) -> List[str]:
    raw = d.get("tags") or d.get("TAGS") or []
    out: List[str] = []
    if isinstance(raw, list):
        for t in raw:
            if isinstance(t, str):
                out.append(t)
            elif isinstance(t, dict):
                n = t.get("name") or t.get("NAME") or t.get("title")
                if n:
                    out.append(n)
    return out


async def _resolve_stage_name(group_id: str, stage_id: str) -> str:
    if not stage_id:
        return ""
    cache = _stage_cache.get(group_id)
    if cache is None:
        papi = get_projects_api()
        if papi is None:
            return f"Stage {stage_id}"
        try:
            sections = await papi.get_sections(group_id)
            cache = {str(s.get("gid")): s.get("name", f"Stage {s.get('gid')}") for s in sections}
            _stage_cache[group_id] = cache
        except BitrixApiError:
            return f"Stage {stage_id}"
    return cache.get(str(stage_id), f"Stage {stage_id}")


async def reset_initial_flag() -> None:
    """Сбросить флаг 'первый импорт пройден' — следующий sync снова молчит."""
    await cfg_set("sync_initial_done", "0")
    logging.info("Sync: флаг первичного импорта сброшен")


async def sync_once() -> None:
    """Один проход синхронизации. Безопасно вызывать вручную (например, при старте).

    Уведомления отправляются только если первичный импорт уже выполнен.
    """
    group_id = await cfg_get("main_project_gid")
    if not group_id:
        return

    task_api = get_task_api()
    if task_api is None:
        return

    try:
        bx_tasks: List[Dict[str, Any]] = await task_api.list_tasks_by_group(group_id)
    except BitrixApiError as e:
        logging.warning(f"sync: не удалось получить задачи группы {group_id}: {e}")
        return

    is_initial = (await cfg_get("sync_initial_done")) != "1"
    if is_initial:
        logging.info(f"Sync: первичный импорт {len(bx_tasks)} задач — без уведомлений")

    _stage_cache.pop(group_id, None)

    # импорт здесь, чтобы избежать циклов на старте
    from core.events_handler import _notify_chats, _apply_tag_rules

    bx_ids: set = set()

    async with Database.make_session() as session:
        for t in bx_tasks:
            tid = _bx_id(t)
            if not tid:
                continue
            bx_ids.add(tid)

            ticket = await Ticket.get_by_gid(session, tid)
            new_stage_id = _bx(t, "stageId", "STAGE_ID")
            new_stage_name = await _resolve_stage_name(group_id, new_stage_id) if new_stage_id else ""
            is_completed = _bx(t, "status", "STATUS") == "5"
            title = _bx(t, "title", "TITLE") or f"Bitrix task {tid}"
            desc = _bx(t, "description", "DESCRIPTION")
            tags = _bx_tags(t)

            # -------- НОВАЯ ЗАДАЧА --------
            if ticket is None:
                ticket = Ticket(gid=tid, title=title, text=desc)
                ticket.completed = is_completed
                session.add(ticket)
                if is_initial:
                    session.add(Status(text="Импортировано из Bitrix24", ticket=ticket))
                else:
                    session.add(Status(text="Создано (из Bitrix24)", ticket=ticket))
                    await _notify_chats(session, "created", f"Получено '{ticket.title}'")
                    await _notify_chats(session, "created_full",
                                        f"Получен челлендж:\n\n{str(ticket)}")
                if new_stage_name:
                    session.add(Status(text=new_stage_name, ticket=ticket))
                    if not is_initial:
                        await _notify_chats(session, "status_changed",
                                            f"'{ticket.title}' перемещено в '{new_stage_name}'")
                if tags and not is_initial:
                    await _apply_tag_rules(session, ticket, tags)
                continue

            # -------- УЖЕ ЗНАЕМ ЗАДАЧУ --------
            # Если тикет помечен удалённым — пользователь (или предыдущий sync)
            # явно вывел его из отслеживания. Не трогаем: ни статусов, ни
            # уведомлений, ни «восстановлений», даже если задача всё ещё
            # существует в Bitrix24.
            if ticket.deleted:
                continue

            watch_fields = (await cfg_get("watch_field_changes")) == "1"
            if watch_fields:
                if title and title != ticket.title:
                    ticket.title = title
                if desc != ticket.text:
                    ticket.text = desc

            # завершение
            if is_completed and not ticket.completed:
                ticket.completed = True
                session.add(Status(text="Завершено", ticket=ticket))
                if not is_initial:
                    await _notify_chats(session, "status_changed",
                                        f"'{ticket.title}' завершено")

            # смена стадии — пишем Status только если реально отличается от last_status
            last = ticket.last_status
            if new_stage_name and (last is None or last.text != new_stage_name):
                session.add(Status(text=new_stage_name, ticket=ticket))
                if not is_initial:
                    await _notify_chats(session, "status_changed",
                                        f"'{ticket.title}' перемещено в '{new_stage_name}'")

            # теги: применяем правила, если есть теги, совпадающие с правилами
            if tags and not is_initial:
                applicable = (await session.execute(
                    select(TagRule).where(TagRule.tag.in_(tags))
                )).scalars().all()
                if applicable:
                    await _apply_tag_rules(session, ticket, tags)

            session.add(ticket)

        # -------- УДАЛЁННЫЕ В BITRIX --------
        rows = (await session.execute(
            select(Ticket).where(Ticket.gid.is_not(None), Ticket.deleted == False)
        )).scalars().all()
        for ticket in rows:
            if ticket.gid not in bx_ids:
                ticket.deleted = True
                ticket.deleted_at = datetime.now()
                session.add(ticket)
                session.add(Status(text="Удалено (обнаружено sync)", ticket=ticket))
                if not is_initial:
                    await _notify_chats(session, "deleted", f"'{ticket.title}' удалено")

    if is_initial:
        await cfg_set("sync_initial_done", "1")
        logging.info("Sync: первичный импорт завершён, флаг sync_initial_done=1")


async def _safe_sync_job() -> None:
    try:
        await sync_once()
    except Exception as e:
        logging.exception(f"sync: неожиданная ошибка цикла: {e}")


def start_scheduler(interval_seconds: Optional[int] = None) -> None:
    """Запускает периодическую синхронизацию. Идемпотентно."""
    global _scheduler, _job
    if _scheduler is not None:
        return
    interval = interval_seconds or _default_interval
    _scheduler = AsyncIOScheduler()
    _job = _scheduler.add_job(_safe_sync_job, IntervalTrigger(seconds=interval))
    _scheduler.start()
    logging.info(f"Sync: запущен с интервалом {interval} сек")


def reschedule(interval_seconds: int) -> None:
    global _job
    if _job is None or _scheduler is None:
        start_scheduler(interval_seconds)
        return
    _job.reschedule(trigger=IntervalTrigger(seconds=interval_seconds))
    logging.info(f"Sync: интервал изменён на {interval_seconds} сек")


def stop_scheduler() -> None:
    global _scheduler, _job
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        _job = None
        logging.info("Sync: остановлен")
