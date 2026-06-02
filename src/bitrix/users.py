"""Поиск пользователей Bitrix24 (для определения ответственного по задаче)."""
import logging
from typing import Optional, List
from .client import BitrixClient, bitrix_client, BitrixApiError


def _split_name(full: str) -> List[str]:
    return [p.strip() for p in full.replace("\xa0", " ").split() if p.strip()]


class UsersApi:
    def __init__(self, client: BitrixClient = bitrix_client):
        self._client = client

    async def find_by_fullname(self, fullname: str) -> Optional[dict]:
        """Ищет пользователя по ФИО. Возвращает словарь с полями
        пользователя Bitrix24 или None, если не найден."""
        fullname = (fullname or "").strip()
        if not fullname:
            return None

        parts = _split_name(fullname)

        # 1) Пробуем user.search (умеет искать по ФИО как единой строке)
        try:
            res = await self._client.call("user.search", {"NAME": fullname})
            users = res.get("result", []) or []
            user = _pick_best(users, parts)
            if user:
                return user
        except BitrixApiError as e:
            logging.warning(f"user.search не сработал: {e}")

        # 2) Фолбэк: user.get с фильтром по фамилии/имени
        try:
            filt: dict = {}
            if len(parts) >= 1:
                filt["LAST_NAME"] = parts[0]
            if len(parts) >= 2:
                filt["NAME"] = parts[1]
            res = await self._client.call("user.get", {"FILTER": filt})
            users = res.get("result", []) or []
            user = _pick_best(users, parts)
            if user:
                return user
        except BitrixApiError as e:
            logging.warning(f"user.get не сработал: {e}")

        # 3) Дополнительный фолбэк: возможно пользователь указан как "Имя Фамилия"
        if len(parts) >= 2:
            try:
                res = await self._client.call("user.get", {
                    "FILTER": {"NAME": parts[0], "LAST_NAME": parts[1]}
                })
                users = res.get("result", []) or []
                user = _pick_best(users, parts)
                if user:
                    return user
            except BitrixApiError as e:
                logging.warning(f"user.get (alt) не сработал: {e}")

        return None

    async def get(self, user_id: str) -> Optional[dict]:
        try:
            res = await self._client.call("user.get", {"ID": user_id})
            users = res.get("result", []) or []
            if users:
                return users[0]
        except BitrixApiError as e:
            logging.warning(f"user.get(id={user_id}) не сработал: {e}")
        return None


def _pick_best(users: list, name_parts: List[str]) -> Optional[dict]:
    if not users:
        return None
    if len(users) == 1:
        return users[0]
    # Если несколько — пытаемся найти точное совпадение по всем частям ФИО
    name_set = {p.lower() for p in name_parts}
    for u in users:
        candidate = " ".join(filter(None, [
            u.get("LAST_NAME", ""), u.get("NAME", ""), u.get("SECOND_NAME", "")
        ])).lower()
        if all(p in candidate for p in name_set):
            return u
    return users[0]
