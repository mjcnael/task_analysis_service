"""Работа с задачами Bitrix24."""
import logging
from typing import Optional, List, Any
from .client import BitrixClient, bitrix_client, BitrixApiError
from database.models import Ticket


def _result_dict(res: Any) -> dict:
    """Достаёт res['result'] как dict.

    Bitrix (PHP) при пустом ответе отдаёт result в виде пустого СПИСКА [],
    а не объекта {}. Прямой .get() на списке ронял обработчик
    ('list' object has no attribute 'get').
    """
    if not isinstance(res, dict):
        return {}
    result = res.get("result")
    return result if isinstance(result, dict) else {}


class TaskApi:
    def __init__(self, client: BitrixClient = bitrix_client):
        self._client = client

    @staticmethod
    def _build_description(ticket: Ticket) -> str:
        info = ticket.additional_info
        if info is None:
            return ticket.text or ""
        return (
            f"ФИО Внедренца: {info.worker_fullname}\n"
            f"Номер наряда из К7: {info.k7_id}\n"
            f"Офис: {info.office}\n"
            f"Менеджер по наряду: {info.manager}\n"
            f"Клиент: {info.client}\n\n"
            f"Задачи:\n{info.ticket.text}"
        )

    async def create_task(self,
                          ticket: Ticket,
                          group_id: str,
                          responsible_id: str,
                          stage_id: Optional[str] = None,
                          tags: Optional[List[str]] = None) -> dict:
        """Создаёт задачу в Bitrix24. Возвращает поля задачи (минимум id)."""
        fields: dict = {
            "TITLE": ticket.title,
            "DESCRIPTION": self._build_description(ticket),
            "RESPONSIBLE_ID": int(responsible_id) if str(responsible_id).isdigit() else responsible_id,
            "GROUP_ID": int(group_id) if str(group_id).isdigit() else group_id,
        }
        if stage_id:
            fields["STAGE_ID"] = int(stage_id) if str(stage_id).isdigit() else stage_id
        if tags:
            fields["TAGS"] = tags

        res = await self._client.call("tasks.task.add", {"fields": fields})
        task = _result_dict(res).get("task") or {}
        if not isinstance(task, dict) or not (task.get("id") or task.get("ID")):
            raise BitrixApiError(0, f"Bitrix не вернул id задачи: {res}", method="tasks.task.add")
        return task

    async def get_task(self, task_id: str) -> dict:
        res = await self._client.call("tasks.task.get", {
            "taskId": int(task_id) if str(task_id).isdigit() else task_id
        })
        task = _result_dict(res).get("task")
        return task if isinstance(task, dict) else {}

    async def update_task(self, task_id: str, fields: dict) -> dict:
        params = {
            "taskId": int(task_id) if str(task_id).isdigit() else task_id,
            "fields": fields,
        }
        res = await self._client.call("tasks.task.update", params)
        task = _result_dict(res).get("task")
        return task if isinstance(task, dict) else {}

    async def list_tasks_by_group(self, group_id: str) -> List[dict]:
        params = {
            "filter": {"GROUP_ID": int(group_id) if str(group_id).isdigit() else group_id},
            "select": ["ID", "TITLE", "STATUS", "STAGE_ID", "TAGS"],
        }
        res = await self._client.call("tasks.task.list", params)
        tasks = _result_dict(res).get("tasks")
        return tasks if isinstance(tasks, list) else []

    async def add_comment(self, task_id: str, text: str) -> dict:
        params = {
            "TASKID": int(task_id) if str(task_id).isdigit() else task_id,
            "FIELDS": {"POST_MESSAGE": text},
        }
        return await self._client.call("task.commentitem.add", params)
