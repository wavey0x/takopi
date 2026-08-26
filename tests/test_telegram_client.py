import httpx
import pytest

from takopi.logging import setup_logging
from takopi.telegram.client import TelegramClient, TelegramRetryAfter
from takopi.telegram.client_api import HttpBotClient


@pytest.mark.anyio
async def test_telegram_429_no_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            429,
            json={
                "ok": False,
                "description": "retry",
                "parameters": {"retry_after": 3},
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        api = HttpBotClient("123:abcDEF_ghij", http_client=client)
        with pytest.raises(TelegramRetryAfter) as exc:
            await api._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert exc.value.retry_after == 3
    assert len(calls) == 1


@pytest.mark.anyio
async def test_no_token_in_logs_on_http_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "123:abcDEF_ghij"
    setup_logging(debug=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops", request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        api = HttpBotClient(token, http_client=client)
        await api._post("getUpdates", {"timeout": 1})
    finally:
        await client.aclose()

    out = capsys.readouterr().out
    assert token not in out
    assert "bot[REDACTED]" in out


@pytest.mark.anyio
async def test_telegram_429_no_retry_post_form() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            429,
            json={
                "ok": False,
                "description": "retry",
                "parameters": {"retry_after": 2},
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        api = HttpBotClient("123:abcDEF_ghij", http_client=client)
        with pytest.raises(TelegramRetryAfter) as exc:
            await api._post_form(
                "sendDocument",
                {"chat_id": 1},
                files={"document": ("note.txt", b"hi")},
            )
    finally:
        await client.aclose()

    assert exc.value.retry_after == 2
    assert len(calls) == 1


@pytest.mark.anyio
async def test_telegram_429_defaults_retry_after_on_bad_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="nope", request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        api = HttpBotClient("123:abcDEF_ghij", http_client=client)
        with pytest.raises(TelegramRetryAfter) as exc:
            await api._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert exc.value.retry_after == 5.0


@pytest.mark.anyio
async def test_telegram_network_error_raises_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        api = HttpBotClient("123:abcDEF_ghij", http_client=client)
        with pytest.raises(TelegramRetryAfter) as exc:
            await api._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert exc.value.retry_after == 2.0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("rich_status", "outcome", "expects_delete"),
    [(200, "delivered", True), (400, "rejected", False)],
)
async def test_rich_replacement_deleted_only_after_confirmed_delivery(
    rich_status: int, outcome: str, expects_delete: bool
) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        if method == "sendRichMessage" and rich_status == 200:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 2,
                        "chat": {"id": 1, "type": "private"},
                    },
                },
                request=request,
            )
        if method == "deleteMessage":
            return httpx.Response(
                200, json={"ok": True, "result": True}, request=request
            )
        return httpx.Response(
            rich_status,
            json={"ok": False, "error_code": rich_status},
            request=request,
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tg = TelegramClient(
        "123:abcDEF_ghij",
        http_client=http_client,
        private_chat_rps=0,
    )
    try:
        attempt = await tg.send_rich_message(
            1,
            {"html": "<table></table>"},
            replace_message_id=1,
        )
    finally:
        await tg.close()
        await http_client.aclose()

    assert attempt.outcome == outcome
    assert ("deleteMessage" in methods) is expects_delete


@pytest.mark.anyio
async def test_telegram_ok_false_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": False, "error_code": 400, "description": "bad"},
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        api = HttpBotClient("123:abcDEF_ghij", http_client=client)
        result = await api._post("getUpdates", {"timeout": 1})
    finally:
        await client.aclose()

    assert result is None


@pytest.mark.anyio
async def test_telegram_invalid_payload_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"], request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        api = HttpBotClient("123:abcDEF_ghij", http_client=client)
        result = await api._post("getUpdates", {"timeout": 1})
    finally:
        await client.aclose()

    assert result is None


@pytest.mark.anyio
async def test_telegram_decode_failure_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": {"username": "bot-only"}},
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", http_client=client)
        result = await tg.get_me()
    finally:
        await client.aclose()

    assert result is None


@pytest.mark.anyio
async def test_telegram_download_file_retries_on_429() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429,
                json={"ok": False, "parameters": {"retry_after": 3}},
                request=request,
            )
        return httpx.Response(200, content=b"ok", request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", http_client=client, sleep=sleep)
        payload = await tg.download_file("path/to/file")
    finally:
        await client.aclose()

    assert payload == b"ok"
    assert sleeps == [3.0]
    assert len(calls) == 2


@pytest.mark.anyio
async def test_telegram_download_file_429_defaults_retry_after_on_bad_body() -> None:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="nope", request=request)
        return httpx.Response(200, content=b"ok", request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", http_client=client, sleep=sleep)
        payload = await tg.download_file("path")
    finally:
        await client.aclose()

    assert payload == b"ok"
    assert sleeps == [5.0]
    assert len(calls) == 2


@pytest.mark.anyio
async def test_telegram_download_file_retries_on_network_error() -> None:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("read timed out", request=request)
        return httpx.Response(200, content=b"ok", request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", http_client=client, sleep=sleep)
        payload = await tg.download_file("path")
    finally:
        await client.aclose()

    assert payload == b"ok"
    assert sleeps == [2.0]
    assert len(calls) == 2
