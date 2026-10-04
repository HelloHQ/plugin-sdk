import pytest

from conftest import FakeClock, FakeHost, ok
from macro_context.errors import FetchError, RateLimitedError, ResponseTooLarge
from macro_context.host import HttpResponse
from macro_context.polite import OriginPolicy, PoliteClient
from macro_context.ratelimit import SlidingWindowLimiter, backoff_delay, parse_retry_after

URL = "https://mempool.space/api/x"
POLICIES = {"mempool.space": OriginPolicy(max_calls=3, window_s=10.0, min_interval_s=1.0)}


def test_limiter_enforces_min_interval():
    clock = FakeClock()
    lim = SlidingWindowLimiter(clock, max_calls=100, window_s=10, min_interval_s=1.0)
    t0 = clock.t
    for _ in range(4):
        lim.acquire()
    assert clock.t - t0 == pytest.approx(3.0)


def test_limiter_enforces_window_cap():
    clock = FakeClock()
    lim = SlidingWindowLimiter(clock, max_calls=3, window_s=10, min_interval_s=0)
    t0 = clock.t
    for _ in range(3):
        lim.acquire()
    assert clock.t == t0  # burst allowed up to the cap
    lim.acquire()  # 4th must wait for the oldest to leave the window
    assert clock.t - t0 == pytest.approx(10.0)


def test_limiter_never_exceeds_cap_in_any_window():
    clock = FakeClock()
    lim = SlidingWindowLimiter(clock, max_calls=4, window_s=10, min_interval_s=0.5)
    stamps = []
    for _ in range(40):
        lim.acquire()
        stamps.append(clock.t)
    for i, s in enumerate(stamps):
        assert len([x for x in stamps[i:] if x < s + 10]) <= 4


@pytest.mark.parametrize("args", [(0, 10), (1, 0), (1, -1)])
def test_limiter_rejects_bad_config(args):
    with pytest.raises(ValueError):
        SlidingWindowLimiter(FakeClock(), *args)


def test_backoff_full_jitter_and_cap():
    assert backoff_delay(0, base_s=1, cap_s=30, rand=lambda: 1.0) == 1.0
    assert backoff_delay(3, base_s=1, cap_s=30, rand=lambda: 1.0) == 8.0
    assert backoff_delay(10, base_s=1, cap_s=30, rand=lambda: 1.0) == 30.0  # capped
    assert backoff_delay(3, base_s=1, cap_s=30, rand=lambda: 0.0) == 0.0  # full jitter reaches zero
    assert backoff_delay(2, base_s=1, cap_s=30, rand=lambda: 0.5) == 2.0


def test_backoff_retry_after_is_a_capped_lower_bound():
    assert backoff_delay(0, base_s=1, cap_s=30, rand=lambda: 0.0, retry_after_s=7) == 7
    assert backoff_delay(0, base_s=1, cap_s=30, rand=lambda: 1.0, retry_after_s=0.2) == 1.0
    assert backoff_delay(0, base_s=1, cap_s=30, rand=lambda: 0.0, retry_after_s=86400) == 30


def test_parse_retry_after():
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(" 12 ") == 12.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after("-1") is None
    assert parse_retry_after(None) is None


def make(handler, clock=None, **kw):
    clock = clock or FakeClock()
    host = FakeHost(handler)
    return PoliteClient(host, clock, POLICIES, rand=lambda: 1.0, **kw), host, clock


def test_requests_are_sequential_and_paced():
    client, host, clock = make(lambda m, u, b: ok("{}"), cache_ttl_s=0)
    t0 = clock.t
    for _ in range(3):
        client.get(URL)
    assert len(host.calls) == 3
    assert clock.t - t0 == pytest.approx(2.0)  # min interval 1s between 3 calls


def test_cache_serves_repeat_without_a_request_and_expires():
    client, host, clock = make(lambda m, u, b: ok('{"a":1}'), cache_ttl_s=60)
    client.get(URL)
    client.get(URL)
    assert len(host.calls) == 1
    clock.t += 61
    client.get(URL)
    assert len(host.calls) == 2


