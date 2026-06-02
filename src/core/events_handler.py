"""Обработка событий, приходящих от Bitrix24 через исходящий вебхук.

Сохраняет в БД историю изменений задач, обновляет тикеты, рассылает
уведомления в Telegram (analog Asana-events из исходной версии).
"""
import logging
from typing import Any, Dict, Optional
from sqlalchemy import select

from database import Database
from database.models import Ticket, Status, TagRule, TelegramConfigExtended
from bitrix import get_task_api
from bitrix.client import BitrixApiError
from config_manager import get
from tgbot import TgBot


def _extract_event_name(payload: Dict[str, Any]) -> str:
    """Bitrix отдаёт имя события в поле 'event' (например ONTASKUPDATE)."""
    return (payload.get("event") or payload.get("EVENT") or "").upper()


def _extract_task_id(payload: Dict[str, Any]) -> Optional[str]:
    """Достаёт id задачи из произвольной формы payload."""
    # Возможные пути в зависимости от события:
    # data[FIELDS_AFTER][ID]  /  data[FIELDS][ID]  /  data[TASK_ID]
    for key in ("data[FIELDS_AFTER][ID]", "data[FIELDS][ID]",
                "data[TASK_ID]", "data[FIELDS_BEFORE][ID]"):
        if key in payload:
            return str(payload[key])
    data = payload.get("data") or {}
    if isinstance(data, dict):
        for k in ("FIELDS_AFTER", "FIELDS", "FIELDS_BEFORE"):
            sub = data.get(k)
            if isinstance(sub, dict) and "ID" in sub:
                return str(sub["ID"])
        if "TASK_ID" in data:
            return str(data["TASK_ID"])
    return None


async def handle_bitrix_event(payload: Dict[str, Any]) -> None:
    """Точка входа для исходящего вебхука Bitrix24."""
    event = _extract_event_name(payload)
    task_id = _extract_task_id(payload)
    logging.info(f"Bitrix event: {event}, task_id={task_id}")

    if not event or not task_id:
        logging.warning(f"Не удалось распознать событие Bitrix24: {payload}")
        return

    if event in ("ONTASKADD",):
        await _on_task_add(task_id)
    elif event in ("ONTASKUPDATE",):
        await _on_task_update(task_id)
    elif event in ("ONTASKDELETE",):
        await _on_task_delete(task_id)
    elif event in ("ONTASKCOMMENTADD",):
        comment = (payload.get("data[COMMENT][POST_MESSAGE]")
                   or (payload.get("data") or {}).get("COMMENT", {}).get("POST_MESSAGE", ""))
        await _on_comment_add(task_id, comment)
    else:
        logging.info(f"Событие {event} не обрабатывается")


async def _fetch_task(task_id: str) -> Optional[dict]:
    task_api = get_task_api()
    if task_api is None:
        return None
    try:
        return await task_api.get_task(task_id)
    except BitrixApiError as e:
        logging.warning(f"Не удалось получить задачу {task_id}: {e}")
        return None


async def _on_task_add(task_id: str) -> None:
    """Если задачу создал кто-то напрямую в Bitrix24 — подхватываем её, если включено."""
    watch = (await get("watch_tasks")) == "1"
    if not watch:
        return
    task = await _fetch_task(task_id)
    if not task:
        return
    async with Database.make_session() as session:
        existing = await Ticket.get_by_gid(session, task_id)
        if existing is not None:
            return
        ticket = Ticket(
            gid=task_id,
            title=task.get("title", f"Bitrix task {task_id}"),
            text=task.get("description", "") or "",
        )
        session.add(ticket)
        session.add(Status(text="Создано (из Bitrix24)", ticket=ticket))


