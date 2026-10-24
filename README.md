# banker_hub_mcp

Local, read-only [MCP](https://modelcontextprotocol.io) server that exposes the transaction
history aggregated by `banker_hub` to LLM clients such as Claude Code and Claude Desktop.
It talks to the banker_hub HTTP API and runs over stdio.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). banker_hub must be running
(`make run` in `banker_hub`).

```sh
uv sync
cp .env.example .env && chmod 600 .env   # set BANKER_HUB_TOKEN to banker_hub's API_TOKEN
```

| Variable | Default | Description |
| --- | --- | --- |
| `BANKER_HUB_URL` | `http://127.0.0.1:8080` | banker_hub base URL |
| `BANKER_HUB_TOKEN` | required | Bearer token (banker_hub `API_TOKEN`) |
| `BANKER_HUB_TIMEOUT` | `15` | Request timeout in seconds |
| `BANKER_HUB_INCLUDE_DETAIL` | `false` | Include the `detail` field (counterparty PII such as phone and cedula). Off by default because tool output is sent to the LLM provider. |
| `BANKER_HUB_REDACT_COUNTERPARTY` | `true` | While `INCLUDE_DETAIL` is false, replace each row's `description` with a safe value because free text can name the counterparty: BNC and any source without a redaction rule get the row `kind`; Binance pay/c2c rows lose the trailing ` - <name>`. |
| `BANKER_HUB_ALLOW_INSECURE` | `false` | The server refuses plain `http://` to a non-loopback host unless this is `true`. |
| `BANKER_HUB_ENV_FILE` | `.env` | Path of the env file; the default is `.env` in the working directory. |

## Register

The registration commands below use `uv --directory <repo>`, which sets the working directory,
so the server finds the repo's `.env` (set `BANKER_HUB_ENV_FILE` to use another path). Prefer a
`chmod 600 .env` holding the token over passing `-e BANKER_HUB_TOKEN=...` on the command line,
where it ends up in shell history and client config.

Claude Code:

```sh
claude mcp add banker-hub -- \
  uv --directory /home/edd1e/projs/portfolio_stuff/banker_hub_mcp run --frozen banker-hub-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "banker-hub": {
      "command": "uv",
      "args": [
        "--directory",
        "/home/edd1e/projs/portfolio_stuff/banker_hub_mcp",
        "run",
        "--frozen",
        "banker-hub-mcp"
      ]
    }
  }
}
```

## Tools

All tools are read-only. Dates are `YYYY-MM-DD` or RFC3339; a date-only `to_date` includes
that whole day. Amounts are returned as decimal strings.

- `get_binance_transactions` / `get_bnc_transactions`: `from_date`, `to_date`, `asset`,
  `direction` (`debit`|`credit`), `limit` (1-100, default 50), `cursor`. Returns
  `{source, items, next_cursor, count}`; pass `next_cursor` back as `cursor` for the next page.
- `list_sources`: available sources with sync freshness (`last_success_at`, `last_error`).
- `get_transaction`: one transaction by `transaction_id`.
- `get_summary`: per-asset credit/debit/net/count, filterable by `source`, `asset`,
  `direction`, `from_date`, `to_date`.

New sources are added to `SOURCES` in `src/banker_hub_mcp/sources.py`.

## Development

```sh
uv run ruff check . && uv run ruff format --check . && uv run pytest
```
