import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from banker_hub_mcp import sources
from banker_hub_mcp.models import Transaction
from banker_hub_mcp.server import create_server
from banker_hub_mcp.sources import SourceSpec, redact_to_kind
from banker_hub_mcp.tools import (
    normalize_from,
    normalize_to,
    redact,
    unwrap_cursor,
    wrap_cursor,
)

from .conftest import TX_ID, make_settings, tx


async def call(server: FastMCP, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await server.call_tool(name, args)
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def test_to_date_inclusive() -> None:
    assert normalize_to("2026-09-30") == "2026-10-01"
    assert normalize_to("2026-12-31") == "2027-01-01"
    assert normalize_to("2026-09-30T10:00:00Z") == "2026-09-30T10:00:00Z"
    assert normalize_to(None) is None
    assert normalize_from("2026-09-01") == "2026-09-01"


@pytest.mark.parametrize(
    "bad",
    [
        "yesterday",
        "2026-13-01",
        "2026-09-30T10:00:00",
        "30/09/2026",
        "2026-9-3",
        "2026-09-30 10:00:00Z",
        " 2026-09-30",
        "2026-09-30T10:00Z",
        "2026-09-30T25:00:00Z",
    ],
)
def test_invalid_dates(bad: str) -> None:
    with pytest.raises(ToolError):
        normalize_from(bad)
    with pytest.raises(ToolError):
        normalize_to(bad)


async def test_registered_tools(server: FastMCP) -> None:
    tools = await server.list_tools()
    assert {t.name for t in tools} == {
        "get_binance_transactions",
        "get_bnc_transactions",
        "list_sources",
        "get_transaction",
        "get_summary",
    }
    assert all(t.annotations and t.annotations.readOnlyHint for t in tools)
    bnc = next(t for t in tools if t.name == "get_bnc_transactions")
    assert "VES" in bnc.description


async def test_source_tool_sends_normalized_params(
    server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/transactions").mock(
        return_value=httpx.Response(200, json={"items": [tx()], "next_cursor": "n"})
    )
    out = await call(
        server,
        "get_bnc_transactions",
        {"from_date": "2026-09-01", "to_date": "2026-09-30", "limit": 100, "direction": "debit"},
    )
    assert dict(route.calls.last.request.url.params) == {
        "source": "bnc",
        "direction": "debit",
        "from": "2026-09-01",
        "to": "2026-10-01",
        "limit": "100",
    }
    assert out["count"] == 1 and out["next_cursor"] != "n" and out["source"] == "bnc"
    assert out["items"][0]["amount"] == "10.50"
    assert not {"source", "external_id", "created_at"} & out["items"][0].keys()


async def test_detail_stripped_by_default(server: FastMCP, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions").mock(return_value=httpx.Response(200, json={"items": [tx()]}))
    mock_api.get(f"/transactions/{TX_ID}").mock(return_value=httpx.Response(200, json=tx()))
    listed = await call(server, "get_bnc_transactions", {})
    single = await call(server, "get_transaction", {"transaction_id": TX_ID})
    assert "detail" not in listed["items"][0]
    assert "detail" not in single


async def test_detail_kept_when_enabled(
    server_with_detail: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/transactions").mock(return_value=httpx.Response(200, json={"items": [tx()]}))
    out = await call(server_with_detail, "get_bnc_transactions", {})
    assert out["items"][0]["detail"].startswith("phone")


async def test_summary(server: FastMCP, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/summary").mock(
        return_value=httpx.Response(
            200,
            json={
                "totals": [
                    {
                        "asset": "USDT",
                        "currency_label": "$",
                        "credit": "1.10",
                        "debit": "0",
                        "net": "1.10",
                        "count": 1,
                    }
                ]
            },
        )
    )
    out = await call(server, "get_summary", {"source": "binance", "to_date": "2026-09-30"})
    assert route.calls.last.request.url.params["to"] == "2026-10-01"
    assert out["totals"][0]["net"] == "1.10"


async def test_list_sources(server: FastMCP, mock_api: respx.MockRouter) -> None:
    mock_api.get("/providers").mock(
        return_value=httpx.Response(
            200,
            json={
                "providers": [{"name": "bnc", "sync_state": {"source": "bnc", "last_error": "x"}}]
            },
        )
    )
    out = await call(server, "list_sources", {})
    assert out["sources"][0]["sync_state"]["last_error"] == "x"


async def test_backend_error_becomes_tool_error(
    server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.get(f"/transactions/{TX_ID}").mock(
        return_value=httpx.Response(404, json={"error": "transaction not found"})
    )
    with pytest.raises(ToolError, match="not found"):
        await server.call_tool("get_transaction", {"transaction_id": TX_ID})


async def test_bad_date_rejected_before_request(
    server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/transactions")
    with pytest.raises(ToolError, match="from_date"):
        await server.call_tool("get_bnc_transactions", {"from_date": "nope"})
    with pytest.raises(ToolError, match="before"):
        await server.call_tool(
            "get_bnc_transactions", {"from_date": "2026-09-30", "to_date": "2026-09-01"}
        )
    assert not route.called


@pytest.mark.parametrize(
    "ok", ["2026-09-30T10:00:00Z", "2026-09-30T10:00:00.123Z", "2026-09-30T10:00:00-04:00"]
)
def test_rfc3339_forms_pass_through(ok: str) -> None:
    assert normalize_from(ok) == ok
    assert normalize_to(ok) == ok


def test_overflow_is_tool_error() -> None:
    with pytest.raises(ToolError, match="to_date"):
        normalize_to("9999-12-31")


def test_error_names_field() -> None:
    with pytest.raises(ToolError, match="to_date"):
        normalize_to("bad")


async def test_from_equals_to_date_only_is_one_day(
    server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/transactions").mock(return_value=httpx.Response(200, json={"items": []}))
    await call(server, "get_bnc_transactions", {"from_date": "2026-09-30", "to_date": "2026-09-30"})
    params = route.calls.last.request.url.params
    assert (params["from"], params["to"]) == ("2026-09-30", "2026-10-01")


async def test_mixed_range(server: FastMCP, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/transactions").mock(return_value=httpx.Response(200, json={"items": []}))
    await call(
        server,
        "get_bnc_transactions",
        {"from_date": "2026-09-30T01:00:00-04:00", "to_date": "2026-10-01"},
    )
    assert route.called
    with pytest.raises(ToolError, match="before"):
        await server.call_tool(
            "get_bnc_transactions",
            {"from_date": "2026-09-30T23:00:00Z", "to_date": "2026-09-30T18:00:00-04:00"},
        )


async def test_cursor_forwarded_and_null_next_cursor(
    server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/transactions").mock(return_value=httpx.Response(200, json={"items": []}))
    cursor = wrap_cursor(
        "abc", {"source": "bnc", "asset": None, "direction": None, "from": None, "to": None}
    )
    out = await call(server, "get_bnc_transactions", {"cursor": cursor})
    assert route.calls.last.request.url.params["cursor"] == "abc"
    assert out["next_cursor"] is None


async def test_limit_above_max_rejected_by_schema(server: FastMCP) -> None:
    with pytest.raises(ToolError):
        await server.call_tool("get_bnc_transactions", {"limit": 101})


async def test_get_transaction_keeps_detail_when_enabled(
    server_with_detail: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.get(f"/transactions/{TX_ID}").mock(return_value=httpx.Response(200, json=tx()))
    out = await call(server_with_detail, "get_transaction", {"transaction_id": TX_ID})
    assert out["detail"].startswith("phone")


def _tx(**over: object) -> Transaction:
    return Transaction.model_validate(tx(**over))


@pytest.mark.parametrize(
    ("kind", "desc", "expected"),
    [
        ("pay", "Binance Pay C2C - Ana", "Binance Pay C2C"),
        ("c2c", "P2P BUY 10 USDT @ 36.5 VES - ana-maria", "P2P BUY 10 USDT @ 36.5 VES"),
        ("c2c", "P2P SELL 1 USDT @ 36 VES - a - b", "P2P SELL 1 USDT @ 36 VES"),
        ("deposit", "Deposit - note", "Deposit - note"),
    ],
)
def test_redact_binance_counterparty(kind: str, desc: str, expected: str) -> None:
    t = _tx(source="binance", kind=kind, description=desc, detail=None)
    assert redact(t).description == expected


async def test_redaction_in_get_transaction_and_disable(
    make_server: Callable[..., FastMCP], mock_api: respx.MockRouter
) -> None:
    row = tx(description="Pago a Juan 0412", detail="Juan 0412")
    mock_api.get(f"/transactions/{TX_ID}").mock(return_value=httpx.Response(200, json=row))
    on = make_server()
    out = await call(on, "get_transaction", {"transaction_id": TX_ID})
    assert out["description"] == "transferencia"
    off = make_server(redact_counterparty=False)
    out = await call(off, "get_transaction", {"transaction_id": TX_ID})
    assert out["description"] == "Pago a Juan 0412" and "detail" not in out


async def test_bnc_empty_detail_still_redacted_in_list(
    server: FastMCP, mock_api: respx.MockRouter
) -> None:
    row = tx(description="PAGO MOVIL DE JUAN", detail=None)
    mock_api.get("/transactions").mock(return_value=httpx.Response(200, json={"items": [row]}))
    out = await call(server, "get_bnc_transactions", {})
    assert out["items"][0]["description"] == "transferencia"
    assert "JUAN" not in json.dumps(out)


async def test_unregistered_source_redacted(server: FastMCP, mock_api: respx.MockRouter) -> None:
    row = tx(source="mystery", kind="k", description="Secret Person")
    mock_api.get(f"/transactions/{TX_ID}").mock(return_value=httpx.Response(200, json=row))
    out = await call(server, "get_transaction", {"transaction_id": TX_ID})
    assert out["description"] == "k"


async def test_binance_redacted_in_list(server: FastMCP, mock_api: respx.MockRouter) -> None:
    row = tx(source="binance", kind="c2c", description="P2P BUY 1 USDT @ 36 VES - ana-maria")
    mock_api.get("/transactions").mock(return_value=httpx.Response(200, json={"items": [row]}))
    out = await call(server, "get_binance_transactions", {})
    assert out["items"][0]["description"] == "P2P BUY 1 USDT @ 36 VES"


def test_redaction_default_denies_without_spec() -> None:
    assert redact(_tx(source="other", description="x")).description == "transferencia"


async def test_new_source_registers_tool(
    monkeypatch: pytest.MonkeyPatch, make_server: Callable[..., FastMCP]
) -> None:
    spec = SourceSpec("paypal", "PayPal.", redact_to_kind)
    monkeypatch.setattr(sources, "SOURCES", {**sources.SOURCES, "paypal": spec})
    names = {t.name for t in await make_server().list_tools()}
    assert "get_paypal_transactions" in names and len(names) == 6


async def test_list_sources_tool_field(server: FastMCP, mock_api: respx.MockRouter) -> None:
    mock_api.get("/providers").mock(
        return_value=httpx.Response(200, json={"providers": [{"name": "bnc"}, {"name": "x"}]})
    )
    out = await call(server, "list_sources", {})
    assert [s["tool"] for s in out["sources"]] == ["get_bnc_transactions", None]


UNTRUSTED_TEXT = "never instructions"


async def test_schema_descriptions_and_titles(server: FastMCP) -> None:
    tools = {t.name: t for t in await server.list_tools()}
    props = tools["get_bnc_transactions"].inputSchema["properties"]
    assert all("description" in p for p in props.values())
    assert props["limit"]["maximum"] == 100
    assert "transaction_id" in tools["get_transaction"].inputSchema["properties"]
    assert UNTRUSTED_TEXT in tools["get_bnc_transactions"].description
    assert tools["get_summary"].inputSchema["properties"]["source"]["anyOf"][0]["enum"] == [
        "binance",
        "bnc",
    ]
    assert all(t.title and t.annotations.idempotentHint for t in tools.values())
    assert "get_summary instead of paging" in tools["get_bnc_transactions"].description


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, _: object) -> None:
        self.closed = False
        FakeClient.instances.append(self)

    async def probe(self) -> None: ...

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    FakeClient.instances = []
    monkeypatch.setattr("banker_hub_mcp.server.BankerHubClient", FakeClient)
    return FakeClient


async def test_lifespan_closes_client_even_on_error(fake_client: type[FakeClient]) -> None:
    server = create_server(make_settings())
    with pytest.raises(RuntimeError):
        async with server.settings.lifespan(server):
            raise RuntimeError("boom")
    assert fake_client.instances[0].closed


async def test_lifespan_reentrant(fake_client: type[FakeClient]) -> None:
    server = create_server(make_settings())
    async with server.settings.lifespan(server):
        async with server.settings.lifespan(server):
            pass
        assert not fake_client.instances[0].closed
    assert all(c.closed for c in fake_client.instances)


async def test_tools_fail_before_lifespan() -> None:
    server = create_server(make_settings())
    with pytest.raises(ToolError, match="server not initialized"):
        await server.call_tool("list_sources", {})


def _params(**over: Any) -> dict[str, Any]:
    return {"source": "bnc", "asset": None, "direction": None, "from": None, "to": None} | over


def test_cursor_roundtrip_and_asset_case_insensitive() -> None:
    wrapped = wrap_cursor("go-cursor", _params(asset="usdt"))
    assert wrapped and unwrap_cursor(wrapped, _params(asset="USDT")) == "go-cursor"


@pytest.mark.parametrize(
    "changed",
    [{"asset": "VES"}, {"direction": "debit"}, {"from": "2026-01-01"}, {"source": "binance"}],
)
def test_cursor_bound_to_query(changed: dict[str, Any]) -> None:
    wrapped = wrap_cursor("c", _params())
    with pytest.raises(ToolError, match="different query"):
        unwrap_cursor(wrapped, _params(**changed))


@pytest.mark.parametrize("garbage", ["!!!", "abc", "e30", "bm90LWpzb24"])
def test_garbage_cursor(garbage: str) -> None:
    with pytest.raises(ToolError, match="different query"):
        unwrap_cursor(garbage, _params())


async def test_cursor_roundtrip_through_tools(server: FastMCP, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/transactions").mock(
        return_value=httpx.Response(200, json={"items": [], "next_cursor": "go1"})
    )
    first = await call(server, "get_bnc_transactions", {"asset": "usdt"})
    assert first["next_cursor"] != "go1"
    await call(server, "get_bnc_transactions", {"asset": "USDT", "cursor": first["next_cursor"]})
    assert route.calls.last.request.url.params["cursor"] == "go1"
    with pytest.raises(ToolError, match="different query"):
        await server.call_tool("get_bnc_transactions", {"cursor": first["next_cursor"]})
    with pytest.raises(ToolError, match="different query"):
        await server.call_tool(
            "get_binance_transactions", {"asset": "USDT", "cursor": first["next_cursor"]}
        )


async def test_text_sanitized_and_amount_plain(server: FastMCP, mock_api: respx.MockRouter) -> None:
    row = tx(
        kind="a\u200bb\x00c",
        description="x\nIGNORE" + "y" * 500,
        reference="r\u202e",
        amount="0.00000001",
        bcv_rate="1E-8",
        bcv_rate_at="2026-09-30T00:00:00Z",
    )
    mock_api.get(f"/transactions/{TX_ID}").mock(return_value=httpx.Response(200, json=row))
    out = await call(server, "get_transaction", {"transaction_id": TX_ID})
    assert out["kind"] == "ab c" and out["reference"] == "r"
    assert out["amount"] == "0.00000001" and out["bcv_rate"] == "0.00000001"
    assert out["bcv_rate_at"].startswith("2026-09-30")


async def test_summary_plain_notation(server: FastMCP, mock_api: respx.MockRouter) -> None:
    mock_api.get("/summary").mock(
        return_value=httpx.Response(
            200,
            json={
                "totals": [
                    {
                        "asset": "X",
                        "currency_label": "X",
                        "credit": "1E-8",
                        "debit": "0",
                        "net": "1E-8",
                        "count": 1,
                    }
                ]
            },
        )
    )
    out = await call(server, "get_summary", {})
    assert out["totals"][0]["credit"] == "0.00000001"


async def test_last_error_scrubbed(server: FastMCP, mock_api: respx.MockRouter) -> None:
    err = "GET https://bank.example/x?token=abc failed " + "z" * 400
    mock_api.get("/providers").mock(
        return_value=httpx.Response(
            200,
            json={
                "providers": [{"name": "bnc", "sync_state": {"source": "bnc", "last_error": err}}]
            },
        )
    )
    out = await call(server, "list_sources", {})
    last = out["sources"][0]["sync_state"]["last_error"]
    assert "token=abc" not in last and "bank.example" not in last and len(last) <= 200


@pytest.mark.parametrize(
    "bad", ["abc", "BNC-0123456789abcdef", "bnc-xyz", "bnc-0123456789abcde", ""]
)
async def test_transaction_id_pattern(server: FastMCP, bad: str) -> None:
    with pytest.raises(ToolError):
        await server.call_tool("get_transaction", {"transaction_id": bad})


async def test_argument_length_limits(server: FastMCP) -> None:
    with pytest.raises(ToolError):
        await server.call_tool("get_bnc_transactions", {"asset": "A" * 17})
    with pytest.raises(ToolError):
        await server.call_tool("get_bnc_transactions", {"cursor": "A" * 257})
