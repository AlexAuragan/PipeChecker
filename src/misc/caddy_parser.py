"""Minimal Caddyfile tokenizer and parser.

Produces a dict of {site_address: block_children} suitable for extracting
the list of configured sites (used by the Caddy connector).
"""

import re


def tokenize(text: str) -> list[str]:
    """Strip comments and split a Caddyfile into a flat token list."""
    text = re.sub(r'#[^\n]*', '', text)
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if line == '{':
            # A lone `{` on its own line opens the global options block.
            tokens.append('__GLOBAL_BLOCK_OPEN__')
        else:
            tokens.extend(re.findall(r'"[^"]*"|\{[^{}\s]+\}|\{|\}|\S+', line))
    return tokens


def parse_block(tokens: list[str], pos: int) -> tuple[list, int]:
    """Recursively parse a Caddyfile block starting at pos, returning (items, new_pos)."""
    items = []
    while pos < len(tokens) and tokens[pos] != '}':
        if tokens[pos] == '__GLOBAL_BLOCK_OPEN__':
            # Global options block: skip it entirely.
            pos += 1
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
            pos += 1  # consume '}'
            items.append({"directive": directive, "block": children})
        else:
            items.append({"directive": directive})

    return items, pos


def parse_caddyfile(text: str) -> dict:
    """Parse a full Caddyfile and return a dict of {site_address: block_children}."""
    tokens = tokenize(text)
    result, _ = parse_block(tokens, 0)
    out = {}
    for block in result:
        if block["directive"][0] == "(global)":
            continue
        out[block["directive"][0]] = block["block"]
    return out
