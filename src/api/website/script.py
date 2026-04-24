from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from src.api.web_auth import require_web_auth
from src.api.website.utils import list_scripts, templates
from src.config import SCRIPTS_FOLDER, ALLOWED_SCRIPT_EXTENSIONS

router = APIRouter(tags=["scripts"], dependencies=[Depends(require_web_auth)])

@router.get("/content")
def script_content(path: str):
    if path not in list_scripts():
        raise HTTPException(status_code=404, detail="Script not found")
    return PlainTextResponse((SCRIPTS_FOLDER / path).read_text())


@router.get("", response_class=HTMLResponse)
def scripts_page(request: Request):
    return templates.TemplateResponse(request=request, name="scripts.html", context={
        "request": request,
        "scripts": list_scripts(),
    })


@router.get("/new", response_class=HTMLResponse)
def new_script_page(request: Request):
    return templates.TemplateResponse(request=request, name="script_form.html", context={
        "request": request,
        "errors": None,
        "form_data": {"subfolder": "", "filename": "", "ext": ".sh", "content": ""},
    })


@router.post("/new", response_class=HTMLResponse)
async def create_script(request: Request):
    form = await request.form()
    subfolder = (form.get("subfolder") or "").strip().strip("/")
    filename  = (form.get("filename")  or "").strip()
    ext       = (form.get("ext")       or ".sh")
    content   = (form.get("content")   or "")

    errors = []
    if not filename:
        errors.append("Filename is required.")
    if any(c in filename for c in ("/", "\\", "..")):
        errors.append("Filename must not contain path separators.")
    if subfolder and any(part == ".." for part in Path(subfolder).parts):
        errors.append("Subfolder must not contain '..'.")
    if ext not in ALLOWED_SCRIPT_EXTENSIONS:
        errors.append(f"Extension must be in {ALLOWED_SCRIPT_EXTENSIONS}.")

    if not errors:
        rel  = Path(subfolder) / (filename + ext) if subfolder else Path(filename + ext)
        full = SCRIPTS_FOLDER / rel
        if not str(full.resolve()).startswith(str(SCRIPTS_FOLDER.resolve())):
            errors.append("Invalid path.")
        else:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
            return RedirectResponse("/script", status_code=303)

    return templates.TemplateResponse(request=request, name="script_form.html", status_code=422, context={
        "request": request,
        "errors": errors,
        "form_data": {"subfolder": subfolder, "filename": filename, "ext": ext, "content": content},
    })
