import logging
import logging.config
import json
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.status import HTTP_303_SEE_OTHER
from starlette.middleware.sessions import SessionMiddleware

from log_config import logging_config
logging.config.dictConfig(logging_config)

import bitrix
from lifespan import lifespan
from core.receiver import receive_form
from core.events_handler import handle_bitrix_event
from web import web_router


app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware,
                   secret_key="ZEfTgTOUTou8cpIlORwmU0fjiEc5j9LlP6Af6j3l4yA")
app.include_router(web_router)
app.mount("/static", StaticFiles(directory="web/static"), name="static")


@app.get("/")
def index():
    if not bitrix.initialized:
        return RedirectResponse("/settings", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse("/reports/all-managers", status_code=HTTP_303_SEE_OTHER)


@app.get("/status")
def status():
    return {"status": "ok", "bitrix_initialized": bitrix.initialized}


def _safe_json_loads(text: str):
    """Парсит строку, которая может быть валидным JSON или JSON в URL-encoded виде."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        from urllib.parse import unquote
        return json.loads(unquote(text))


async def _process_receive(text: str) -> JSONResponse:
    try:
        data = _safe_json_loads(text)
    except Exception as e:
        logging.warning(f"Не удалось распарсить тело /receive: {e}")
        return JSONResponse({"ok": False, "error": "invalid_payload",
                             "message": "Тело запроса не является валидным JSON"},
                            status_code=400)

    try:
        result = await receive_form(data)
        return JSONResponse({"ok": True, **(result or {})}, status_code=200)
    except Exception as e:
        # Намеренно не отдаём 500, чтобы Яндекс Формы не ретраили бесконечно
        # и в браузере не появлялся Internal Server Error
        logging.exception(f"Ошибка обработки формы: {e}")
        return JSONResponse(
            {"ok": False, "error": "processing_error", "message": str(e)},
            status_code=200,
        )


@app.get("/receive/{text:path}")
async def receive_yandex_form_get(text: str):
    return await _process_receive(text)


@app.post("/receive/{text:path}")
async def receive_yandex_form_post(text: str):
    return await _process_receive(text)


@app.post("/receive")
async def receive_yandex_form_body(request: Request):
    """Альтернативный путь: payload приходит в теле запроса."""
    try:
        body = await request.body()
        text = body.decode("utf-8", errors="replace")
    except Exception as e:
        return JSONResponse({"ok": False, "error": "invalid_payload", "message": str(e)},
                            status_code=400)
    return await _process_receive(text)


@app.get("/receive-test/{text:path}")
async def receive_test(text: str):
    try:
        data = _safe_json_loads(text)
        logging.info(f"Получено в тестовый эндпоинт: {data}")
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=200)


# ----- Исходящий вебхук Bitrix24: события задач -----
@app.post("/bitrix/event")
async def bitrix_event_handler(request: Request):
    """Принимает события исходящего вебхука Bitrix24
    (ONTASKADD / ONTASKUPDATE / ONTASKDELETE / ONTASKCOMMENTADD и т.п.).

    Bitrix посылает application/x-www-form-urlencoded с полями вида
    event, data[FIELDS_AFTER][ID], auth[application_token] и т.д.
    """
    try:
        try:
            form = await request.form()
            payload = {k: v for k, v in form.items()}
        except Exception:
            payload = {}

        if not payload:
            try:
                payload = await request.json()
            except Exception:
                payload = {}

        if not payload:
            return PlainTextResponse("empty", status_code=200)

        await handle_bitrix_event(payload)
        return PlainTextResponse("ok", status_code=200)
    except Exception as e:
        logging.exception(f"Ошибка обработки события Bitrix24: {e}")
        # отдаём 200, чтобы Bitrix не ретраил бесконечно
        return PlainTextResponse("error_handled", status_code=200)
