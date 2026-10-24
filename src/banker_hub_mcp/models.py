import re
import unicodedata
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

MAX_TEXT = 200
_URL = re.compile(r"\S*://\S*")

# format "f" avoids exponent notation such as 1E-8
PlainDecimal = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str)]


def clean_text(value: str | None, limit: int = MAX_TEXT) -> str | None:
    """Drop control/format (zero-width, bidi) characters and cap the length."""
    if value is None:
        return None
    out = []
    for ch in value:
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        out.append(" " if cat in {"Cc", "Zl", "Zp"} else ch)
    return "".join(out).strip()[:limit]


class Transaction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    source: str
    external_id: str
    account: str
    kind: str
    direction: str
    amount: PlainDecimal
    asset: str
    status: str | None = None
    reference: str | None = None
    description: str | None = None
    detail: str | None = None
    occurred_at: datetime
    created_at: datetime | None = None
    bcv_rate: PlainDecimal | None = None
    bcv_rate_at: datetime | None = None

    @field_validator("kind", "reference", "description")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        return clean_text(v)


class Page(BaseModel):
    items: list[Transaction] = Field(default_factory=list)
    next_cursor: str | None = None


class SummaryTotal(BaseModel):
    asset: str
    currency_label: str
    credit: PlainDecimal
    debit: PlainDecimal
    net: PlainDecimal
    count: int


class SyncState(BaseModel):
    source: str
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    rows_upserted: int = 0

    @field_validator("last_error")
    @classmethod
    def _clean_error(cls, v: str | None) -> str | None:
        return clean_text(_URL.sub("[url]", v)) if v else v


class Provider(BaseModel):
    name: str
    sync_state: SyncState | None = None


class SummaryResponse(BaseModel):
    totals: list[SummaryTotal] | None = None


class ProvidersResponse(BaseModel):
    providers: list[Provider] | None = None
