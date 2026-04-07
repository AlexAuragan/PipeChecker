import src.classes.connectors as _connectors

connectors: dict[str, type[_connectors.Connector]] = {
    "Proxmox": _connectors.Proxmox,
    "Caddy": _connectors.Caddy,
}