async def _on_task_update(task_id: str) -> None:
    task = await _fetch_task(task_id)
    if not task:
        return

    new_stage = str(task.get("stageId") or task.get("STAGE_ID") or "")
    is_completed = str(task.get("status", "")) in ("5",)  # 5 = завершена в Bitrix24

    async with Database.make_session() as session:
        ticket = await Ticket.get_by_gid(session, task_id)
        if ticket is None:
            return

        # обновляем поля
        title = task.get("title") or ticket.title
        desc = task.get("description") or ticket.text
        if title != ticket.title or desc != ticket.text:
            watch_fields = (await get("watch_field_changes")) == "1"
            if watch_fields:
                ticket.title = title
                ticket.text = desc

        if is_completed and not ticket.completed:
            ticket.completed = True
            session.add(Status(text="Завершено", ticket=ticket))

        # изменение стадии
        last = ticket.last_status
        if new_stage and (last is None or not (last.text or "").startswith(f"Stage {new_stage}")):
            from bitrix import get_projects_api
            papi = get_projects_api()
            stage_name = f"Stage {new_stage}"
            if papi is not None:
                try:
                    for s in await papi.get_sections(str(task.get("groupId") or "")):
                        if str(s.get("gid")) == new_stage:
                            stage_name = s.get("name") or stage_name
                            break
                except Exception:
                    pass
            if last is None or last.text != stage_name:
                session.add(Status(text=stage_name, ticket=ticket))
                await _notify_chats(session, "status_changed",
                                    f"'{ticket.title}' перемещено в '{stage_name}'")

        # обработка тегов
        new_tags = task.get("tags") or task.get("TAGS") or []
        if new_tags:
            await _apply_tag_rules(session, ticket, new_tags)

        session.add(ticket)


async def _on_task_delete(task_id: str) -> None:
    from datetime import datetime
    async with Database.make_session() as session:
        ticket = await Ticket.get_by_gid(session, task_id)
        if ticket is None:
            return
        ticket.deleted = True
        ticket.deleted_at = datetime.now()
        session.add(ticket)
        session.add(Status(text="Удалено", ticket=ticket))
        await _notify_chats(session, "deleted", f"'{ticket.title}' удалено")


async def _on_comment_add(task_id: str, comment_text: str) -> None:
    async with Database.make_session() as session:
        ticket = await Ticket.get_by_gid(session, task_id)
        if ticket is None:
            return
        text = f"На '{ticket.title}' добавлен комментарий"
        if comment_text:
            text += f": {comment_text}"
        await _notify_chats(session, "commented", text)


async def _apply_tag_rules(session, ticket: Ticket, tags: list) -> None:
    tag_names = []
    for t in tags:
        if isinstance(t, str):
            tag_names.append(t)
        elif isinstance(t, dict):
            n = t.get("name") or t.get("NAME")
            if n:
                tag_names.append(n)

    if not tag_names:
        return

    rules = (await session.execute(
        select(TagRule).where(TagRule.tag.in_(tag_names))
    )).scalars().all()

    if not rules:
        return

    task_api = get_task_api()
    if task_api is None:
        return

    for rule in rules:
        # переносим/добавляем в другую группу/стадию
        try:
            fields = {"GROUP_ID": int(rule.project_gid) if rule.project_gid.isdigit() else rule.project_gid}
            if rule.section_gid:
                fields["STAGE_ID"] = int(rule.section_gid) if rule.section_gid.isdigit() else rule.section_gid
            await task_api.update_task(ticket.gid, fields)
            session.add(Status(
                text=f"Перемещено по правилу тега '{rule.tag}'", ticket=ticket
            ))
            await _notify_chats(
                session, "sub_tag_setted",
                f"На '{ticket.title}' тег '{rule.tag}': задача перенесена в '{rule.project_name or rule.project_gid}'"
            )
        except BitrixApiError as e:
            logging.warning(f"Не удалось применить правило тега {rule.tag}: {e}")


async def _notify_chats(session, flag: str, text: str) -> None:
    column = {
        "status_changed": TelegramConfigExtended.status_changed,
        "deleted": TelegramConfigExtended.deleted,
        "commented": TelegramConfigExtended.commented,
        "sub_tag_setted": TelegramConfigExtended.sub_tag_setted,
        "created": TelegramConfigExtended.created,
        "created_full": TelegramConfigExtended.created_full,
    }.get(flag)
    if column is None:
        return
    chats = (await session.execute(select(TelegramConfigExtended).where(column == True))).scalars().all()
    for c in chats:
        try:
            await TgBot.send_message(c.chat_id, text)
        except Exception as e:
            logging.warning(f"Не удалось отправить в чат {c.chat_id}: {e}")
