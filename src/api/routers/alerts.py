from typing import Any

from fastapi import APIRouter, HTTPException, Depends

from src.api.security import require_api_key
from src.classes.alert import AlertConfig
from src.core import jobs, storage

router = APIRouter(
    prefix="/alerts", tags=["alerts"], dependencies=[Depends(require_api_key)]
)


@router.get("")
def list_alerts() -> list[AlertConfig]:
    return storage.load_alerts()


@router.post("", status_code=201)
def create_alert(body: AlertConfig) -> AlertConfig:
    try:
        storage.save_alert(body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return body


# /history must be declared before /{name} to avoid "history" being treated as a name
@router.get("/history")
def get_history(limit: int = 200) -> list[dict[str, Any]]:
    return jobs.list_alert_history(limit=limit)


@router.delete("/history", status_code=204)
def clear_history() -> None:
    jobs.clear_alert_history()


@router.get("/{name}")
def get_alert(name: str) -> AlertConfig:
    alerts = {a.name: a for a in storage.load_alerts()}
    if name not in alerts:
        raise HTTPException(status_code=404, detail=f"Alert '{name}' not found.")
    return alerts[name]


@router.put("/{name}")
def update_alert_route(name: str, body: AlertConfig) -> AlertConfig:
    if body.name != name:
        raise HTTPException(status_code=422, detail="Body name must match URL name.")
    try:
        storage.update_alert(body)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return body


@router.delete("/{name}", status_code=204)
def delete_alert_route(name: str) -> None:
    try:
        storage.delete_alert(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
