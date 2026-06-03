"""Настройки интеграции с Bitrix24."""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from starlette.status import HTTP_303_SEE_OTHER

import bitrix
from bitrix import bitrix_client, get_projects_api, get_users_api, try_create_apis
from bitrix.client import BitrixApiError
from bitrix.projects import ProjectsApi

from config_manager import set as cfg_set, get as cfg_get
from database import Database
from database.models import TagRule

from ...templates import settings_template


settings_router = APIRouter(prefix="/settings")


@settings_router.get("/")
async def settings_index(request: Request):
    return RedirectResponse(
        settings_router.prefix + "/bitrix/", status_code=HTTP_303_SEE_OTHER
    )


# ---------- Главные настройки Bitrix24 ----------


@settings_router.get("/bitrix")
@settings_router.get("/bitrix/")
async def bitrix_settings(request: Request):
    webhook = bitrix_client.get_webhook()
    data = request.session.pop("data", None)

    projects = []
    sections = []
    main_project_gid = await cfg_get("main_project_gid")
    main_section_gid = await cfg_get("main_section")
    default_responsible_id = await cfg_get("default_responsible_id") or ""

    if bitrix.initialized:
        try:
            projects = await ProjectsApi(bitrix_client).get_projects()
            if data is None:
                data = {}
            data["success"] = True
            data["selected_main"] = main_project_gid or ""
            if main_project_gid:
                sections = await ProjectsApi(bitrix_client).get_sections(
                    main_project_gid
                )
                data["project_set"] = True
        except BitrixApiError as e:
            if data is None:
                data = {}
            data["success"] = False
            data["message"] = f"Ошибка обращения к Bitrix24: {e}"
            logging.warning(f"Ошибка получения проектов: {e}")

    return settings_template(
        "bitrix_main.html",
        {
            "request": request,
            "webhook": webhook,
            "data": data,
            "projects": projects,
            "sections": sections,
            "selected_section": main_section_gid or "",
            "default_responsible_id": default_responsible_id,
        },
    )


@settings_router.post("/bitrix")
@settings_router.post("/bitrix/")
async def bitrix_settings_submit(request: Request):
    form = await request.form()
    data: dict = {}

    webhook = (form.get("bitrix_webhook") or "").strip()
    if webhook:
        ok = await bitrix_client.set_webhook(webhook)
        data["success"] = ok
        data["message"] = (
            "Webhook сохранён" if ok else "Не удалось проверить webhook. Проверьте URL."
        )
        if ok:
            await try_create_apis()

    main_project = (form.get("main_project") or "").strip()
    main_section = (form.get("section") or "").strip()
    default_responsible = (form.get("default_responsible_id") or "").strip()

    if bitrix.initialized and main_project:
        try:
            is_proj = await ProjectsApi(bitrix_client).is_project(main_project)
        except BitrixApiError:
            is_proj = False

        if is_proj:
            await cfg_set("main_project_gid", main_project)
            data["project_set"] = True
            data["project_success"] = True
            data["project_message_1"] = "Сохранено"
            data["selected_main"] = main_project
            if main_section:
                await cfg_set("main_section", main_section)
                data["section_success"] = True
                data["project_message_2"] = "Сохранено"
            else:
                data["project_message_2"] = "Выберите стадию"
        else:
            data["project_message_1"] = "Проект не найден"

    if default_responsible:
        await cfg_set("default_responsible_id", default_responsible)

    request.session["data"] = data
    return RedirectResponse(
        settings_router.prefix + "/bitrix/", status_code=HTTP_303_SEE_OTHER
    )


# ---------- AJAX: подгрузка стадий ----------
@settings_router.get("/bitrix/project_sections")
async def get_project_sections(
    request: Request, project: str = "", main_project: str = ""
):
    if not project:
        project = main_project
    sections = []
    if project:
        try:
            sections = await ProjectsApi(bitrix_client).get_sections(project)
        except BitrixApiError as e:
            logging.warning(f"Не удалось получить стадии: {e}")
    return settings_template(
        "tag_rules/section_select.html",
        {
            "request": request,
            "sections": sections,
        },
    )


# ---------- Правила тегов ----------


@settings_router.get("/bitrix/tag-rules")
@settings_router.get("/bitrix/tag-rules/")
async def tag_rules_list(request: Request):
    async with Database.make_session() as session:
        tag_rules = (await session.execute(select(TagRule))).scalars().all()

    avail_projects = []
    main_project_gid = await cfg_get("main_project_gid")
    listen = await cfg_get("listen_projects")
    listen_projects = listen.split(" ") if listen else []

    if bitrix.initialized:
        try:
            avail_projects = await ProjectsApi(bitrix_client).get_projects()
        except BitrixApiError as e:
            logging.warning(f"Не удалось получить проекты: {e}")

    return settings_template(
        "tag_rules/list.html",
        {
            "request": request,
            "tag_rules": tag_rules,
            "initialized": bitrix.initialized,
            "avail_projects": avail_projects,
            "listen_projects": listen_projects,
            "main_project_gid": main_project_gid,
        },
    )


