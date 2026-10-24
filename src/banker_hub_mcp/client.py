import json
import logging
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings, safe_url
from .models import (
    Page,
    Provider,
    ProvidersResponse,
    SummaryResponse,
    SummaryTotal,
    Transaction,
)

T = TypeVar("T", bound=BaseModel)

MAX_BODY = 5 * 1024 * 1024
PROBE_TIMEOUT = 2.0
log = logging.getLogger("banker_hub_mcp")


class BankerHubError(Exception):
    """A failed banker_hub call, with a message safe to show to the LLM."""


class BankerHubClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._url = safe_url(settings.url)
        self._http = http or httpx.AsyncClient(
            base_url=settings.url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.token.get_secret_value()}"},
            timeout=settings.timeout,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _read(self, resp: httpx.Response) -> bytes:
        body = bytearray()
        async for chunk in resp.aiter_bytes():
            body += chunk
            if len(body) > MAX_BODY:
                raise BankerHubError("banker_hub response too large")
        return bytes(body)

    async def _get(
        self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        try:
            async with self._http.stream("GET", path, params=clean, **extra) as resp:
                body = await self._read(resp)
        except httpx.ConnectError as exc:
            raise BankerHubError(
                f"banker_hub not reachable at {self._url} - run `make run` in banker_hub"
            ) from exc
        except httpx.TimeoutException as exc:
            raise BankerHubError(f"banker_hub timed out at {self._url}") from exc
        except httpx.InvalidURL as exc:
            raise BankerHubError("invalid request URL") from exc
        except httpx.HTTPError as exc:
            raise BankerHubError(f"banker_hub request failed ({type(exc).__name__})") from exc

        if not resp.is_success:
            raise BankerHubError(self._error_message(resp, body))
        try:
            return json.loads(body)
        except ValueError as exc:
            raise BankerHubError("banker_hub returned a non-JSON response") from exc

    @staticmethod
    def _error_message(resp: httpx.Response, body: bytes) -> str:
        status = resp.status_code
        detail = ""
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                detail = str(parsed.get("error", ""))
        except ValueError:
            pass
        if not detail:
            detail = resp.reason_phrase or body[:100].decode("utf-8", "replace")
        detail = detail[:200]
        if 300 <= status < 400:
            return f"unexpected redirect from banker_hub ({status})"
        if status == 401:
            return "banker_hub rejected the token (401) - check BANKER_HUB_TOKEN"
        if status == 404:
            return f"not found: {detail}"
        if status == 400:
            return f"invalid request: {detail}"
        if status >= 500:
            return f"banker_hub server error ({status}): {detail}"
        return f"banker_hub error ({status}): {detail}"

    @staticmethod
    def _parse(model: type[T], data: Any) -> T:
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            pairs = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}"
                for e in exc.errors(include_input=False, include_url=False)
            )
            raise BankerHubError(f"unexpected banker_hub response shape: {pairs}"[:300]) from exc

    async def list_transactions(self, params: dict[str, Any]) -> Page:
        return self._parse(Page, await self._get("/transactions", params))

    async def get_transaction(self, tx_id: str) -> Transaction:
        data = await self._get(f"/transactions/{quote(tx_id, safe='')}")
        return self._parse(Transaction, data)

    async def summary(self, params: dict[str, Any]) -> list[SummaryTotal]:
        data = await self._get("/summary", params)
        return self._parse(SummaryResponse, data).totals or []

    async def providers(self) -> list[Provider]:
        data = await self._get("/providers")
        return self._parse(ProvidersResponse, data).providers or []

    async def probe(self) -> None:
        """Warn on stderr if banker_hub is unreachable or rejects the token; never raises."""
        try:
            await self._get("/providers", timeout=PROBE_TIMEOUT)
        except BankerHubError as exc:
            log.warning("startup check failed: %s", exc)
