from fastapi import FastAPI

from src.api import utils
from src.api.routers.connectors import router as connectors_router
from src.api.routers.jobs import router as jobs_router
from src.api.routers.pipelines import router as pipelines_router
from src.api.routers.web import router as web_router
from src.core.database import init_db

init_db()

app = FastAPI(lifespan=utils.lifespan, title="Pipeline Runner", version="0.1.0")
app.include_router(connectors_router, prefix="/api/v1")
app.include_router(pipelines_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(web_router)