@settings_router.post("/bitrix/projects-listening")
async def post_projects_listening(request: Request):
    form = await request.form()
    listen = form.getlist("listen")
    listen_str = " ".join(str(x) for x in listen)
    await cfg_set("listen_projects", listen_str)
    logging.info(f"Обновлены отслеживаемые проекты: {listen_str}")
    return RedirectResponse(
        settings_router.prefix + "/bitrix/tag-rules/", status_code=HTTP_303_SEE_OTHER
    )


@settings_router.post("/bitrix/tag-rules/rule-delete/{rule_id}")
async def delete_tag_rule(request: Request, rule_id: int):
    async with Database.make_session() as session:
        rule = (
            (await session.execute(select(TagRule).where(TagRule.id == rule_id)))
            .scalars()
            .one_or_none()
        )
        if rule is not None:
            await session.delete(rule)
            await session.flush()
    return RedirectResponse(
        settings_router.prefix + "/bitrix/tag-rules/", status_code=HTTP_303_SEE_OTHER
    )


@settings_router.get("/bitrix/tag-rules/rule/new")
async def new_tag_rule(request: Request):
    projects = []
    tags: set = set()
    if bitrix.initialized:
        try:
            projects = await ProjectsApi(bitrix_client).get_projects()
            for t in await ProjectsApi(bitrix_client).get_tags():
                tags.add(t["name"])
        except BitrixApiError as e:
            logging.warning(f"Не удалось получить теги/проекты: {e}")
    async with Database.make_session() as session:
        used = {r.tag for r in (await session.execute(select(TagRule))).scalars().all()}
    tags -= used

    return settings_template(
        "tag_rules/rule.html",
        {
            "request": request,
            "tags": tags,
            "data": {},
            "projects": projects,
            "tag_rule": None,
        },
    )


@settings_router.post("/bitrix/tag-rules/rule/new")
async def new_tag_rule_submit(request: Request):
    form = await request.form()
    tag = (form.get("tag") or "").strip()
    action = int(form.get("action") or 0)
    project = (form.get("project") or "").strip()
    section = (form.get("section") or "").strip()

    if not tag or not project:
        return RedirectResponse(
            settings_router.prefix + "/bitrix/tag-rules/",
            status_code=HTTP_303_SEE_OTHER,
        )

    project_name = ""
    section_name = ""
    if bitrix.initialized:
        try:
            for p in await ProjectsApi(bitrix_client).get_projects():
                if p["gid"] == project:
                    project_name = p["name"]
                    break
            for s in await ProjectsApi(bitrix_client).get_sections(project):
                if s["gid"] == section:
                    section_name = s["name"]
                    break
        except BitrixApiError:
            pass

    async with Database.make_session() as session:
        rule = TagRule(
            tag=tag,
            action=action,
            project_gid=project,
            project_name=project_name,
            section_gid=section,
            section_name=section_name,
        )
        session.add(rule)

    return RedirectResponse(
        settings_router.prefix + "/bitrix/tag-rules/", status_code=HTTP_303_SEE_OTHER
    )


@settings_router.get("/bitrix/tag-rules/rule/{rule_id}")
async def get_tag_rule(request: Request, rule_id: int):
    async with Database.make_session() as session:
        rule = (
            (await session.execute(select(TagRule).where(TagRule.id == rule_id)))
            .scalars()
            .one_or_none()
        )
    if rule is None:
        return RedirectResponse(
            settings_router.prefix + "/bitrix/tag-rules/",
            status_code=HTTP_303_SEE_OTHER,
        )

    projects = []
    sections = []
    tags: set = set()
    if bitrix.initialized:
        try:
            projects = await ProjectsApi(bitrix_client).get_projects()
            sections = await ProjectsApi(bitrix_client).get_sections(rule.project_gid)
            for t in await ProjectsApi(bitrix_client).get_tags():
                tags.add(t["name"])
        except BitrixApiError as e:
            logging.warning(f"Ошибка получения данных: {e}")
    tags.add(rule.tag)

    return settings_template(
        "tag_rules/rule.html",
        {
            "request": request,
            "tags": tags,
            "data": {},
            "projects": projects,
            "sections": sections,
            "tag_rule": rule,
        },
    )


