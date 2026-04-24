"""Simple text parsers for Proxmox CLI output formats."""

import re


def parse_table(output: str) -> list[dict]:
    """Parse a fixed-width columnar table (e.g. `pct list` output) into a list of dicts."""
    lines = output.strip().splitlines()
    header_line = lines[0]

    # Find each header token and its exact start column.
    headers = [(m.group(), m.start()) for m in re.finditer(r'\S+', header_line)]

    results = []
    for line in lines[1:]:
        if not line.strip():
            continue
        row = {}
        for i, (header, start) in enumerate(headers):
            end = headers[i + 1][1] if i + 1 < len(headers) else None
            row[header] = line[start:end].strip() or None
        results.append(row)
    return results


def pct_config_parser(conf: str) -> dict:
    """Extract key fields from a Proxmox CT config file (e.g. /etc/pve/lxc/<id>.conf)."""
    def _find(pattern: str):
        return (re.findall(pattern, conf) or [None])[0]

    arch = _find(r'arch: (.*?)\n')
    memory = _find(r'memory: (.*?)\n')
    swap = _find(r'swap: (.*?)\n')
    hostname = _find(r'hostname: (.*?)\n')
    ostype = _find(r'ostype: (.*?)\n')
    rootfs = _find(r'rootfs: (.*?)\n')
    net0 = _find(r'net0: (.*?)\n')

    if memory:
        memory = int(memory)
    if swap:
        swap = int(swap)

    rootfs_size = None
    if rootfs:
        rootfs_size = (re.findall(r'size=(.*?),|\n', rootfs) or [None])[0]

    ip = None
    if net0:
        ip = (re.findall(r'ip=(.*?),|\n', net0) or [None])[0]
        if ip:
            ip = ip.split("/")[0]

    return {
        "arch": arch,
        "memory": memory,
        "swap": swap,
        "ostype": ostype,
        "rootfs_size": rootfs_size,
        "ip": ip,
        "hostname": hostname,
    }
