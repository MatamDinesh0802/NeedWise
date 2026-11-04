from tests.conftest import ALL_HEALTHY, ClientFactory


def test_generates_a_request_id_when_none_is_sent(make_client: ClientFactory) -> None:
    client = make_client(ALL_HEALTHY)

    response = client.get("/healthz")

    assert len(response.headers["x-request-id"]) == 32


def test_echoes_a_safe_caller_request_id(make_client: ClientFactory) -> None:
    client = make_client(ALL_HEALTHY)

    response = client.get("/healthz", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["x-request-id"] == "trace-abc.123"


def test_replaces_an_unsafe_caller_request_id(make_client: ClientFactory) -> None:
    client = make_client(ALL_HEALTHY)

    unsafe = "abc def<script>" + "x" * 100
    response = client.get("/healthz", headers={"X-Request-ID": unsafe})

    assert response.headers["x-request-id"] != unsafe
    assert len(response.headers["x-request-id"]) == 32
