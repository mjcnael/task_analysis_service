"""Базовый HTTP-клиент для Bitrix24 REST API через входящий вебхук."""
import logging
from typing import Any, Optional
import aiohttp
import asyncio
import config_manager


class BitrixApiError(Exception):
    def __init__(self, status: int, body: Any, method: str = ""):
        self.status = status
        self.body = body
        self.method = method
        super().__init__(f"Bitrix24 API Error {status} ({method}): {body}")

    def __str__(self):
        return f"Код: {self.status}\nМетод: {self.method}\nОписание: {self.body}"


def _normalize_webhook(url: str) -> str:
    """Гарантирует, что URL входящего вебхука заканчивается на '/'."""
    url = url.strip()
    if not url:
        return url
    if not url.endswith("/"):
        url += "/"
    return url


class BitrixClient:
    def __init__(self, webhook_url: str = ""):
        self.webhook_url = _normalize_webhook(webhook_url)

    def get_webhook(self) -> str:
        return self.webhook_url

    def _has_webhook(self) -> bool:
        return bool(self.webhook_url) and self.webhook_url.startswith("http")

    async def _check_webhook(self, url: str) -> bool:
        """Проверяет работоспособность входящего вебхука вызовом app.info."""
        url = _normalize_webhook(url)
        if not url.startswith("http"):
            return False
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(url + "app.info") as resp:
                    data = await resp.json(content_type=None)
                    if resp.status != 200:
                        return False
                    if isinstance(data, dict) and "error" in data:
                        # некоторые методы могут отдавать "error", но 200 — значит вебхук валиден
                        # тут считаем неуспехом только полностью невалидный URL
                        return False
                    return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.warning(f"Не удалось проверить webhook Bitrix24: {e}")
            return False
        except Exception as e:
            logging.error(f"Ошибка при проверке webhook Bitrix24: {e}")
            return False

    async def set_webhook(self, new_url: str) -> bool:
        ok = await self._check_webhook(new_url)
        if ok:
            self.webhook_url = _normalize_webhook(new_url)
            await config_manager.set("bitrix_webhook", self.webhook_url)
        return ok

    async def call(self, method: str, params: Optional[dict] = None,
                   timeout: Optional[aiohttp.ClientTimeout] = None) -> dict:
        """Универсальный вызов метода REST API Bitrix24."""
        if not self._has_webhook():
            raise BitrixApiError(0, "Webhook URL не настроен", method=method)

        url = self.webhook_url + method
        if timeout is None:
            timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=params or {}) as resp:
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        text = await resp.text()
                        raise BitrixApiError(resp.status, text, method=method)

                    if resp.status != 200 or (isinstance(data, dict) and data.get("error")):
                        raise BitrixApiError(resp.status, data, method=method)
                    return data
        except asyncio.TimeoutError:
            raise BitrixApiError(0, "Тайм-аут запроса", method=method)
        except aiohttp.ClientError as e:
            raise BitrixApiError(0, f"Сетевая ошибка: {e}", method=method)


bitrix_client = BitrixClient()


async def try_init() -> bool:
    """Загружает webhook из конфига и проверяет его."""
    global bitrix_client
    url = await config_manager.get("bitrix_webhook")
    if not url:
        logging.info("Webhook Bitrix24 не настроен")
        return False
    ok = await bitrix_client._check_webhook(url)
    if not ok:
        logging.warning("Сохранённый webhook Bitrix24 не отвечает")
        bitrix_client.webhook_url = _normalize_webhook(url)
        # всё равно используем — пользователь может его потом починить;
        # инициализированными модулями сможем работать
        return True
    bitrix_client.webhook_url = _normalize_webhook(url)
    return True
