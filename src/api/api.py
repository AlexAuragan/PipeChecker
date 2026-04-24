from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from src.api import utils
from src.api.web_auth import RequiresLoginException
from src.api.routers.connectors import router as connectors_router
from src.api.routers.jobs import router as jobs_router
from src.api.routers.pipelines import router as pipelines_router
from src.api.website.web import router as web_base_router
from src.api.website.script import router as web_script_router
from src.api.website.pipeline import router as web_pipeline_router
from src.api.website.connector import router as web_connector_router
from src.api.website.job import router as web_job_router
from src.api.website.login import router as web_login_router
from src.core.database import init_db

init_db()

app = FastAPI(lifespan=utils.lifespan, title="Pipeline Runner", version="0.1.0")


@app.exception_handler(RequiresLoginException)
async def requires_login_handler(request: Request, exc: RequiresLoginException):
    return RedirectResponse(url=f"/login?next={exc.next_url}", status_code=303)

# API
app.include_router(connectors_router, prefix="/api/v1")
app.include_router(pipelines_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")

# Website
app.include_router(web_login_router)
app.include_router(web_script_router, prefix="/script")
app.include_router(web_pipeline_router, prefix="/pipeline")
app.include_router(web_connector_router, prefix="/connector")
app.include_router(web_job_router, prefix="/job")
app.include_router(web_base_router)
