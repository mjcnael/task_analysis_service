"""
Модуль интеграции с Bitrix24.

Использует входящий вебхук Bitrix24 для исходящих запросов
(создание задач, получение пользователей, групп, стадий).
Получение событий реализовано через исходящий вебхук Bitrix24
(см. web/routes/bitrix_events.py).
"""
from typing import Optional
from .client import BitrixClient, bitrix_client, try_init
from .tasks import TaskApi
from .projects import ProjectsApi
from .users import UsersApi
import logging

_task_api: Optional[TaskApi] = None
_projects_api: Optional[ProjectsApi] = None
_users_api: Optional[UsersApi] = None

initialized = False


def get_task_api() -> Optional[TaskApi]:
    return _task_api


def get_projects_api() -> Optional[ProjectsApi]:
    return _projects_api


def get_users_api() -> Optional[UsersApi]:
    return _users_api


async def try_create_apis() -> bool:
    """Пытается проинициализировать клиент и подмодули.
    Возвращает True если webhook URL валиден."""
    global _task_api, _projects_api, _users_api, initialized

    init_res = await try_init()
    if not init_res:
        initialized = False
        return False

    _task_api = TaskApi(bitrix_client)
    _projects_api = ProjectsApi(bitrix_client)
    _users_api = UsersApi(bitrix_client)
    initialized = True
    logging.info("Клиент Bitrix24 успешно инициализирован")
    return True
