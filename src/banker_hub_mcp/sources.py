import re
from collections.abc import Callable
from dataclasses import dataclass

from .models import Transaction

_COUNTERPARTY_SUFFIX = re.compile(r" - .*$", re.DOTALL)


def _redact_binance(tx: Transaction) -> str | None:
    """Pay and C2C descriptions end with " - <counterparty name>"."""
    if tx.kind in {"pay", "c2c"} and tx.description:
        return _COUNTERPARTY_SUFFIX.sub("", tx.description)
    return tx.description


def redact_to_kind(tx: Transaction) -> str | None:
    return tx.kind


@dataclass(frozen=True)
class SourceSpec:
    name: str
    description: str
    redact: Callable[[Transaction], str | None]


SOURCES: dict[str, SourceSpec] = {
    "binance": SourceSpec(
        "binance",
        "Binance transactions (deposits, withdrawals, Binance Pay, C2C/P2P), newest first. "
        "`kind` is one of deposit, withdrawal, pay, c2c. A P2P order appears as two rows "
        "sharing the same `reference`; pair them to see both legs.",
        _redact_binance,
    ),
    "bnc": SourceSpec(
        "bnc",
        "Banco Nacional de Credito (BNC) transactions, newest first. Always in VES "
        "(bolivares, shown as Bs). `kind` is in Spanish and the account number is masked. "
        "BNC has date-only precision; occurred_at 04:00Z = local midnight Caracas; ignore "
        "the time. Rows may carry `bcv_rate` (official Bs per USD) and `bcv_rate_at`.",
        redact_to_kind,
    ),
}
