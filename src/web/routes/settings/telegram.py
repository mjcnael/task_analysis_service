import logging
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from starlette.status import HTTP_303_SEE_OTHER

import bitrix
from bitrix import bitrix_client
from bitrix.projects import ProjectsApi
from bitrix.client import BitrixApiError

from ...templates import settings_template
from config_manager import set as cfg_set, get as cfg_get
from database import Database
from database.models import TelegramConfig, TelegramConfigExtended
from tgbot import TgBot

telegram_settings_router = APIRouter(prefix="/settings/telegram")


@telegram_settings_router.get("/")
async def telegram_settings(request: Request):
    telegram_token = await cfg_get("telegram_token") or ""

    notify_created = (await cfg_get("notify_created")) == "1"
    notify_created_full = (await cfg_get("notify_created_full")) == "1"
    notify_status_changed = (await cfg_get("notify_status_changed")) == "1"
    notify_deleted = (await cfg_get("notify_deleted")) == "1"
    notify_sub_tag_setted = (await cfg_get("notify_sub_tag_setted")) == "1"
    notify_commented = (await cfg_get("notify_commented")) == "1"

    async with Database.make_session() as session:
        chats = (
            (
                await session.execute(
                    select(TelegramConfig).where(
                        TelegramConfig.destination_type == "chat"
                    )
                )
            )
            .scalars()
            .all()
        )
        users = (
            (
                await session.execute(
                    select(TelegramConfig).where(
                        TelegramConfig.destination_type == "user"
                    )
                )
            )
            .scalars()
            .all()
        )
        chats_str = "\n".join(c.destination_id for c in chats)
        users_str = "\n".join(u.destination_id for u in users)

        chat_rules = (
            (await session.execute(select(TelegramConfigExtended))).scalars().all()
        )
        chat_ids = [cr.chat_id for cr in chat_rules]
        chat_titles = await TgBot.get_chats_titles(chat_ids) if chat_ids else []

    telegram_token_error = request.session.pop("telegram_token_error", None)
    telegram_token_error_message = request.session.pop(
        "telegram_token_error_message", None
    )
    saved = request.session.pop("saved", None)
    if telegram_token_error:
        saved = False

    projects = await get_watched_projects()

    return settings_template(
        "telegram.html",
        {
            "request": request,
            "telegram_token": telegram_token,
            "users": users_str,
            "chats": chats_str,
            "notify_created": notify_created,
            "notify_created_full": notify_created_full,
            "notify_status_changed": notify_status_changed,
            "notify_deleted": notify_deleted,
            "notify_sub_tag_setted": notify_sub_tag_setted,
            "notify_commented": notify_commented,
            "saved": saved,
            "telegram_token_error": telegram_token_error,
            "telegram_token_error_message": telegram_token_error_message,
            "chat_rules": chat_rules,
            "chat_titles": chat_titles,
            "projects": projects,
        },
    )


@telegram_settings_router.post("/")
async def telegram_settings_submit(request: Request):
    form = await request.form()
    bot_token = form.get("telegram_token")
    if bot_token:
        ok, msg = await TgBot.check_token(bot_token)
        if ok:
            await cfg_set("telegram_token", bot_token)
        else:
            request.session["telegram_token_error"] = True
            request.session["telegram_token_error_message"] = msg

    await cfg_set("notify_created", "1" if form.get("created") is not None else "0")
    await cfg_set(
        "notify_created_full", "1" if form.get("created_full") is not None else "0"
    )
    await cfg_set(
        "notify_status_changed", "1" if form.get("status_changed") is not None else "0"
    )
    await cfg_set("notify_deleted", "1" if form.get("deleted") is not None else "0")
    await cfg_set(
        "notify_sub_tag_setted", "1" if form.get("sub_tag_setted") is not None else "0"
    )
    await cfg_set("notify_commented", "1" if form.get("commented") is not None else "0")

    chats = (form.get("chats") or "").split("\r\n")
    users = (form.get("users") or "").split("\r\n")

    async with Database.make_session() as session:
        await session.execute(delete(TelegramConfig))
        for chat in chats:
            chat = chat.strip()
            if chat and chat.startswith("-"):
                session.add(
                    TelegramConfig(destination_id=chat, destination_type="chat")
                )
        for user in users:
            user = user.strip()
            if user and user.startswith("@"):
                session.add(
                    TelegramConfig(destination_id=user, destination_type="user")
                )

    request.session["saved"] = True
    return RedirectResponse(
        telegram_settings_router.prefix, status_code=HTTP_303_SEE_OTHER
    )


