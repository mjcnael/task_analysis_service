from fastapi.templating import Jinja2Templates
from utils import format_timedelta_pretty

templates = Jinja2Templates(directory="web/templates")
templates.env.filters['timedelta_human'] = format_timedelta_pretty


def settings_template(path: str, context: dict):
    return templates.TemplateResponse("settings/" + path, context)


def tickets_template(path: str, context: dict):
    return templates.TemplateResponse("tickets/" + path, context)


def reports_template(path: str, context: dict):
    return templates.TemplateResponse("reports/" + path, context)
