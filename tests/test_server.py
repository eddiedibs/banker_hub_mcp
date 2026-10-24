import pytest
from pydantic import ValidationError

from banker_hub_mcp.config import Settings, safe_url
from banker_hub_mcp.server import main


def build(**kw: object) -> Settings:
    base: dict[str, object] = {"token": "tok"}
    return Settings(_env_file=None, **(base | kw))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TOKEN", "URL", "TIMEOUT", "ALLOW_INSECURE", "INCLUDE_DETAIL"):
        monkeypatch.delenv(f"BANKER_HUB_{name}", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


def test_main_without_token_writes_nothing_to_stdout(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "BANKER_HUB_TOKEN" in caplog.text


def test_main_bad_url_message_has_no_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("BANKER_HUB_TOKEN", "tok")
    monkeypatch.setenv("BANKER_HUB_URL", "http://user:hunter2@example.com")
    with pytest.raises(SystemExit):
        main()
    captured = capsys.readouterr()
    assert "hunter2" not in caplog.text and "hunter2" not in captured.err
    assert captured.out == "" and "BANKER_HUB_URL" in caplog.text


def test_main_oserror_is_clean(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise PermissionError("/secret/path")

    monkeypatch.setattr("banker_hub_mcp.server.Settings", boom)
    with pytest.raises(SystemExit):
        main()
    assert "/secret/path" not in caplog.text and "PermissionError" in caplog.text


@pytest.mark.parametrize("token", ["", "   ", "has space", "tab\there", "café"])
def test_invalid_tokens(token: str) -> None:
    with pytest.raises(ValidationError):
        build(token=token)


def test_token_is_stripped() -> None:
    assert build(token="  abc \n").token.get_secret_value() == "abc"


@pytest.mark.parametrize("timeout", [0, -1, 301, float("inf"), float("nan")])
def test_invalid_timeout(timeout: float) -> None:
    with pytest.raises(ValidationError):
        build(timeout=timeout)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1",
        "127.0.0.1:8080",
        "http://",
        "http://u:p@127.0.0.1",
        "http://127.0.0.1?x=1",
        "http://127.0.0.1#f",
        "http://127.0.0.1 /x",
        "http://127.0.0.1:99999",
        "http://192.168.1.5:8080",
        "http://hub.example.com",
    ],
)
def test_invalid_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        build(url=url)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8080", "http://localhost", "http://[::1]:8080", "https://hub.example.com/"],
)
def test_valid_urls(url: str) -> None:
    assert build(url=url).url == url


def test_insecure_override() -> None:
    assert build(url="http://192.168.1.5:8080", allow_insecure=True).allow_insecure


def test_safe_url_drops_path() -> None:
    assert safe_url("https://hub.example.com:8443/a/b") == "https://hub.example.com:8443"
    assert safe_url("http://[::1]:8080") == "http://[::1]:8080"
