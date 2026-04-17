"""
/api/v1/connectors router
"""
import asyncio
from typing import Annotated, Union

from fastapi import APIRouter, HTTPException, status, Request
from fastapi.params import Depends
from pydantic import BaseModel, Field

from src.api.security import require_api_key
from src.api import utils
from src.classes.connectors import Manager, ConnectorType, Proxmox, Caddy, Connector
from src.core.storage import save_manager


router = APIRouter(prefix="/connectors", tags=["connectors"], dependencies=[Depends(require_api_key)])

## Schema
ConnectorBody = Annotated[Union[Proxmox, Caddy], Field(discriminator="type")]

class ConnectorPatch(BaseModel):
    type: ConnectorType | None = None
    config_path: list[str] | None = None
    config_url: list[str] | None = None
    config_ssh: list[str] | None = None

class TargetResponse(BaseModel):
    id: str
    conf: dict


## Routes
@router.post("/reload")
async def reload(request: Request):
    asyncio.create_task(utils.reload_manager(request.app))
    return {"status": "reload started"}

@router.get("", response_model=list[Connector])
def list_connectors(manager: Manager= Depends(utils.get_manager)):
    return [connector for connector in manager]

@router.get("/{name}", response_model=Connector)
def get_connector(name :str, manager: Manager = Depends(utils.get_manager)):
    return utils.get_connector_or_404(manager, name)

@router.post(
    "",
    response_model=Connector,
    status_code=status.HTTP_201_CREATED
)
def create_connector(body: ConnectorBody, manager: Manager = Depends(utils.get_manager)):
    # Conflict check
    try:
        manager.get(body.name)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Connector '{body.name}' already exists.",
        )
    except KeyError:
        pass
    manager.add(body)
    save_manager(manager)
    return body

@router.put("/{name}", response_model=Connector)
def replace_connector(name: str, body: ConnectorBody, manager: Manager = Depends(utils.get_manager)):
    utils.get_connector_or_404(manager, name)
    manager.remove(name)
    manager.add(body)
    save_manager(manager)
    return body

@router.patch("/{name}", response_model=Connector)
def update_connector(name: str, body: ConnectorPatch, manager: Manager = Depends(utils.get_manager)):
    existing = utils.get_connector_or_404(manager, name)

    # merge existing and update
    updates = body.model_dump(exclude_unset=True)
    new_type = updates.get("type", existing.type)
    new_path = updates.get("config_path", existing.config_path)
    new_url = updates.get("config_url", existing.config_url)
    new_ssh = updates.get("config_ssh", existing.config_ssh)
    from src.classes import connectors
    cls = connectors[new_type.value]
    conn = cls.model_validate({
        "name":name,
        "config_path":new_path,
        "config_url":new_url,
        "config_ssh":new_ssh
    })
    manager.remove(name)
    manager.add(conn)
    save_manager(manager)
    return conn

@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connector(name: str, manager: Manager = Depends(utils.get_manager)):
    utils.get_connector_or_404(manager, name)
    manager.remove(name)
    save_manager(manager)

@router.get("/{name}/targets", response_model=list[TargetResponse])
def list_targets(name: str, manager: Manager = Depends(utils.get_manager)):
    conn = utils.get_connector_or_404(manager, name)
    return [
        TargetResponse(id=str(target.id), conf=target.config) for target in conn.targets
    ]

@router.post("/{name}/discover", response_model=list[TargetResponse])
def reload_targets(name: str, manager: Manager = Depends(utils.get_manager)):
    conn = utils.get_connector_or_404(manager, name)
    conn.load_targets()
    return [
        TargetResponse(id=str(target.id), conf=target.config) for target in conn.targets
    ]