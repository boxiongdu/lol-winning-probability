from unittest.mock import Mock
import pytest
import requests
from src.ingestion.client import RiotClient, RiotError


def response(code, data=None, headers=None):
    return Mock(status_code=code, headers=headers or {}, json=Mock(return_value=data))


def client(responses):
    session = Mock(headers={})
    session.get.side_effect = responses
    sleep = Mock()
    return RiotClient("test-only", interval=0, session=session, sleep=sleep), session, sleep


def test_retry_after_is_respected():
    c, session, sleep = client([response(429, headers={"Retry-After": "61"}), response(200, {})])
    assert c.get("/test") == {}
    assert any(call.args[0] >= 61 for call in sleep.call_args_list)
    assert c.requests == 2 and c.retry_count == 1
    assert session.headers["X-Riot-Token"] == "test-only"
    assert session.get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_errors_are_not_retried(code):
    c, session, _ = client([response(code)])
    with pytest.raises(RiotError) as exc:
        c.get("/test")
    assert exc.value.status == code
    assert "test-only" not in str(exc.value)
    assert session.get.call_count == 1


def test_network_and_server_retries():
    c, _, _ = client([requests.Timeout(), response(503), response(200, ["ok"])])
    assert c.get("/test") == ["ok"]
    assert c.retry_count == 2


def test_retry_budget_is_bounded():
    c, session, _ = client([response(500)] * 5)
    with pytest.raises(RiotError, match="budget"):
        c.get("/test")
    assert session.get.call_count == 5


def test_pagination_and_deduplication():
    c, session, _ = client([response(200, [f"NA1_{i}" for i in range(100)]),
                             response(200, ["NA1_99", "NA1_100"])])
    ids = list(c.match_ids("a/b", limit=200, start_time=100, end_time=200))
    assert len(ids) == 101
    assert session.get.call_args.kwargs["params"] == dict(start=100, count=100, queue=420, startTime=100, endTime=200)
    assert "a%2Fb" in session.get.call_args.args[0]


def test_resolve_encodes_riot_id():
    c, session, _ = client([response(200, {"puuid": "example"})])
    assert c.resolve("a/b", "a b") == "example"
    assert "/a%2Fb/a%20b" in session.get.call_args.args[0]


def test_missing_key_and_bad_region():
    with pytest.raises(ValueError):
        RiotClient("")
    with pytest.raises(ValueError):
        RiotClient("example", region="na1")
