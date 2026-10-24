import os
import re
from ipaddress import ip_address
from typing import Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_TOKEN = re.compile(r"^[\x21-\x7e]+$")


def is_loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def safe_url(url: str) -> str:
    """scheme://host:port only; never userinfo, path or query."""
    parsed = urlparse(url)
    host = f"[{parsed.hostname}]" if ":" in (parsed.hostname or "") else parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BANKER_HUB_",
        env_file=os.environ.get("BANKER_HUB_ENV_FILE", ".env"),
        extra="ignore",
    )

    url: str = "http://127.0.0.1:8080"
    token: SecretStr
    timeout: float = Field(15.0, gt=0, le=300, allow_inf_nan=False)
    include_detail: bool = False
    redact_counterparty: bool = True
    allow_insecure: bool = False

    @field_validator("token")
    @classmethod
    def _check_token(cls, v: SecretStr) -> SecretStr:
        token = v.get_secret_value().strip()
        if not _TOKEN.match(token):
            raise ValueError("must be non-empty printable ASCII without spaces")
        return SecretStr(token)

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        if re.search(r"\s", v):
            raise ValueError("must not contain spaces")
        try:
            parsed = urlparse(v)
            parsed.port  # noqa: B018 - raises on a bad port
        except ValueError:
            raise ValueError("is not a valid URL") from None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("must be http(s)://host[:port]")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("must not contain userinfo, query or fragment")
        return v

    @model_validator(mode="after")
    def _check_transport(self) -> Self:
        parsed = urlparse(self.url)
        if (
            parsed.scheme == "http"
            and not is_loopback(parsed.hostname or "")
            and not (self.allow_insecure)
        ):
            raise ValueError(
                "BANKER_HUB_URL uses plain http to a non-loopback host; "
                "use https or set BANKER_HUB_ALLOW_INSECURE=true"
            )
        return self