@settings_router.post("/bitrix/tag-rules/rule/{rule_id}")
async def update_tag_rule(request: Request, rule_id: int):
    form = await request.form()
    tag = (form.get("tag") or "").strip()
    action = int(form.get("action") or 0)
    project = (form.get("project") or "").strip()
    section = (form.get("section") or "").strip()

    async with Database.make_session() as session:
        rule = (
            (await session.execute(select(TagRule).where(TagRule.id == rule_id)))
            .scalars()
            .one_or_none()
        )
        if rule is None:
            return RedirectResponse(
                settings_router.prefix + "/bitrix/tag-rules/",
                status_code=HTTP_303_SEE_OTHER,
            )

        rule.tag = tag
        rule.action = action
        rule.project_gid = project
        rule.section_gid = section

        if bitrix.initialized:
            try:
                for p in await ProjectsApi(bitrix_client).get_projects():
                    if p["gid"] == project:
                        rule.project_name = p["name"]
                        break
                for s in await ProjectsApi(bitrix_client).get_sections(project):
                    if s["gid"] == section:
                        rule.section_name = s["name"]
                        break
            except BitrixApiError:
                pass
        session.add(rule)

    return RedirectResponse(
        settings_router.prefix + "/bitrix/tag-rules/", status_code=HTTP_303_SEE_OTHER
    )


# ---------- Инструкция Яндекс Формы ----------
@settings_router.get("/yandex-forms")
async def yandex_hints(request: Request):
    return settings_template("yandex_forms_hint.html", {"request": request})


# ---------- Настройки приложения ----------
@settings_router.get("/app")
async def settings_app(request: Request):
    watch_field_changes = (await cfg_get("watch_field_changes")) == "1"
    watch_tasks = (await cfg_get("watch_tasks")) == "1"
    sync_enabled = (await cfg_get("sync_enabled")) != "0"  # по умолчанию вкл
    try:
        sync_interval = int(await cfg_get("sync_interval") or 60)
    except (TypeError, ValueError):
        sync_interval = 60
    saved = request.session.pop("saved", None)
    return settings_template(
        "app.html",
        {
            "request": request,
            "watch_field_changes": watch_field_changes,
            "watch_tasks": watch_tasks,
            "sync_enabled": sync_enabled,
            "sync_interval": sync_interval,
            "saved": saved,
        },
    )


@settings_router.post("/app")
async def settings_app_submit(request: Request):
    form = await request.form()
    # ВАЖНО: у чекбоксов в шаблоне value="", поэтому при отмеченном чекбоксе
    # в форме приходит пустая строка (falsy). Проверяем именно факт наличия
    # ключа через `is not None`, а не его truthiness.
    await cfg_set(
        "watch_field_changes",
        "1" if form.get("watch_field_changes") is not None else "0",
    )
    await cfg_set("watch_tasks", "1" if form.get("watch_tasks") is not None else "0")

    sync_enabled = form.get("sync_enabled") is not None
    await cfg_set("sync_enabled", "1" if sync_enabled else "0")

    try:
        sync_interval = int(form.get("sync_interval") or 60)
        if sync_interval < 10:
            sync_interval = 10
    except (TypeError, ValueError):
        sync_interval = 60
    await cfg_set("sync_interval", str(sync_interval))

    # применяем налету
    from core.sync import start_scheduler, stop_scheduler, reschedule

    if sync_enabled:
        start_scheduler(sync_interval)
        reschedule(sync_interval)
    else:
        stop_scheduler()

    request.session["saved"] = True
    return RedirectResponse(
        settings_router.prefix + "/app", status_code=HTTP_303_SEE_OTHER
    )


@settings_router.post("/app/sync-now")
async def settings_app_sync_now(request: Request):
    """Принудительная разовая синхронизация по кнопке."""
    from core.sync import sync_once

    try:
        await sync_once()
        request.session["saved"] = True
    except Exception as e:
        logging.warning(f"Принудительная синхронизация завершилась с ошибкой: {e}")
    return RedirectResponse(
        settings_router.prefix + "/app", status_code=HTTP_303_SEE_OTHER
    )


@settings_router.post("/app/sync-reset")
async def settings_app_sync_reset(request: Request):
    """Сбрасывает флаг 'первый импорт пройден'. Следующий sync снова молчит
    (полезно после смены проекта или массовых изменений в Bitrix, чтобы не
    спамить чаты сотнями уведомлений)."""
    from core.sync import reset_initial_flag

    try:
        await reset_initial_flag()
        request.session["saved"] = True
    except Exception as e:
        logging.warning(f"Сброс флага синхронизации завершился с ошибкой: {e}")
    return RedirectResponse(
        settings_router.prefix + "/app", status_code=HTTP_303_SEE_OTHER
    )


# ---------- Совместимость: старые /asana ссылки редиректят на /bitrix ----------
@settings_router.get("/asana")
@settings_router.get("/asana/")
async def _legacy_asana(request: Request):
    return RedirectResponse(
        settings_router.prefix + "/bitrix/", status_code=HTTP_303_SEE_OTHER
    )


@settings_router.get("/asana/tag-rules")
@settings_router.get("/asana/tag-rules/")
async def _legacy_asana_tag_rules(request: Request):
    return RedirectResponse(
        settings_router.prefix + "/bitrix/tag-rules/", status_code=HTTP_303_SEE_OTHER
    )