async def get_watched_projects() -> list:
    if not bitrix.initialized:
        return []
    try:
        all_projects = await ProjectsApi(bitrix_client).get_projects()
    except BitrixApiError:
        return []

    result = []
    main_project = await cfg_get("main_project_gid")
    for p in all_projects:
        if p["gid"] == main_project:
            result.append(p)
            break
    listen = await cfg_get("listen_projects")
    if listen:
        listen_ids = listen.split(" ")
        for p in all_projects:
            if p["gid"] in listen_ids and p not in result:
                result.append(p)
    return result


@telegram_settings_router.get("/new-chat-settings")
async def new_chat_settings_page(request: Request):
    error_message = request.session.pop("new_tg_chat_error_message", None)
    success_message = request.session.pop("new_tg_chat_success_message", None)
    projects = await get_watched_projects()
    return settings_template(
        "telegram/chat_settings.html",
        {
            "request": request,
            "error_message": error_message,
            "success_message": success_message,
            "projects": projects,
        },
    )


@telegram_settings_router.post("/new-chat-settings")
async def submit_new_chat_settings(request: Request):
    form = await request.form()
    chat_id = (form.get("chat_id") or "").strip()
    project_id = (form.get("project") or "").strip()

    created = form.get("created") is not None
    created_full = form.get("created_full") is not None
    status_changed = form.get("status_changed") is not None
    deleted = form.get("deleted") is not None
    sub_tag_setted = form.get("sub_tag_setted") is not None
    commented = form.get("commented") is not None

    ok, msg = await TgBot.get_chat_title(chat_id)
    setting = None
    if not ok:
        request.session["new_tg_chat_error_message"] = msg
    else:
        async with Database.make_session() as session:
            existing = (
                (
                    await session.execute(
                        select(TelegramConfigExtended).where(
                            TelegramConfigExtended.chat_id == chat_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            if any(r.additional == project_id for r in existing):
                request.session["new_tg_chat_error_message"] = (
                    f'Для чата "{msg}" (ID {chat_id}) уже настроены уведомления для этого проекта'
                )
                return RedirectResponse(
                    telegram_settings_router.prefix + "/new-chat-settings",
                    status_code=HTTP_303_SEE_OTHER,
                )
            request.session["new_tg_chat_success_message"] = (
                f"Успешно сохранена настройка уведомлений для чата '{msg}'"
            )
            setting = TelegramConfigExtended(
                chat_id=chat_id,
                created=created,
                created_full=created_full,
                status_changed=status_changed,
                deleted=deleted,
                sub_tag_setted=sub_tag_setted,
                commented=commented,
                additional=project_id,
            )
            session.add(setting)
            await session.flush()
            setting_id = setting.id

    if setting is None:
        return RedirectResponse(
            telegram_settings_router.prefix + "/new-chat-settings",
            status_code=HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        telegram_settings_router.prefix + f"/chat-settings/{setting_id}",
        status_code=HTTP_303_SEE_OTHER,
    )


@telegram_settings_router.get("/chat-settings/{setting_id}")
async def show_chat_settings_page(request: Request, setting_id: int):
    success_message = request.session.pop("new_tg_chat_success_message", None)
    async with Database.make_session() as session:
        chat_setting = (
            (
                await session.execute(
                    select(TelegramConfigExtended).where(
                        TelegramConfigExtended.id == setting_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
    projects = await get_watched_projects()
    return settings_template(
        "telegram/chat_settings.html",
        {
            "request": request,
            "chat_setting": chat_setting,
            "success_message": success_message,
            "selected_project": chat_setting.additional if chat_setting else "",
            "projects": projects,
        },
    )


@telegram_settings_router.post("/chat-settings/{setting_id}")
async def update_chat_settings(request: Request, setting_id: int):
    form = await request.form()
    project_id = (form.get("project") or "").strip()
    async with Database.make_session() as session:
        s = (
            (
                await session.execute(
                    select(TelegramConfigExtended).where(
                        TelegramConfigExtended.id == setting_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if s is not None:
            s.created = form.get("created") is not None
            s.created_full = form.get("created_full") is not None
            s.status_changed = form.get("status_changed") is not None
            s.deleted = form.get("deleted") is not None
            s.sub_tag_setted = form.get("sub_tag_setted") is not None
            s.commented = form.get("commented") is not None
            s.additional = project_id
            session.add(s)
            request.session["new_tg_chat_success_message"] = "Сохранено"
    return RedirectResponse(
        telegram_settings_router.prefix + f"/chat-settings/{setting_id}",
        status_code=HTTP_303_SEE_OTHER,
    )


@telegram_settings_router.post("/chat-settings/{setting_id}/delete")
async def delete_chat_settings(request: Request, setting_id: int):
    async with Database.make_session() as session:
        s = (
            (
                await session.execute(
                    select(TelegramConfigExtended).where(
                        TelegramConfigExtended.id == setting_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if s is not None:
            await session.delete(s)
            await session.flush()
    return RedirectResponse(
        telegram_settings_router.prefix, status_code=HTTP_303_SEE_OTHER
    )
