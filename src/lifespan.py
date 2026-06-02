from contextlib import asynccontextmanager
from functools import wraps
from typing import Callable
from fastapi import FastAPI
import logging
from database import Database
from bitrix import try_create_apis


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


@status_message("Выключение")
async def shutdown():
    pass
