"""Работа с проектами (workgroups), стадиями и тегами Bitrix24."""
from typing import List, Optional
from .client import BitrixClient, bitrix_client, BitrixApiError


class ProjectsApi:
    def __init__(self, client: BitrixClient = bitrix_client):
        self._client = client

    async def get_projects(self) -> List[dict]:
        """Возвращает список рабочих групп/проектов в формате,
        совместимом с шаблонами: [{'gid': str, 'name': str}, ...]."""
        result: List[dict] = []
        start = 0
        while True:
            res = await self._client.call("sonet_group.get", {"ORDER": {"NAME": "ASC"}, "start": start})
            data = res.get("result", []) or []
            for g in data:
                result.append({
                    "gid": str(g.get("ID")),
                    "name": g.get("NAME", f"Группа {g.get('ID')}"),
                })
            next_ = res.get("next")
            if next_ is None:
                break
            start = next_
            if start >= 500:  # safety
                break
        return result

    async def is_project(self, gid: str) -> bool:
        try:
            res = await self._client.call("sonet_group.get", {"FILTER": {"ID": gid}})
            return bool(res.get("result"))
        except BitrixApiError:
            return False

    async def get_sections(self, project_gid: str) -> List[dict]:
        """Возвращает стадии канбана для группы.
        Формат: [{'gid': str, 'name': str}, ...]."""
        if not project_gid:
            return []
        try:
            res = await self._client.call("task.stages.get", {
                "entityId": int(project_gid) if str(project_gid).isdigit() else project_gid,
                "isAdmin": "N",
            })
        except BitrixApiError:
            return []
        stages = res.get("result", {}) or {}
        out = []
        if isinstance(stages, dict):
            for sid, info in stages.items():
                if isinstance(info, dict):
                    out.append({"gid": str(sid), "name": info.get("TITLE", f"Стадия {sid}")})
        elif isinstance(stages, list):
            for info in stages:
                if isinstance(info, dict):
                    out.append({"gid": str(info.get("ID")), "name": info.get("TITLE", "")})
        return out

    async def get_tags(self) -> List[dict]:
        """Возвращает теги (по пользователю).
        Bitrix не имеет глобального справочника тегов как Asana;
        возвращаем уникальные теги, использованные в задачах."""
        try:
            res = await self._client.call("tasks.task.list", {
                "select": ["ID", "TAGS"],
            })
        except BitrixApiError:
            return []
        tasks = res.get("result", {}).get("tasks", []) or []
        seen = set()
        out = []
        for t in tasks:
            tags = t.get("tags") or t.get("TAGS") or []
            if isinstance(tags, list):
                for tag in tags:
                    name = tag if isinstance(tag, str) else tag.get("name") if isinstance(tag, dict) else None
                    if name and name not in seen:
                        seen.add(name)
                        out.append({"gid": name, "name": name})
        return out

    async def get_tasks(self, project_gid: str) -> List[dict]:
        params = {
            "filter": {"GROUP_ID": int(project_gid) if str(project_gid).isdigit() else project_gid},
            "select": ["ID", "TITLE", "STATUS", "STAGE_ID", "TAGS"],
        }
        res = await self._client.call("tasks.task.list", params)
        return res.get("result", {}).get("tasks", [])
