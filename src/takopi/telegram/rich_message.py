"""Safe Telegram Rich Message rendering for final answers."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .api_schemas import RichMessage

RichMessagesMode = Literal["off", "auto", "always"]

MAX_RICH_CHARS = 32768
MAX_RICH_BLOCKS = 500
MAX_RICH_COLUMNS = 20

_BLOCK_TOKENS = {
    "blockquote_open",
    "bullet_list_open",
    "code_block",
    "fence",
    "heading_open",
    "hr",
    "list_item_open",
    "ordered_list_open",
    "paragraph_open",
    "table_open",
    "tr_open",
}
_SAFE_LINK_SCHEMES = {"http", "https", "tg"}


def _table_open(*_: object) -> str:
    return "<table bordered striped compact>\n"


def _section_tag(*_: object) -> str:
    return ""


def _cell_open(tokens: list[Token], idx: int, *_: object) -> str:
    token = tokens[idx]
    style = str(token.attrGet("style") or "")
    align = style.removeprefix("text-align:")
    if align in {"left", "center", "right"}:
        return f'<{token.tag} align="{align}">'  # values are allowlisted above
    return f"<{token.tag}>"


def _new_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    rules = getattr(parser.renderer, "rules", None)
    if not isinstance(rules, dict):
        raise RuntimeError("markdown renderer does not support custom rules")
    rules["table_open"] = _table_open
    rules["thead_open"] = _section_tag
    rules["thead_close"] = _section_tag
    rules["tbody_open"] = _section_tag
    rules["tbody_close"] = _section_tag
    rules["th_open"] = _cell_open
    rules["td_open"] = _cell_open
    return parser


_PARSER = _new_parser()


def _safe_link(href: str) -> bool:
    parsed = urlparse(href)
    if parsed.scheme not in _SAFE_LINK_SCHEMES:
        return False
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.netloc)
    return bool(parsed.netloc or parsed.path)


def _sanitize_inline(children: list[Token]) -> list[Token]:
    sanitized: list[Token] = []
    links: list[bool] = []
    for token in children:
        if token.type == "image":
            sanitized.append(Token("text", "", 0, content=token.content or "image"))
            continue
        if token.type == "link_open":
            keep = _safe_link(str(token.attrGet("href") or ""))
            links.append(keep)
            if keep:
                sanitized.append(token)
            continue
        if token.type == "link_close":
            if links and links.pop():
                sanitized.append(token)
            continue
        if token.children:
            token.children = _sanitize_inline(token.children)
        sanitized.append(token)
    return sanitized


def _table_too_wide(tokens: list[Token]) -> bool:
    columns = 0
    in_row = False
    for token in tokens:
        if token.type == "tr_open":
            columns = 0
            in_row = True
        elif in_row and token.type in {"th_open", "td_open"}:
            columns += 1
        elif token.type == "tr_close":
            if columns > MAX_RICH_COLUMNS:
                return True
            in_row = False
    return False


def _within_limits(tokens: list[Token], html: str) -> bool:
    if len(html.encode("utf-8")) > MAX_RICH_CHARS:
        return False
    if sum(token.type in _BLOCK_TOKENS for token in tokens) > MAX_RICH_BLOCKS:
        return False
    return not _table_too_wide(tokens)


def _join_lines(parts: list[str]) -> str:
    return "\n".join(part for part in parts if part)


def _rich_text_to_plain(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_rich_text_to_plain(item) for item in value)
    if not isinstance(value, dict):
        return ""

    kind = value.get("type")
    if kind == "custom_emoji":
        alternative = value.get("alternative_text")
        return alternative if isinstance(alternative, str) else ""
    if kind == "mathematical_expression":
        expression = value.get("expression")
        return expression if isinstance(expression, str) else ""
    if kind == "button":
        button = value.get("button")
        return (
            _rich_text_to_plain(button.get("text")) if isinstance(button, dict) else ""
        )
    return _rich_text_to_plain(value.get("text"))


def _nodes_to_plain(nodes: Any) -> str:
    if not isinstance(nodes, list):
        return ""
    return _join_lines([_node_to_plain(node) for node in nodes])


def _table_to_plain(rows: Any) -> str:
    if not isinstance(rows, list):
        return ""
    return _join_lines(
        [
            " | ".join(_node_to_plain(cell) for cell in row)
            for row in rows
            if isinstance(row, list)
        ]
    )


def _node_to_plain(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    expression = node.get("expression")
    return _join_lines(
        [
            _rich_text_to_plain(node.get("summary")),
            _rich_text_to_plain(node.get("text")),
            _nodes_to_plain(node.get("blocks")),
            _nodes_to_plain(node.get("items")),
            _node_to_plain(node.get("caption")),
            _table_to_plain(node.get("cells")),
            _rich_text_to_plain(node.get("credit")),
            _nodes_to_plain(node.get("buttons")),
            expression if isinstance(expression, str) else "",
        ]
    )


def rich_message_to_plain(message: RichMessage | None) -> str | None:
    """Recover visible text from a Telegram Rich Message reply."""
    if message is None:
        return None
    text = _nodes_to_plain(message.blocks)
    return text if text.strip() else None


def build_input_rich_message(
    markdown: str, mode: RichMessagesMode
) -> dict[str, Any] | None:
    """Render trusted structure and inert content into Telegram Rich HTML."""
    if mode == "off" or not markdown.strip():
        return None
    tokens = _PARSER.parse(markdown)
    if mode == "auto" and not any(token.type == "table_open" for token in tokens):
        return None
    for token in tokens:
        if token.children:
            token.children = _sanitize_inline(token.children)
    html = _PARSER.renderer.render(tokens, _PARSER.options, {})
    if not _within_limits(tokens, html):
        return None
    return {"html": html}
