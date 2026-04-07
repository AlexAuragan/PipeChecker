import re

def parse_table(output: str) -> list[dict]:
    lines = output.strip().splitlines()
    header_line = lines[0]

    # Find each header and its exact start position
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
    arch = (re.findall(r'arch: (.*?)\n', conf) or [None])[0]
    memory = (re.findall(r'memory: (.*?)\n', conf) or [None])[0]
    swap = (re.findall(r'swap: (.*?)\n', conf) or [None])[0]
    hostname = (re.findall(r'hostname: (.*?)\n', conf) or [None])[0]
    ostype = (re.findall(r'ostype: (.*?)\n', conf) or [None])[0]
    rootfs = (re.findall(r'rootfs: (.*?)\n', conf) or [None])[0]
    net0 = (re.findall(r'net0: (.*?)\n', conf) or [None])[0]

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
        "hostname": hostname
    }