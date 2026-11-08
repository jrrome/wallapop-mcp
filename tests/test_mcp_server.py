"""Tests for the Python MCP server (wallapop_mcp.mcp_server).

No network: these only check tool registration, read/write gating, session
enforcement and argument shaping.
"""
import asyncio

import pytest

import wallapop_mcp.mcp_server as mcp_server

READ_TOOLS = {
    "session_status", "search", "get_item", "get_seller", "list_categories",
    "list_inbox", "get_conversation", "unread_count", "list_purchases",
    "list_sales", "get_offer_constraints", "get_offer_status",
    "seller_items", "suggest", "list_favorites", "saved_search_hits",
    "check_saved_search",
}
WRITE_TOOLS = {
    "set_favorite", "send_message", "send_offer", "respond_offer",
    "counter_offer", "publish_item", "edit_item", "reserve_item",
    "mark_sold", "pause_item", "delete_item", "save_search",
    "remove_saved_search",
}


def _tool_names(allow_writes):
    srv = mcp_server.build_server(allow_writes)
    return {t.name for t in asyncio.run(srv.list_tools())}


def test_writes_hidden_by_default(tmp_path):
    mcp_server._init(str(tmp_path), allow_writes=False)
    reads = _tool_names(False)
    assert reads == READ_TOOLS
    assert not (reads & WRITE_TOOLS)


def test_allow_writes_exposes_mutations(tmp_path):
    mcp_server._init(str(tmp_path), allow_writes=True)
    both = _tool_names(True)
    assert both == READ_TOOLS | WRITE_TOOLS
    assert len(both) == 30


def test_account_tool_requires_a_session(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLAPOP_ACCESS_TOKEN", raising=False)
    mcp_server._init(str(tmp_path), allow_writes=True)  # empty dir -> no token
    with pytest.raises(RuntimeError, match="session"):
        mcp_server.unread_count()


@pytest.mark.parametrize("shipping_allowed", [True, False])
def test_publish_forwards_shipping_preference(tmp_path, monkeypatch, shipping_allowed):
    from wallapop_mcp.http import Response

    monkeypatch.setenv("WALLAPOP_ACCESS_TOKEN", "tok")
    mcp_server._init(str(tmp_path), allow_writes=True)
    captured = {}
    monkeypatch.setattr(
        mcp_server._c(), "publish",
        lambda **kwargs: captured.update(kwargs) or Response(200, {"id": "item-1"}, True, False),
    )
    result = mcp_server.publish_item(
        title="t", description="d", price=10.0, category_leaf_id="10311",
        condition="good", photo_paths=["x.jpg"], shipping_allowed=shipping_allowed,
    )
    assert result["ok"] is True
    assert captured["shipping_allowed"] is shipping_allowed


def test_save_search_sends_the_web_filter_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLAPOP_ACCESS_TOKEN", "tok")
    mcp_server._init(str(tmp_path), allow_writes=True)
    seen = {}
    monkeypatch.setattr(mcp_server._c(), "top_level_category", lambda cid: 24200)
    monkeypatch.setattr(mcp_server._c(), "save_search", lambda params: seen.update(params) or "uuid-1")
    out = mcp_server.save_search(
        "switch 2", category_id=24203, max_price=300, shippable=True,
        latitude=40.4, longitude=-3.7,
    )
    assert out == {"ok": True, "id": "uuid-1"}
    assert seen == {
        "keywords": "switch 2", "source": "search_box", "filters_source": "search_box",
        "order_by": "most_relevance", "category_id": 24200, "max_sale_price": 300,
        "is_shippable": "true", "latitude": 40.4, "longitude": -3.7,
    }


def test_saved_search_rejects_half_a_coordinate(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLAPOP_ACCESS_TOKEN", "tok")
    mcp_server._init(str(tmp_path), allow_writes=True)
    with pytest.raises(ValueError, match="latitude"):
        mcp_server.check_saved_search("switch", latitude=40.4)


def test_check_saved_search_reports_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLAPOP_ACCESS_TOKEN", "tok")
    mcp_server._init(str(tmp_path), allow_writes=False)
    monkeypatch.setattr(mcp_server._c(), "saved_search_exists", lambda params: None)
    assert mcp_server.check_saved_search("switch") == {"saved": False, "id": None}
