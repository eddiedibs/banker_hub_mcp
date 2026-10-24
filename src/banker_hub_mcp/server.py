import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from .client import BankerHubClient
from .config import Settings, safe_url
from .tools import register_tools

log = logging.getLogger("banker_hub_mcp")


class _ClientHolder:
    """Holds the client of the active lifespan; entries nest and restore the previous one."""

    def __init__(self, client: BankerHubClient | None) -> None:
        self.client = client

    def get(self) -> BankerHubClient:
        if self.client is None:
            raise ToolError("server not initialized")
        return self.client


def create_server(settings: Settings, client: BankerHubClient | None = None) -> FastMCP:
    """Build the app. A caller-supplied client is used as is and is not closed here."""
    holder = _ClientHolder(client)

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        previous = holder.client
        owned = client is None
        if owned:
            holder.client = BankerHubClient(settings)
            await holder.client.probe()
        try:
            yield
        finally:
            current = holder.client
            holder.client = previous
            if owned and current is not None:
                await current.aclose()

    mcp = FastMCP("banker-hub", lifespan=lifespan)
    register_tools(mcp, holder.get, settings)
    return mcp


def _config_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [
            f"BANKER_HUB_{str(e['loc'][0]).upper() if e['loc'] else 'URL'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        ]
        return "; ".join(parts)
    return f"could not load settings ({type(exc).__name__})"


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings()  # type: ignore[call-arg]
    except (ValidationError, SettingsError, OSError) as exc:
        log.error("invalid configuration: %s", _config_error(exc))
        raise SystemExit(1) from None
    if settings.url.startswith("http://") and settings.allow_insecure:
        log.warning("using plain http to a remote host (BANKER_HUB_ALLOW_INSECURE)")
    log.info("starting banker-hub MCP server (banker_hub at %s)", safe_url(settings.url))
    create_server(settings).run(transport="stdio")


if __name__ == "__main__":
    main()
