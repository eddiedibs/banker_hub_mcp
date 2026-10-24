import httpx
import pytest
import respx

from banker_hub_mcp.client import BankerHubClient, BankerHubError

from .conftest import BASE, make_settings, tx


async def test_auth_header_and_params(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/transactions").mock(
        return_value=httpx.Response(200, json={"items": [tx()]})
    )
    page = await client.list_transactions({"source": "bnc", "asset": None, "limit": 5})
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer secret-token"
    assert dict(req.url.params) == {"source": "bnc", "limit": "5"}
    assert str(page.items[0].amount) == "10.50"


async def test_paging_cursor(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions", params={"cursor": "c1"}).mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    mock_api.get("/transactions").mock(
        return_value=httpx.Response(200, json={"items": [tx()], "next_cursor": "c1"})
    )
    first = await client.list_transactions({})
    assert first.next_cursor == "c1"
    second = await client.list_transactions({"cursor": first.next_cursor})
    assert second.items == [] and second.next_cursor is None


async def test_get_transaction_escapes_id(
    client: BankerHubClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(url__regex=r".*/transactions/.*").mock(
        return_value=httpx.Response(200, json=tx())
    )
    await client.get_transaction("a/b")
    assert route.calls.last.request.url.raw_path == b"/transactions/a%2Fb"


async def test_summary_and_providers(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/summary").mock(
        return_value=httpx.Response(
            200,
            json={
                "totals": [
                    {
                        "asset": "VES",
                        "currency_label": "Bs",
                        "credit": "5",
                        "debit": "2",
                        "net": "3",
                        "count": 2,
                    }
                ]
            },
        )
    )
    mock_api.get("/providers").mock(
        return_value=httpx.Response(
            200,
            json={
                "providers": [
                    {"name": "bnc", "sync_state": {"source": "bnc", "rows_upserted": 3}},
                    {"name": "paypal"},
                ]
            },
        )
    )
    assert (await client.summary({}))[0].count == 2
    providers = await client.providers()
    assert providers[0].sync_state is not None and providers[1].sync_state is None


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": "unauthorized"}, "401"),
        (404, {"error": "transaction not found"}, "not found: transaction not found"),
        (400, {"error": "direction must be debit or credit"}, "invalid request: direction"),
        (500, {"error": "internal error"}, r"server error \(500\)"),
        (502, None, r"server error \(502\)"),
    ],
)
async def test_error_mapping(
    client: BankerHubClient,
    mock_api: respx.MockRouter,
    status: int,
    body: dict[str, str] | None,
    expected: str,
) -> None:
    mock_api.get("/transactions").mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(BankerHubError, match=expected) as exc:
        await client.list_transactions({})
    assert "secret-token" not in str(exc.value)


async def test_plain_text_404(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions/x").mock(
        return_value=httpx.Response(404, text="404 page not found")
    )
    with pytest.raises(BankerHubError, match="not found"):
        await client.get_transaction("x")


async def test_http_error_has_no_token(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions").mock(side_effect=httpx.RemoteProtocolError("boom"))
    with pytest.raises(BankerHubError) as exc:
        await client.list_transactions({})
    assert "secret-token" not in str(exc.value)


@pytest.mark.parametrize("path", ["/summary", "/providers"])
async def test_non_dict_body(
    client: BankerHubClient, mock_api: respx.MockRouter, path: str
) -> None:
    mock_api.get(path).mock(return_value=httpx.Response(200, json=[1, 2]))
    call = client.summary({}) if path == "/summary" else client.providers()
    with pytest.raises(BankerHubError, match="unexpected"):
        await call


async def test_connect_error(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(BankerHubError, match=f"not reachable at {BASE}") as exc:
        await client.list_transactions({})
    assert "secret-token" not in str(exc.value)


async def test_timeout(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(BankerHubError, match="timed out"):
        await client.list_transactions({})


async def test_bad_shape(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "x"}]})
    )
    with pytest.raises(BankerHubError, match="unexpected"):
        await client.list_transactions({})


async def test_validation_error_does_not_leak_values(
    client: BankerHubClient, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/transactions").mock(
        return_value=httpx.Response(
            200, json={"items": [tx(amount="SECRET-VALUE", description="PRIVATE-NAME")]}
        )
    )
    with pytest.raises(BankerHubError) as exc:
        await client.list_transactions({})
    text = str(exc.value)
    assert "amount" in text and "SECRET-VALUE" not in text and "PRIVATE-NAME" not in text
    assert len(text) <= 300


async def test_redirect_is_error(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions").mock(
        return_value=httpx.Response(302, headers={"Location": "http://evil.test/"})
    )
    with pytest.raises(BankerHubError, match="redirect"):
        await client.list_transactions({})


async def test_plain_text_405(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions").mock(return_value=httpx.Response(405, text="nope"))
    with pytest.raises(BankerHubError, match=r"\(405\): Method Not Allowed"):
        await client.list_transactions({})


async def test_http_error_message_is_type_only(
    client: BankerHubClient, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/transactions").mock(side_effect=httpx.RemoteProtocolError("secret-token"))
    with pytest.raises(BankerHubError) as exc:
        await client.list_transactions({})
    assert str(exc.value) == "banker_hub request failed (RemoteProtocolError)"


async def test_invalid_url(client: BankerHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/transactions").mock(side_effect=httpx.InvalidURL("secret-token"))
    with pytest.raises(BankerHubError, match="invalid request URL"):
        await client.list_transactions({})


async def test_response_size_cap(
    client: BankerHubClient, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("banker_hub_mcp.client.MAX_BODY", 100)
    mock_api.get("/transactions").mock(return_value=httpx.Response(200, content=b"x" * 500))
    with pytest.raises(BankerHubError, match="too large"):
        await client.list_transactions({})


async def test_probe_warns_and_never_raises(
    client: BankerHubClient, mock_api: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    mock_api.get("/providers").mock(return_value=httpx.Response(401, json={"error": "x"}))
    await client.probe()
    assert "401" in caplog.text and "secret-token" not in caplog.text
    mock_api.get("/providers").mock(side_effect=httpx.ConnectError("down"))
    await client.probe()
    assert "not reachable" in caplog.text


def test_client_ignores_proxy_env() -> None:
    c = BankerHubClient(make_settings())
    assert c._http._trust_env is False
