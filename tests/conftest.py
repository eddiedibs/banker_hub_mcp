from collections.abc import AsyncIterator, Callable

import pytest
import respx
from mcp.server.fastmcp import FastMCP
from pydantic import SecretStr

from banker_hub_mcp.client import BankerHubClient
from banker_hub_mcp.config import Settings
from banker_hub_mcp.server import create_server

BASE = "https://hub.test"
TX_ID = "bnc-0123456789abcdef"


def make_settings(**kw: object) -> Settings:
    return Settings(_env_file=None, url=BASE, token=SecretStr("secret-token"), **kw)  # type: ignore[arg-type]


@pytest.fixture
async def client() -> AsyncIterator[BankerHubClient]:
    c = BankerHubClient(make_settings())
    yield c
    await c.aclose()


@pytest.fixture
def mock_api() -> respx.MockRouter:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def make_server() -> AsyncIterator[Callable[..., FastMCP]]:
    clients: list[BankerHubClient] = []

    def factory(**kw: object) -> FastMCP:
        settings = make_settings(**kw)
        c = BankerHubClient(settings)
        clients.append(c)
        return create_server(settings, c)

    yield factory
    for c in clients:
        await c.aclose()


@pytest.fixture
def server(make_server: Callable[..., FastMCP]) -> FastMCP:
    return make_server()


@pytest.fixture
def server_with_detail(make_server: Callable[..., FastMCP]) -> FastMCP:
    return make_server(include_detail=True)


def tx(**over: object) -> dict[str, object]:
    base = {
        "id": TX_ID,
        "source": "bnc",
        "external_id": "1",
        "account": "****1234",
        "kind": "transferencia",
        "direction": "debit",
        "amount": "10.50",
        "asset": "VES",
        "detail": "phone 0412 cedula V123",
        "occurred_at": "2026-09-30T12:00:00Z",
    }
    return base | over