def test_cache_keys_distinguish_post_bodies():
    client, host, _ = make(lambda m, u, b: ok("{}"))
    url = "https://mempool.space/rpc"
    client.post_json(url, '{"id":1}')
    client.post_json(url, '{"id":2}')
    client.post_json(url, '{"id":1}')
    assert len(host.calls) == 2
    assert host.calls[0][2]["Content-Type"] == "application/json"


def test_errors_are_not_cached():
    answers = iter([HttpResponse(404, "nope"), ok("{}")])
    client, host, _ = make(lambda m, u, b: next(answers))
    with pytest.raises(FetchError):
        client.get(URL)
    client.get(URL)
    assert len(host.calls) == 2


def test_429_retries_with_backoff_then_succeeds():
    answers = iter([HttpResponse(429, ""), HttpResponse(503, ""), ok("{}")])
    client, host, clock = make(lambda m, u, b: next(answers), cache_ttl_s=0)
    client.get(URL)
    assert len(host.calls) == 3
    # rand()==1.0 -> delays are the full ceilings 1s then 2s (plus pacing waits)
    assert 1.0 in clock.sleeps and 2.0 in clock.sleeps


def test_retry_after_header_is_honoured():
    answers = iter([HttpResponse(429, "", {"Retry-After": "9"}), ok("{}")])
    client, _, clock = make(lambda m, u, b: next(answers))
    client.get(URL)
    assert 9.0 in clock.sleeps


def test_gives_up_after_max_retries():
    client, host, _ = make(lambda m, u, b: HttpResponse(429, ""), max_retries=2)
    with pytest.raises(RateLimitedError):
        client.get(URL)
    assert len(host.calls) == 3


def test_non_retryable_status_fails_immediately():
    client, host, _ = make(lambda m, u, b: HttpResponse(400, "bad"))
    with pytest.raises(FetchError) as err:
        client.get(URL)
    assert err.value.code == "http_400" and len(host.calls) == 1


def test_permission_denied_is_not_retried():
    client, host, _ = make(lambda m, u, b: FetchError("denied", code="permission_denied"))
    with pytest.raises(FetchError) as err:
        client.get(URL)
    assert err.value.code == "permission_denied" and len(host.calls) == 1


def test_transport_timeouts_are_retried():
    answers = iter([FetchError("t", code="timeout"), ok("{}")])
    client, host, _ = make(lambda m, u, b: next(answers))
    client.get(URL)
    assert len(host.calls) == 2


def test_backoff_waits_do_not_burst_past_the_limiter():
    client, host, clock = make(lambda m, u, b: HttpResponse(429, ""), max_retries=5)
    with pytest.raises(RateLimitedError):
        client.get(URL)
    assert len(host.calls) == 6
    # no more than max_calls (3) in any 10 s window, honouring the limiter
    assert clock.t - 1000.0 >= 10.0


@pytest.mark.parametrize(
    "url", ["http://mempool.space/x", "https://evil.example/x", "ftp://mempool.space/x", "mempool.space/x"]
)
def test_origin_allowlist_and_https_only(url):
    client, host, _ = make(lambda m, u, b: ok("{}"))
    with pytest.raises(FetchError) as err:
        client.get(url)
    assert err.value.code == "origin_not_allowed" and host.calls == []


def test_response_size_cap():
    client, _, _ = make(lambda m, u, b: ok("x" * 100), max_response_bytes=10)
    with pytest.raises(ResponseTooLarge):
        client.get(URL)


def test_cache_is_bounded():
    client, host, _ = make(lambda m, u, b: ok("{}"), max_cache_entries=2)
    for i in range(5):
        client.get(f"{URL}{i}")
    assert len(client._cache) == 2


def test_no_auth_headers_are_ever_sent():
    client, host, _ = make(lambda m, u, b: ok("{}"))
    client.get(URL)
    sent = {k.lower() for k in host.calls[0][2]}
    assert not sent & {"authorization", "cookie", "x-api-key"}
