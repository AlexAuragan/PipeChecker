from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from src.api import utils
from src.api.website.utils import connector_form_data, parse_connector_form, templates
from src.classes.connectors import ConnectorType
from src.core import storage


router = APIRouter(tags=["connector"])


@router.get("", response_class=HTMLResponse)
def connectors_page(request: Request):
    manager = storage.load_manager()
    return templates.TemplateResponse(request=request, name="connectors.html", context={
        "request": request,
        "connectors": list(manager),
    })


@router.get("/connector/new", response_class=HTMLResponse)
def new_connector_page(request: Request):
    return templates.TemplateResponse(request=request, name="connector_form.html", context={
        "request": request,
        "editing": False,
        "connector_types": list(ConnectorType),
        "form_data": {
            "name": "", "type": ConnectorType.proxmox.value,
            "config_ssh": [], "config_path": [], "config_url": [],
        },
        "errors": None,
    })


@router.post("/connector/new", response_class=HTMLResponse)
async def create_connector_web(request: Request):
    form = await request.form()
    try:
        connector = parse_connector_form(form)
    except (ValidationError, ValueError) as exc:
        errors = [f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in (exc.errors() if hasattr(exc, 'errors') else [])] or [str(exc)]
        return templates.TemplateResponse(request=request, name="connector_form.html", status_code=422, context={
            "request": request,
            "editing": False,
            "connector_types": list(ConnectorType),
            "form_data": connector_form_data(form),
            "errors": errors,
        })
    manager = storage.load_manager()
    if connector.name in manager:
        return templates.TemplateResponse(request=request, name="connector_form.html", status_code=409, context={
            "request": request,
            "editing": False,
            "connector_types": list(ConnectorType),
            "form_data": connector_form_data(form),
            "errors": [f"Connector '{connector.name}' already exists."],
        })
    manager.add(connector)
    storage.save_manager(manager)
    return RedirectResponse("/connector", status_code=303)


@router.get("/connector/{name}/edit", response_class=HTMLResponse)
def edit_connector_page(request: Request, name: str):
    manager = storage.load_manager()
    connector = utils.get_connector_or_404(manager, name)
    return templates.TemplateResponse(request=request, name="connector_form.html", context={
        "request": request,
        "editing": True,
        "connector_types": list(ConnectorType),
        "form_data": {
            "name": connector.name, "type": connector.type.value,
            "config_ssh": connector.config_ssh,
            "config_path": connector.config_path,
            "config_url": connector.config_url,
        },
        "errors": None,
    })


@router.post("/connector/{name}/edit", response_class=HTMLResponse)
async def update_connector_web(request: Request, name: str):
    form = await request.form()
    try:
        connector = parse_connector_form(form)
    except (ValidationError, ValueError) as exc:
        errors = [f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in (exc.errors() if hasattr(exc, 'errors') else [])] or [str(exc)]
        return templates.TemplateResponse(request=request, name="connector_form.html", status_code=422, context={
            "request": request,
            "editing": True,
            "connector_types": list(ConnectorType),
            "form_data": connector_form_data(form, name_override=name),
            "errors": errors,
        })
    manager = storage.load_manager()
    utils.get_connector_or_404(manager, name)
    manager.remove(name)
    manager.add(connector)
    storage.save_manager(manager)
    return RedirectResponse("/connector", status_code=303)

