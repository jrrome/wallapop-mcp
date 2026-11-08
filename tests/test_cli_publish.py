"""Publishing preferences must reach the API without silently enabling shipping."""
import pytest

from wallapop_mcp.cli import commands
from wallapop_mcp.cli.main import build_parser
from wallapop_mcp.http import Response


@pytest.mark.parametrize("flags,expected_shipping", [([], True), (["--no-shipping"], False)])
def test_publish_shipping_preference_reaches_client(tmp_path, monkeypatch, flags, expected_shipping):
    (tmp_path / "foto.jpg").write_bytes(b"jpeg")
    captured = {}

    class Client:
        def publish(self, **kwargs):
            captured.update(kwargs)
            return Response(200, {"id": "item-1"}, True, False)

    monkeypatch.setattr(commands, "authed_client", lambda: (Client(), {}))
    args = build_parser().parse_args([
        "publish", str(tmp_path), "--summary", "Test item", "--price", "300", *flags,
    ])
    assert args.func(args) == 0
    assert captured["shipping_allowed"] is expected_shipping


def test_browser_publish_rejects_no_shipping_before_starting(monkeypatch, capsys):
    monkeypatch.setattr(
        commands, "_cmd_publish_browser",
        lambda *args: pytest.fail("must not launch a wizard that could enable shipping"),
    )
    args = build_parser().parse_args([
        "publish", "photos", "--summary", "Test item", "--price", "300",
        "--no-shipping", "--mode", "browser",
    ])
    assert args.func(args) == 1
    assert "only in API mode" in capsys.readouterr().err
