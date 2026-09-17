"""A slow display should cost a reading, not every reading."""

from __future__ import annotations

import asyncio


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ the rule itself


def test_the_first_failures_keep_what_we_have(patience):
    rule = patience.Patience(interval=1800, retry_interval=300, limit=3)
    assert rule.failed(has_data=True) is False
    assert rule.failed(has_data=True) is False
    assert rule.failed(has_data=True) is True    # three in a row: it is gone


def test_with_nothing_to_show_a_failure_is_shown_at_once(patience):
    rule = patience.Patience(interval=1800, retry_interval=300)
    assert rule.failed(has_data=False) is True


def test_a_failure_makes_the_next_try_sooner(patience):
    rule = patience.Patience(interval=1800, retry_interval=300)
    assert rule.seconds == 1800
    rule.failed(has_data=True)
    assert rule.seconds == 300
    rule.worked()
    assert rule.seconds == 1800


def test_trying_again_is_never_slower_than_the_normal_pace(patience):
    rule = patience.Patience(interval=120, retry_interval=300)
    rule.failed(has_data=True)
    assert rule.seconds == 120


# -------------------------------------------------- asking the display again


class FakeResponse:
    def __init__(self, status=200, body=b"1|2|3"):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return self._body


class FakeSession:
    """Answers according to a script of "timeout" and "ok"."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, str, float]] = []

    async def _answer(self, kind, url, timeout):
        self.calls.append((kind, url, timeout.total))
        what = self.script.pop(0) if self.script else "ok"
        if what == "timeout":
            raise asyncio.TimeoutError
        return FakeResponse()

    async def get(self, url, timeout=None):
        return await self._answer("get", url, timeout)

    async def post(self, url, data=None, timeout=None):
        return await self._answer("post", url, timeout)


def test_a_slow_reading_is_asked_for_once_more(web_api):
    session = FakeSession(["timeout", "ok"])
    client = web_api.CtcWebClient(session, "10.0.0.1")
    assert run(client.async_vars(7)) == [1, 2, 3]
    assert [kind for kind, _url, _t in session.calls] == ["get", "get"]
    # The second attempt waits longer than the first.
    assert session.calls[1][2] > session.calls[0][2]


def test_a_reading_that_never_comes_is_a_failure(web_api):
    session = FakeSession(["timeout", "timeout"])
    client = web_api.CtcWebClient(session, "10.0.0.1")
    try:
        run(client.async_vars(7))
    except web_api.CtcWebError as err:
        assert "timed out" in str(err)
    else:
        raise AssertionError("borde ha gett upp")
    assert len(session.calls) == 2


def test_a_tap_is_never_repeated(web_api):
    # A tap that landed and only looked like a failure would move the panel
    # a second time, which is the one thing that must not happen.
    session = FakeSession(["timeout"])
    client = web_api.CtcWebClient(session, "10.0.0.1")
    try:
        run(client.async_click([1], 100, 100))
    except web_api.CtcWebError:
        pass
    else:
        raise AssertionError("borde ha gett upp")
    assert [kind for kind, _url, _t in session.calls] == ["post"]
