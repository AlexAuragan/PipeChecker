import re
from typing import Any


def tokenize(text: str) -> list[str]:
    text = re.sub(r'#[^\n]*', '', text)
    # Split into lines first to detect the global block `{` on its own line
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if line == '{':
            tokens.append('__GLOBAL_BLOCK_OPEN__')
        else:
            tokens.extend(re.findall(r'"[^"]*"|\{[^{}\s]+\}|\{|\}|\S+', line))
    return tokens

def parse_block(tokens: list[str], pos: int) -> tuple[list, int]:
    items = []
    while pos < len(tokens) and tokens[pos] != '}':
        directive = []

        if tokens[pos] == '__GLOBAL_BLOCK_OPEN__':
            # Global options block: { ... }
            pos += 1  # skip the marker, next token should be {
            children, pos = parse_block(tokens, pos)
            pos += 1  # consume '}'
            items.append({"directive": ["(global)"], "block": children})
            continue

        directive = [tokens[pos]]
        pos += 1
        while pos < len(tokens) and tokens[pos] not in ('{', '}'):
            directive.append(tokens[pos])
            pos += 1
            if pos < len(tokens) and tokens[pos] == '{':
                break

        if pos < len(tokens) and tokens[pos] == '{':
            pos += 1
            children, pos = parse_block(tokens, pos)
            pos += 1
            items.append({"directive": directive, "block": children})
        else:
            items.append({"directive": directive})

    return items, pos


def parse_caddyfile(text: str) -> dict:
    tokens = tokenize(text)
    result, _ = parse_block(tokens, 0)
    out = {}
    for block in result:
        if block["directive"][0] == "(global)":
            continue
        out[block["directive"][0]] = block["block"]
    return out