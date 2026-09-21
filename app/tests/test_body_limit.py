import asyncio
from unittest.mock import AsyncMock

import pytest

from containerops.api import BodyLimitMiddleware
from containerops.domain import MAX_BODY_BYTES


@pytest.mark.parametrize(("size", "expected"), [(MAX_BODY_BYTES, 200), (MAX_BODY_BYTES + 1, 413)])
def test_separate_asgi_frames_share_one_byte_budget(size: int, expected: int) -> None:
    frames = [
        {"type": "http.request", "body": b"a" * 16384, "more_body": True},
        {"type": "http.request", "body": b"b" * (size - 16384), "more_body": False},
    ]
    receive = AsyncMock(side_effect=frames)
    send = AsyncMock()

    async def downstream(scope, incoming, outgoing):
        message = await incoming()
        assert message["body"] == frames[0]["body"] + frames[1]["body"]
        assert message["more_body"] is False
        await outgoing({"type": "http.response.start", "status": 200, "headers": []})

    app = AsyncMock(side_effect=downstream)
    asyncio.run(BodyLimitMiddleware(app)({"type": "http"}, receive, send))
    assert send.call_args_list[0].args[0]["status"] == expected
    assert app.call_count == (expected == 200)
    assert receive.call_count == 2


def test_disconnected_partial_body_never_reaches_route() -> None:
    receive = AsyncMock(
        side_effect=[
            {"type": "http.request", "body": b'{"text":', "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    app, send = AsyncMock(), AsyncMock()
    asyncio.run(BodyLimitMiddleware(app)({"type": "http"}, receive, send))
    app.assert_not_called()
    send.assert_not_called()
