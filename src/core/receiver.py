"""Обработчик входящих данных формы Яндекс Форм:
- сохраняет тикет в БД
- создаёт задачу в Bitrix24 в проекте «Челлендж ОВ»
- назначает ответственным менеджера по наряду (поиск по ФИО через Bitrix API)
- отправляет уведомления в Telegram-чаты
"""

import logging
from typing import Dict, Any
from sqlalchemy import select

from database import Database
from database.models import TelegramConfigExtended, Ticket, Status
import bitrix
from bitrix import get_task_api, get_users_api
from bitrix.client import BitrixApiError
from config_manager import get
from tgbot import TgBot


class ReceiveError(Exception):
    """Ошибка обработки формы — поднимается с человекочитаемым сообщением."""


async def receive_form(data: Dict[str, Any]) -> Dict[str, Any]:
    """Обрабатывает данные формы. Возвращает информацию о созданной задаче.
    Исключения наружу пробрасываются для корректной обработки в роутах."""
    logging.info(f"Received form data: {data}")

    if not isinstance(data, dict):
        raise ReceiveError("Ожидался JSON-объект с ответами формы")

    # 1. Создаём тикет в БД
    try:
        ticket = Ticket.full_ticket_from_dict(data)
    except Exception as e:
        raise ReceiveError(f"Не удалось разобрать данные формы: {e}")

    async with Database.make_session() as session:
        session.add(ticket)
        await session.flush()  # чтобы у ticket появился id
        ticket_id = ticket.id

        if not bitrix.initialized:
            # сохраняем тикет, но без задачи в Bitrix24
            status_create = Status(text="Создано (Bitrix24 не настроен)", ticket=ticket)
            session.add(status_create)
            logging.warning(
                "Bitrix24 не инициализирован — задача не создана в Bitrix24"
            )
            return {
                "ticket_id": ticket_id,
                "bitrix_task_id": None,
                "warning": "Bitrix24 не настроен — задача сохранена только локально",
            }

        # 2. Подготовка к созданию задачи в Bitrix24
        group_id = await get("main_project_gid")  # проект «Челлендж ОВ»
        stage_id = await get("main_section")  # стадия по умолчанию
        if not group_id:
            status_create = Status(
                text="Создано (проект Bitrix24 не выбран)", ticket=ticket
            )
            session.add(status_create)
            return {
                "ticket_id": ticket_id,
                "bitrix_task_id": None,
                "warning": "Не выбран проект Bitrix24 в настройках",
            }

        # 3. Поиск ответственного по ФИО
        manager_name = (
            ticket.additional_info.manager if ticket.additional_info else None
        )
        users_api = get_users_api()
        responsible_id = None
        if manager_name and users_api:
            try:
                user = await users_api.find_by_fullname(manager_name)
                if user:
                    responsible_id = user.get("ID") or user.get("id")
            except BitrixApiError as e:
                logging.warning(
                    f"Ошибка поиска менеджера '{manager_name}' в Bitrix24: {e}"
                )

        if not responsible_id:
            # Фолбэк: дефолтный ответственный из конфигурации
            responsible_id = await get("default_responsible_id")

        if not responsible_id:
            status_create = Status(
                text=f"Создано (менеджер '{manager_name}' не найден в Bitrix24)",
                ticket=ticket,
            )
            session.add(status_create)
            return {
                "ticket_id": ticket_id,
                "bitrix_task_id": None,
                "warning": f"Менеджер '{manager_name}' не найден в Bitrix24, "
                f"задача не создана. Назначьте дефолтного ответственного в настройках.",
            }

        # 4. Создание задачи в Bitrix24
        task_api = get_task_api()
        if task_api is None:
            raise ReceiveError("Task API Bitrix24 не инициализирован")

        try:
            task = await task_api.create_task(
                ticket=ticket,
                group_id=group_id,
                responsible_id=str(responsible_id),
                stage_id=stage_id or None,
            )
        except BitrixApiError as e:
            logging.error(f"Не удалось создать задачу в Bitrix24: {e}")
            status_err = Status(text=f"Ошибка создания в Bitrix24: {e}", ticket=ticket)
            session.add(status_err)
            return {
                "ticket_id": ticket_id,
                "bitrix_task_id": None,
                "error": f"Bitrix24: {e}",
            }

        # 5. Сохранение результата
        task_bitrix_id = str(task.get("id"))
        ticket.gid = task_bitrix_id

        status_create = Status(text="Создано", ticket=ticket)
        session.add(status_create)
        if stage_id:
            stage_name = await _resolve_stage_name(group_id, stage_id)
            status_stage = Status(text=stage_name or f"Stage {stage_id}", ticket=ticket)
            session.add(status_stage)

        session.add(ticket)

        # 6. Уведомления в Telegram
        await _notify_telegram(session, ticket)

        return {
            "ticket_id": ticket_id,
            "bitrix_task_id": task_bitrix_id,
            "responsible_id": str(responsible_id),
        }


async def _resolve_stage_name(group_id: str, stage_id: str) -> str:
    try:
        from bitrix import get_projects_api

        papi = get_projects_api()
        if papi is None:
            return ""
        for s in await papi.get_sections(group_id):
            if str(s.get("gid")) == str(stage_id):
                return s.get("name", "")
    except Exception as e:
        logging.warning(f"Не удалось получить имя стадии: {e}")
    return ""


async def _notify_telegram(session, ticket: Ticket) -> None:
    try:
        chats_short = (
            (
                await session.execute(
                    select(TelegramConfigExtended).where(
                        TelegramConfigExtended.created == True
                    )
                )
            )
            .scalars()
            .all()
        )
        for chat in chats_short:
            await TgBot.send_message(chat.chat_id, f"Получено '{ticket.title}'")

        chats_full = (
            (
                await session.execute(
                    select(TelegramConfigExtended).where(
                        TelegramConfigExtended.created_full == True
                    )
                )
            )
            .scalars()
            .all()
        )
        for chat in chats_full:
            await TgBot.send_message(
                chat.chat_id, f"Получен челлендж:\n\n{str(ticket)}"
            )
    except Exception as e:
        logging.warning(f"Ошибка отправки уведомлений в Telegram: {e}")
