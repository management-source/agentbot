from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_config": {
                "recaptcha_enabled": settings.recaptcha_enabled(),
                "recaptcha_site_key": settings.RECAPTCHA_SITE_KEY or "",
            },
        },
    )
