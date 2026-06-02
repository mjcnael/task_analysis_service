from fastapi import APIRouter, Request
from database import Database
from sqlalchemy import select
from database.models import Status
from .tickets import ticket_router
from .settings import settings_router
from .reports import reports_router


web_router = APIRouter()
web_router.include_router(ticket_router)
web_router.include_router(settings_router)
web_router.include_router(reports_router)


@web_router.get("/statuses")
async def statuses(request: Request):
    async with Database.make_session() as session:
        result = await session.execute(select(Status))
        return result.scalars().all()
