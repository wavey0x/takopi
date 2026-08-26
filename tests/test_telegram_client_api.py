import httpx
import pytest

from takopi.telegram.client_api import (
    HttpBotClient,
    TelegramRetryAfter,
    retry_after_from_payload,
)
from takopi.telegram.api_models import User


def _response() -> httpx.Response:
    request = httpx.Request("POST", "https://example.com")
    return httpx.Response(200, request=request)


def test_retry_after_from_payload() -> None:
    assert retry_after_from_payload({}) is None
    assert retry_after_from_payload({"parameters": {"retry_after": 2}}) == 2.0


def test_parse_envelope_invalid_payload() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    assert (
        client._parse_telegram_envelope(
            method="sendMessage",
            resp=_response(),
            payload="nope",
        )
        is None
    )


def test_parse_envelope_rate_limited() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    payload = {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}
    with pytest.raises(TelegramRetryAfter) as exc:
        client._parse_telegram_envelope(
            method="sendMessage",
            resp=_response(),
            payload=payload,
        )
    assert exc.value.retry_after == 1.0


def test_parse_envelope_api_error() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    payload = {"ok": False, "error_code": 400, "description": "boom"}
    assert (
        client._parse_telegram_envelope(
            method="sendMessage",
            resp=_response(),
            payload=payload,
        )
        is None
    )


def test_parse_envelope_ok() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    payload = {"ok": True, "result": {"message_id": 1}}
    assert client._parse_telegram_envelope(
        method="sendMessage",
        resp=_response(),
        payload=payload,
    ) == {"message_id": 1}


@pytest.mark.anyio
async def test_client_methods_build_params_and_decode() -> None:
    payloads = {
        "getUpdates": [{"update_id": 1}],
        "getFile": {"file_path": "path"},
        "sendMessage": {"message_id": 1, "chat": {"id": 1, "type": "private"}},
        "sendRichMessage": {
            "message_id": 4,
            "chat": {"id": 1, "type": "private"},
        },
        "sendDocument": {"message_id": 2, "chat": {"id": 1, "type": "private"}},
        "editMessageText": {"message_id": 3, "chat": {"id": 1, "type": "private"}},
        "deleteMessage": True,
        "setMyCommands": True,
        "getMe": {"id": 7},
        "answerCallbackQuery": True,
        "getChat": {"id": 5, "type": "private"},
        "getChatMember": {"status": "member"},
        "createForumTopic": {"message_thread_id": 11},
        "editForumTopic": True,
    }

    class _StubClient(HttpBotClient):
        def __init__(self) -> None:
            super().__init__("token", http_client=httpx.AsyncClient())
            self.calls: list[tuple[str, dict | None, dict | None, dict | None]] = []

        async def _request(
            self,
            method: str,
            *,
            json: dict | None = None,
            data: dict | None = None,
            files: dict | None = None,
            classify_failure: bool = False,
        ) -> object | None:
            _ = classify_failure
            self.calls.append((method, json, data, files))
            return payloads.get(method)

    client = _StubClient()

    updates = await client.get_updates(offset=10, allowed_updates=["message"])
    assert updates and updates[0].update_id == 1

    assert await client.get_file("file") is not None

    msg = await client.send_message(
        1,
        "hi",
        reply_to_message_id=2,
        disable_notification=True,
        message_thread_id=3,
        entities=[{"type": "bold", "offset": 0, "length": 2}],
        parse_mode="Markdown",
        reply_markup={"inline_keyboard": []},
    )
    assert msg and msg.message_id == 1

    rich = await client.send_rich_message(
        1,
        {"html": "<table bordered striped compact></table>"},
        reply_to_message_id=2,
        disable_notification=True,
        message_thread_id=3,
        reply_markup={"inline_keyboard": []},
    )
    assert rich.outcome == "delivered"
    assert rich.message and rich.message.message_id == 4

    doc = await client.send_document(
        1,
        "file.txt",
        b"data",
        reply_to_message_id=2,
        message_thread_id=3,
        disable_notification=True,
        caption="doc",
    )
    assert doc and doc.message_id == 2

    edit = await client.edit_message_text(
        1,
        2,
        "edit",
        entities=[{"type": "italic", "offset": 0, "length": 4}],
        parse_mode="Markdown",
        reply_markup={"inline_keyboard": []},
    )
    assert edit and edit.message_id == 3

    rich_edit = await client.edit_rich_message_text(
        1,
        2,
        {"html": "<table bordered striped compact></table>"},
        reply_markup={"inline_keyboard": []},
    )
    assert rich_edit.outcome == "delivered"

    assert await client.delete_message(1, 2) is True
    assert await client.set_my_commands(
        [{"command": "ping", "description": "pong"}],
        scope={"type": "chat"},
        language_code="en",
    )
    assert await client.answer_callback_query("cb", text="ok", show_alert=True) is True
    assert await client.get_chat(1) is not None
    assert await client.get_chat_member(1, 2) is not None
    assert await client.create_forum_topic(1, "topic") is not None
    assert await client.edit_forum_topic(1, 2, "topic") is True

    await client.close()

    send_call = next(call for call in client.calls if call[0] == "sendMessage")
    assert send_call[1]["disable_notification"] is True
    assert send_call[1]["reply_to_message_id"] == 2
    assert send_call[1]["message_thread_id"] == 3
    assert send_call[1]["entities"]
    assert send_call[1]["parse_mode"] == "Markdown"
    assert send_call[1]["link_preview_options"] == {"is_disabled": True}
    assert send_call[1]["reply_markup"]

    rich_call = next(call for call in client.calls if call[0] == "sendRichMessage")
    assert rich_call[1]["rich_message"]["html"].startswith("<table")
    assert rich_call[1]["reply_parameters"] == {
        "message_id": 2,
        "allow_sending_without_reply": True,
    }

    doc_call = next(call for call in client.calls if call[0] == "sendDocument")
    assert doc_call[2]["caption"] == "doc"
    assert doc_call[3]["document"][0] == "file.txt"

    edit_call = next(call for call in client.calls if call[0] == "editMessageText")
    assert edit_call[1]["link_preview_options"] == {"is_disabled": True}
    rich_edit_call = next(
        call
        for call in client.calls
        if call[0] == "editMessageText"
        and call[1] is not None
        and "rich_message" in call[1]
    )
    assert "text" not in rich_edit_call[1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "payload", "outcome"),
    [
        (400, {"ok": False, "error_code": 400}, "rejected"),
        (500, {"ok": False, "error_code": 500}, "unknown"),
        (200, {"ok": False, "error_code": 500}, "unknown"),
    ],
)
async def test_rich_message_failure_classification(
    status: int, payload: dict, outcome: str
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json=payload, request=request)
    )
    http_client = httpx.AsyncClient(transport=transport)
    client = HttpBotClient("token", http_client=http_client)

    attempt = await client.send_rich_message(1, {"html": "<table></table>"})

    assert attempt.outcome == outcome
    assert attempt.message is None
    await http_client.aclose()


@pytest.mark.anyio
async def test_rich_message_network_failure_is_unknown_not_retried() -> None:
    calls = 0

    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("ambiguous", request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    client = HttpBotClient("token", http_client=http_client)

    attempt = await client.send_rich_message(1, {"html": "<table></table>"})

    assert attempt.outcome == "unknown"
    assert calls == 1
    await http_client.aclose()


@pytest.mark.anyio
async def test_decode_result_invalid_payload_returns_none() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    assert client._decode_result(method="getMe", payload=["bad"], model=User) is None
    await client.close()
