from contextlib import asynccontextmanager
from functools import wraps
from typing import Callable
from fastapi import FastAPI
import logging
from database import Database
from bitrix import try_create_apis
from core.sync import start_scheduler, stop_scheduler, sync_once
from config_manager import get as cfg_get


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()
    yield
    await shutdown()


def status_message(text: str):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            logging.info("Начато: %s", text)
            await func(*args, **kwargs)
            logging.info("Завершено: %s", text)
        return wrapper
    return decorator


@status_message("Инициализация")
async def startup():
    await Database.create_all()
    try:
        await try_create_apis()
    except Exception as e:
        logging.error(f"Не удалось инициализировать клиент Bitrix24 при старте: {e}")

    # фоновая синхронизация с Bitrix24
    sync_enabled = (await cfg_get("sync_enabled")) != "0"  # включена по умолчанию
    if sync_enabled:
        try:
            interval = int(await cfg_get("sync_interval") or 60)
        except (TypeError, ValueError):
            interval = 60
        start_scheduler(interval)
        # одноразовый catch-up прямо на старте
        try:
            await sync_once()
        except Exception as e:
            logging.warning(f"Ошибка стартовой синхронизации: {e}")


@status_message("Выключение")
async def shutdown():
    stop_scheduler()
