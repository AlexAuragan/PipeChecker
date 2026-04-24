from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.web_auth import (
    SESSION_COOKIE, _SESSION_LIFETIME,
    verify_credentials, create_session_cookie,
)
from src.api.website.utils import templates

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(request=request, name="login.html", context={
        "request": request,
        "next": next,
        "error": None,
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/"),
):
    safe_next = next if (next.startswith("/") and not next.startswith("//")) else "/"
    if verify_credentials(username, password):
        response = RedirectResponse(safe_next, status_code=303)
        response.set_cookie(
            SESSION_COOKIE, create_session_cookie(username),
            httponly=True, samesite="lax", max_age=_SESSION_LIFETIME,
        )
        return response
    return templates.TemplateResponse(request=request, name="login.html", status_code=401, context={
        "request": request,
        "next": safe_next,
        "error": "Invalid username or password.",
    })


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
