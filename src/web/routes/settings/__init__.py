from fastapi import APIRouter
from .general import settings_router as general_settings_router
from .telegram import telegram_settings_router

settings_router = APIRouter()
settings_router.include_router(general_settings_router)
settings_router.include_router(telegram_settings_router)
