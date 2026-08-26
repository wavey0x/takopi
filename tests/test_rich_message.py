from takopi.telegram.rich_message import (
    MAX_RICH_CHARS,
    build_input_rich_message,
)

TABLE = "| A | B |\n|:---|---:|\n| 1 | 2 |\n"


def _html(markdown: str, mode: str = "auto") -> str:
    payload = build_input_rich_message(markdown, mode)  # type: ignore[arg-type]
    assert payload is not None
    return payload["html"]


def _table(columns: int) -> str:
    header = "|" + "|".join(f" c{i} " for i in range(columns)) + "|"
    separator = "|" + "|".join("---" for _ in range(columns)) + "|"
    return f"{header}\n{separator}\n"


def test_auto_renders_explicit_native_table_style() -> None:
    html = _html(TABLE)

    assert html.startswith("<table bordered striped compact>\n")
    assert '<th align="left">A</th>' in html
    assert '<th align="right">B</th>' in html
    assert "<thead>" not in html
    assert "<tbody>" not in html


def test_auto_only_uses_rich_messages_for_real_tables() -> None:
    assert build_input_rich_message("## Heading\n\nProse", "auto") is None
    assert build_input_rich_message(f"```\n{TABLE}```\n", "auto") is None
    assert build_input_rich_message(TABLE, "off") is None
    assert build_input_rich_message("## Heading", "always") is not None


def test_content_is_inert_but_safe_links_survive() -> None:
    markdown = (
        "| Kind | Value |\n"
        "|---|---|\n"
        "| link | [safe](https://example.com/path) |\n"
        "| image | ![remote image](https://example.com/image.png) |\n"
        "| html | <script>alert(1)</script> |\n"
        "| unsafe | [click](ftp://example.com/file) |\n"
    )
    html = _html(markdown)

    assert '<a href="https://example.com/path">safe</a>' in html
    assert "<img" not in html
    assert "image.png" not in html
    assert "remote image" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "ftp://" not in html
    assert "click" in html


def test_rich_message_limits_fail_closed() -> None:
    assert build_input_rich_message(_table(20), "auto") is not None
    assert build_input_rich_message(_table(21), "auto") is None
    assert build_input_rich_message("x" * (MAX_RICH_CHARS + 1), "always") is None
    too_many_blocks = (
        TABLE + "\n" + "\n\n".join(f"paragraph {index}" for index in range(510))
    )
    assert build_input_rich_message(too_many_blocks, "auto") is None
