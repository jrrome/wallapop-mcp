"""CLI command handlers: login/session, search/deals/watch, offers, messaging."""

from __future__ import annotations

import argparse
import datetime
import getpass
import json
import os
import re
import signal
import time
from typing import Any, Optional

from ..client import Wallapop, persist_browser_session, refresh_web_session
from ..config import (
    CITIES,
    CRED_PATH,
    DEFAULT_GEO,
    ORDER_ALIASES,
    SESSION_PATH,
    WALLAPOP_COOKIE_KEYS,
    clear_credentials,
    clear_session,
    load_contacted,
    load_credentials,
    load_session,
    parse_cookie_string,
    read_firefox_cookies,
    save_contacted,
    save_credentials,
)
from ..http import ApiError, Response
from ..ids import resolve_internal_id
from ..models import Deal, Item, deep_find, first, offer_state_summary, parse_offer_events, parse_sdui_transactions, rank_deals
from .output import (
    announce,
    clock,
    emit_csv,
    emit_json,
    eprint,
    fmt_thread_message,
    msg_type_label,
    offer_label,
    offer_payload_summary,
    offer_tag,
    paint,
    print_deals,
    print_items,
    print_offer_events,
    render_template,
    truncate,
)
from ..session import SessionError, build_client
from ..tokens import token_expiry

def authed_client(required: bool = True) -> tuple[Wallapop, dict]:
    """CLI wrapper around :func:`wallapop_mcp.session.build_client`.

    The library helper raises ``SessionError``; the CLI turns that into a
    friendly ``SystemExit(1)``.
    """
    try:
        return build_client(required=required)
    except SessionError as exc:
        eprint(str(exc))
        raise SystemExit(1) from None


def resolve_geo(args: argparse.Namespace) -> tuple[Optional[float], Optional[float]]:
    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None:
            raise ApiError("--lat and --lon must be provided together")
        return args.lat, args.lon
    if args.city:
        key = args.city.strip().lower()
        if key not in CITIES:
            raise ApiError(f"unknown city '{args.city}'. available: {', '.join(sorted(CITIES))}")
        return CITIES[key]
    return DEFAULT_GEO


def fetch_items(client: Wallapop, args: argparse.Namespace, keyword: str) -> list[Item]:
    lat, lon = resolve_geo(args)
    if lat is None and args.city is None and args.lat is None and not args.quiet:
        eprint("note: no location given, searching all of Spain (--city or --lat/--lon to narrow)")
    order = ORDER_ALIASES.get(args.order, "most_relevance")
    if order == "price_low_to_high" and not args.quiet:
        eprint("note: Wallapop hides prices on 'price_asc' sort; results may show 0.00")
    items = client.search(
        keyword,
        category_id=args.category,
        subcategory_ids=int(args.subcat) if getattr(args, "subcat", None) else None,
        order_by=order,
        latitude=lat,
        longitude=lon,
        pages=args.pages,
        delay=args.delay,
        condition=getattr(args, "condition", None),
        min_price=args.min_price,
        max_price=args.max_price,
        ship_only=getattr(args, "ship_only", None),
    )
    items = [it for it in items if not it.reserved and not it.sold]
    return items


def deliver(client: Wallapop, it: Item, offer_pct: Optional[float], message: Optional[str], dry_run: bool) -> None:
    amount = None
    if offer_pct is not None:
        amount = max(1.0, round(it.price * offer_pct, 2))
    text = render_template(message, it, amount) if message else None
    snippet = truncate(it.title, 40)
    if offer_pct is not None:
        label = f"offer {amount:.2f}€ on {snippet!r} ({it.id})"
        if dry_run:
            announce(label, None)
            if text:
                print(f"      message: {text}")
        else:
            announce(label, client.send_offer(it.id, amount, message=text))
    elif text:
        label = f"message seller of {it.id} re {snippet!r}"
        if dry_run:
            announce(label, None)
            print(f"      text: {text}")
        else:
            announce(label, client.send_message(it.id, text))


def maybe_deliver_all(client: Wallapop, args: argparse.Namespace, deals: list[Deal]) -> None:
    if args.offer_pct is None and not args.msg:
        return
    contacted = set(load_contacted())
    for d in deals:
        if d.item.id in contacted:
            continue
        deliver(client, d.item, args.offer_pct, args.msg, args.dry_run)
        if not args.dry_run:
            contacted.add(d.item.id)
            save_contacted(contacted)


def _valid_session() -> bool:
    creds = load_credentials()
    session = load_session()
    token = creds.get("access_token") or session.get("accessToken") or ""
    if not token:
        return False
    exp = token_expiry(token)
    return exp is None or exp > datetime.datetime.now()


def _login_via_wizard(timeout_s: int = 300) -> int:
    from ..weblogin import wizard_login  # noqa: PLC0415 (optional dep)

    try:
        session, token = wizard_login(timeout_s=timeout_s)
    except RuntimeError as exc:
        eprint(f"{exc}")
        return 1
    save_credentials(
        {"email": "browser-session", "access_token": token, "refresh_token": "", "user": {}}
    )
    print(f"session captured from browser ({', '.join(sorted(session))})")
    print(f"session stored in {SESSION_PATH}")
    return 0


def _login_from_firefox() -> int:
    cookies = read_firefox_cookies()
    if not cookies:
        eprint("no wallapop cookies found in any Firefox profile; log in at es.wallapop.com first")
        return 1
    session = {k: cookies[k] for k in WALLAPOP_COOKIE_KEYS if k in cookies}
    token = refresh_web_session(session)
    if not token:
        eprint("session cookie found but could not get an access token; log in again at es.wallapop.com")
        return 1
    client = Wallapop(
        token=token,
        email="browser-session",
        persist_fn=lambda c: persist_browser_session(c.web_cookies or {}, c.token),
    )
    client.web_cookies = session
    save_credentials({"email": "browser-session", "access_token": token, "refresh_token": "", "user": {}})
    persist_browser_session(session, token)
    print(f"imported Firefox session ({', '.join(sorted(session))})")
    print(f"session stored in {SESSION_PATH}")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    if getattr(args, "wizard", False):
        return _login_via_wizard(args.timeout)

    if getattr(args, "browser", None) == "firefox":
        return _login_from_firefox()

    if getattr(args, "cookies", None):
        cookies_source = args.cookies
    elif getattr(args, "cookies_file", None):
        cfp = args.cookies_file
        try:
            with open(cfp, encoding="utf-8") as fh:
                cookies_source = " ".join(
                    line.strip() for line in fh.read().splitlines() if not line.strip().startswith("#")
                )
        except OSError as exc:
            eprint(f"could not read {cfp}: {exc}")
            return 1
    elif os.path.exists("cookies.txt") and args.email:
        try:
            with open("cookies.txt", encoding="utf-8") as fh:
                cookies_source = " ".join(
                    line.strip() for line in fh.read().splitlines() if not line.strip().startswith("#")
                )
        except OSError as exc:
            eprint(f"could not read cookies.txt: {exc}")
            return 1
    else:
        cookies_source = ""
    if cookies_source:
        parsed = parse_cookie_string(cookies_source)
        session = {k: parsed[k] for k in WALLAPOP_COOKIE_KEYS if k in parsed}
        missing = ", ".join(k for k in WALLAPOP_COOKIE_KEYS if k not in session)
        if missing:
            eprint(f"missing cookies: {missing}. needed: {', '.join(WALLAPOP_COOKIE_KEYS)}")
            return 1
        token = refresh_web_session(session)
        if not token:
            eprint("could not get an access token from those cookies; are they fresh?")
            return 1
        persist_browser_session(session, token)
        save_credentials({"email": "browser-session", "access_token": token, "refresh_token": "", "user": {}})
        print("session imported from cookies")
        print(f"session stored in {SESSION_PATH}")
        return 0

    if not args.email:
        if not getattr(args, "force", False) and _valid_session():
            print("already logged in. run: wallapop login --force  (to switch accounts)")
            return 0
        if read_firefox_cookies():
            return _login_from_firefox()
        try:
            import playwright  # noqa: F401, PLC0415 (optional dep)
        except ImportError:
            pass
        else:
            return _login_via_wizard(args.timeout)
        eprint("no account found. use: wallapop login <email>  |  pip install 'wallapop-mcp[browser]' for the Google login wizard")
        return 1
    password = args.password
    if not password:
        password = getpass.getpass("password: ")
    client = Wallapop(persist_fn=lambda c: save_credentials({
        "email": c.email,
        "access_token": c.token,
        "refresh_token": c.refresh_token,
        "user": c.user,
    }))
    client.login(args.email, password)
    name = client.user.get("micro_name") or client.user.get("name") or args.email
    print(f"logged in as {name} ({args.email})")
    print(f"credentials stored in {CRED_PATH}")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    clear_credentials()
    clear_session()
    print("credentials cleared")
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    creds = load_credentials()
    session = load_session()
    source = "browser" if session.get("__Secure-next-auth.session-token") else ("email" if creds.get("access_token") else "none")
    token = creds.get("access_token") or session.get("accessToken") or ""
    if not token:
        print(f"source:  none")
        print("not logged in")
        return 0
    exp = token_expiry(token)
    name = (creds.get("user") or {}).get("micro_name") or ""
    print(f"source:  {source}")
    print(f"name:    {name or 'browser session'}")
    print(f"token:   stored at {SESSION_PATH if source == 'browser' else CRED_PATH}")
    print(f"expires: {exp.strftime('%Y-%m-%d %H:%M:%S') if exp else 'unknown'}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    item_hash = resolve_internal_id(args.item)
    client, _ = authed_client()
    it = client.item(item_hash, authed=True)
    data = it.to_dict()
    seller_data = seller_summary(client, it.user_id)
    if args.json:
        data["seller"] = seller_data.get("data", {})
        data["seller_name"] = seller_data.get("name", "")
        data["seller_rating"] = seller_data.get("rating")
        data["seller_reviews"] = seller_data.get("reviews")
        emit_json(data)
        return 0
    print(paint.bold(str(it.title)))
    print(f"price:    {it.price:.2f} {it.currency}".rstrip("0").rstrip(".") if it.currency else f"price:    {it.price}")
    who = it.user_name or it.user_id or "?"
    extra = seller_data.get("text")
    if extra:
        who = f"{who}  {extra}"
    print(f"seller:   {who}")
    city = it.city or "?"
    if it.latitude or it.longitude:
        city += f"  ({it.latitude:.4f}, {it.longitude:.4f})"
    print(f"city:     {city}")
    print(f"category: {it.category_name or it.category or '?'}")
    extras = []
    if it.brand:
        extras.append(f"marca: {it.brand}")
    if it.genre:
        extras.append(f"genre: {it.genre}")
    if it.condition or it.condition_text:
        extras.append(f"estado: {it.condition_text or it.condition}")
    if extras:
        print("  " + "  ·  ".join(extras))
    shipped = "yes" if it.shippable else "no"
    print(f"shipping: {shipped}  reserved: {'yes' if it.reserved else 'no'}  sold: {'yes' if it.sold else 'no'}")
    print(f"stats:    {it.views} vistas · {it.favorites} favs {'(te gusta)' if it.favorited else ''}")
    print(f"url:      {it.url}")
    if it.description:
        print()
        print(paint.dim("description:"))
        print(it.description.strip())
    return 0


def seller_summary(client, user_id: str) -> dict:
    """Return {text, name, rating, reviews, data} about a seller (tolerant to failures)."""
    if not user_id:
        return {"text": "", "name": "", "rating": None, "reviews": None, "data": {}}
    try:
        profile = client.seller(user_id)
    except Exception:
        profile = {}
    micro_name = str((profile or {}).get("micro_name") or "").strip()
    if micro_name.lower() == "undefined":
        micro_name = ""
    try:
        stats = client.seller_stats(user_id)
    except Exception:
        stats = {}
    avg = None
    count = None
    rv = stats.get("rating_average")
    if isinstance(rv, (int, float)):
        avg = round(float(rv), 1)
    for c in (stats.get("counters") or []):
        if isinstance(c, dict) and c.get("type") == "reviews":
            count = c.get("value")
            break
    parts = []
    if micro_name:
        parts.append(f"({micro_name})")
    if avg is not None:
        label = f"★ {avg}" + (f" ({count})" if count is not None else "")
        parts.append(label)
    return {
        "text": " ".join(parts),
        "name": micro_name,
        "rating": avg,
        "reviews": count,
        "data": {"profile": profile, "stats": stats},
    }


def cmd_favorite(args: argparse.Namespace) -> int:
    item_hash = resolve_internal_id(args.item)
    client, _ = authed_client()
    r = client.favorite(item_hash, not args.remove)
    if r.ok:
        action = "added to" if not args.remove else "removed from"
        print(f"[ok] {action} favoritos")
    else:
        eprint(f"error: {r.error_reason or r.status}")
        return 1
    return 0


def cmd_seller(args: argparse.Namespace) -> int:
    client, _ = authed_client()
    user_id = args.user
    try:
        profile = client.seller(user_id)
    except Exception as e:
        print(paint.error(f"error: could not fetch the seller: {e}"))
        return 1
    try:
        stats = client.seller_stats(user_id)
    except Exception:
        stats = {}
    try:
        reviews = client.seller_reviews(user_id)
    except Exception:
        reviews = []
    extra = {}
    if getattr(args, "extra", False):
        try:
            extra = client.seller_extra_info(user_id)
        except Exception:
            extra = {}
    name = str((profile or {}).get("micro_name") or user_id).strip()
    if name.lower() == "undefined":
        name = user_id
    if args.json:
        emit_json({"profile": profile, "stats": stats, "reviews": reviews, "extra_info": extra})
        return 0
    avg = stats.get("rating_average")
    avg_lbl = f"★ {avg}" if isinstance(avg, (int, float)) else "★ ?"
    reviews_lbl = "0"
    for c in (stats.get("counters") or []):
        if isinstance(c, dict) and c.get("type") == "reviews":
            reviews_lbl = str(c.get("value", "0"))
            break
    print(paint.bold(f"{name} ({user_id})"))
    print(f"rating: {avg_lbl} · {reviews_lbl} reviews")
    print(f"total reviews: {reviews_lbl}")
    if isinstance(stats.get("sells"), (int, float)):
        print(f"sales: {stats['sells']}")
    if isinstance(stats.get("shipping"), (int, float)):
        print(f"shipments: {stats['shipping']}")
    if isinstance(stats.get("deliveries_on_time_in_days"), (int, float)):
        print(f"avg. delivery: {stats['deliveries_on_time_in_days']} days")
    if extra:
        rate = extra.get("response_rate")
        level = (extra.get("validations") or {}).get("level")
        rating = extra.get("rating_average")
        if rate:
            labels = {
                "less_than_one_hour": "< 1 h",
                "less_than_a_day": "< 1 day",
                "less_than_a_week": "< 1 week",
                "unknown": "unknown",
            }
            rate_lbl = labels.get(rate, str(rate))
            print(f"responde: {rate_lbl}")
        if isinstance(rating, (int, float)):
            print(f"extra rating: {rating} ★")
        if level:
            print(f"verificado: {level}")
    if reviews:
        print()
        print(paint.dim("reviews:"))
        for r in reviews:
            rev = r.get("review") or {}
            score = rev.get("rating_over_five")
            item = (r.get("item") or {}).get("title", "")
            print(f"  {'★' * int(score) if isinstance(score, (int, float)) else '?'}  {item}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    client = Wallapop()
    keyword = " ".join(args.keyword)
    items = fetch_items(client, args, keyword)
    if args.json:
        emit_json([it.to_dict() for it in items])
    elif args.csv:
        emit_csv(items)
    else:
        print(paint.bold(f"{len(items)} listings for {keyword!r}"))
        print_items(items)
    return 0


def cmd_deals(args: argparse.Namespace) -> int:
    client = Wallapop()
    keyword = " ".join(args.keyword)
    items = fetch_items(client, args, keyword)
    deals, median = rank_deals(items, args.min_discount, args.max_price)
    deals = deals[: args.top]
    if args.json:
        emit_json({"keyword": keyword, "median": median, "count": len(deals), "deals": [d.to_dict() for d in deals]})
    elif args.csv:
        emit_csv(deals)
    else:
        print(
            paint.bold(
                f"{len(deals)} deals for {keyword!r} "
                f"(median {median:.2f}€, {args.min_discount * 100:.0f}%+ below market)"
            )
        )
        print_deals(deals)
    maybe_deliver_all(client, args, deals)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    sending = args.offer_pct is not None or bool(args.msg)
    client, _ = authed_client(required=sending and not args.dry_run)
    keyword = " ".join(args.keyword)

    def stop(sig, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    try:
        while True:
            items = fetch_items(client, args, keyword)
            deals, median = rank_deals(items, args.min_discount, args.max_price)
            contacted = set(load_contacted())
            fresh = [d for d in deals if d.item.id not in contacted]
            print(
                f"[{clock()}] scanned {len(items)} listings, median {median:.2f}€, "
                f"{len(deals)} deals, {len(fresh)} new"
            )
            for d in fresh[: args.top]:
                print_deals([d])
                deliver(client, d.item, args.offer_pct, args.msg, args.dry_run)
                if not args.dry_run:
                    contacted.add(d.item.id)
                    save_contacted(contacted)
            if args.once:
                break
            time.sleep(max(args.interval, 5))
    except KeyboardInterrupt:
        print("\nwatch stopped")
    return 0


def resolve_conversation_thread(client, what: str) -> Optional[Response]:
    """Fetch a thread from a conversation hash, or via an item hash."""
    resp = client.conversation_thread(what)
    if resp.ok:
        return resp
    resp2 = client.ensure_conversation(what)
    if resp2.ok:
        conv_id = first(deep_find(resp2.data, "conversation_id"))
        if conv_id:
            return client.conversation_thread(conv_id)
    return None


def run_offer_status(args: argparse.Namespace) -> int:
    client, _ = authed_client()
    what = (args.offer_id or args.what or "").lower()

    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", what):
        conv_hash = find_conversation_for_offer(client, what)
        if conv_hash is None:
            eprint(f"offer {what}: no conversation in your inbox holds this offer id")
            return 1
        resp = client.conversation_thread(conv_hash)
        data = resp.data if resp.ok else {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        effective = what
        return render_thread_offer_status(data, effective)

    if what in ("", "status"):
        resp = client.inbox()
        if not resp.ok:
            eprint(f"inbox failed ({resp.status}): {resp.error_reason or str(resp.data)[:200]}")
            return 1
        convs = ((resp.data or {}).get("conversations")) or []

        def newest_ts(c: dict) -> int:
            msgs = (((c.get("messages") or {}).get("messages")) or [])
            ts = [m.get("timestamp") or 0 for m in msgs]
            return max(ts) if ts else 0

        convs = sorted(convs, key=newest_ts, reverse=True)
        limit = getattr(args, "last", None)
        if limit:
            convs = convs[: max(0, limit)]
        printed_header = False
        for c in convs:
            item = c.get("item") or {}
            title = str(item.get("title") or c.get("hash") or "?")
            price = item.get("price")
            amount = price.get("amount") if isinstance(price, dict) else ""
            cur = (price or {}).get("currency") if isinstance(price, dict) else ""
            who = ((c.get("with_user") or {}) or {}).get("name") or "?"
            istatus = item.get("status") or ""
            msgs = (((c.get("messages") or {}).get("messages")) or [])
            events = parse_offer_events(msgs)
            if events:
                state = offer_state_summary(events)
                kind = state.kind if state else ""
                label = offer_label(kind) if kind else "?"
                oid = state.offer_id or "" if state else ""
                amt = state.amount if state else None
                offer = f"  offer={oid}" if oid else ""
                pr = f"  actual {amt:.2f}€" if amt is not None else ""
                tag = offer_tag(kind)
            else:
                label = paint.dim("no offers")
                offer = ""
                pr = ""
                tag = ""
            st = f" [{istatus}]" if istatus else ""
            if not printed_header:
                print(paint.dim("  asking = listing price · \"actual\" = amount of the latest offer in play"))
                printed_header = True
            print(f"  {tag:<13} {paint.bold(title[:40]):<40}  {amount} {cur}{st}  ·  {who:<12}  {label}{pr}{offer}")
            print(paint.dim(f"      conv {c.get('hash')}  ->  offer status {c.get('hash')}"))
        print(paint.dim(f"{len(convs)} conversations"))
        return 0

    resp = resolve_conversation_thread(client, what)
    if resp is None:
        eprint(f"could not resolve {what!r} to a conversation or item")
        return 1
    data = resp.data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    return render_thread_offer_status(data, what)


def find_conversation_for_offer(client, offer_id: str) -> Optional[str]:
    """Locate the conversation whose thread mentions the given offer id.

    Cheap pass over the inbox first (its per-conversation messages usually
    carry the latest offer event), then a full-thread pass as fallback.
    """
    resp = client.inbox()
    if not resp.ok:
        return None
    convs = ((resp.data or {}).get("conversations")) or []
    for c in convs:
        msgs = (((c.get("messages") or {}).get("messages")) or [])
        if any(ev.offer_id == offer_id for ev in parse_offer_events(msgs)):
            return c.get("hash")
    for c in convs:
        t = client.conversation_thread(c.get("hash"))
        msgs = (((t.data or {}).get("messages") or {}).get("messages")) or []
        if any(ev.offer_id == offer_id for ev in parse_offer_events(msgs)):
            return c.get("hash")
    return None


def render_thread_offer_status(data: Any, what: str) -> int:
    """Print the offer ledger + current state for a conversation thread."""
    if not isinstance(data, dict):
        eprint(f"unexpected conversation payload for {what!r}: {str(data)[:200]}")
        return 1
    item = data.get("item") or {}
    with_user = data.get("with_user") or {}
    sale_status = (data.get("sale") or {}).get("status") or item.get("status") or ""
    price = item.get("price")
    amount = price.get("amount") if isinstance(price, dict) else item.get("price")
    cur = ((price or {}).get("currency")) if isinstance(price, dict) else ""
    msgs = (((data.get("messages") or {}).get("messages")) or [])
    msgs.sort(key=lambda m: m.get("timestamp") or 0)
    events = parse_offer_events(msgs)
    print(paint.bold(f"{data.get('hash') or what}  ·  {with_user.get('name') or '?'}  ·  {item.get('title') or '?'}"))
    print(f"   asking {amount} {cur}  ·  sale {sale_status}")
    if not events:
        print(paint.dim("   no offer activity in this conversation"))
        return 0
    print_offer_events(events)
    state = offer_state_summary(events)
    if state:
        oid = f"  offer={state.offer_id}" if state.offer_id else ""
        amt = f"  actual {state.amount:.2f}€" if state.amount is not None else ""
        print(f"   => {offer_label(state.kind)}{amt}{oid}")
        if state.actionable:
            print(paint.dim("      you can: offer accept <offer_id> | offer decline <offer_id> | offer counter <offer_id> --price N"))
            print(paint.dim("      (accepting gives you 24h to buy at the accepted price)"))
    return 0


def cmd_offer(args: argparse.Namespace) -> int:
    if args.what is None:
        raise ApiError("provide an item id/url/slug, or an action: accept|decline|counter|status")
    if args.what == "status":
        return run_offer_status(args)
    if args.what in ("accept", "decline", "counter"):
        if not args.offer_id:
            raise ApiError(f"offer {args.what} requires an <offer_id>")
        client, _ = authed_client()
        if args.what == "counter":
            if args.price is None:
                raise ApiError("offer counter requires --price")
            if args.pct is not None:
                raise ApiError("offer counter uses --price, not --pct")
            label = f"counter-offer {args.price:.2f}€ on offer {args.offer_id}"
            if args.dry_run:
                announce(label, None)
                return 0
            announce(label, client.counter_offer(args.offer_id, args.price))
            return 0
        label = f"{args.what} offer {args.offer_id}"
        if args.dry_run:
            announce(label, None)
            return 0
        announce(label, client.offer_response(args.offer_id, args.what))
        return 0
    if args.price is None and args.pct is None:
        raise ApiError("provide --price or --pct")
    if args.price is not None and args.pct is not None:
        raise ApiError("provide only one of --price or --pct")
    item_id = resolve_internal_id(args.what)
    client, _ = authed_client()
    info = client.make_offer_info(item_id)
    asking = 0.0
    max_pct = 30.0
    if info.ok:
        asking = float((((info.data or {}).get("item") or {}).get("price") or {}).get("amount") or 0.0)
        max_pct = float(((info.data or {}).get("restriction") or {}).get("max_offer_percentage") or 30.0)
        remain = ((info.data or {}).get("restriction") or {}).get("remaining_offers_per_day")
    if args.price is not None:
        amount = args.price
    else:
        if asking <= 0:
            asking = Wallapop().item(item_id).price
        amount = max(1.0, round(asking * args.pct, 2))
    min_offer = 0.0 if asking <= 0 else round(asking * (1 - max_pct / 100), 2)
    if min_offer > 0 and amount < min_offer:
        print(paint.yellow(f"warning: {amount:.2f}€ is below the {max_pct:.0f}% discount limit (min {min_offer:.2f}€); the offer may be rejected"))
    if "remain" in locals() and remain is not None:
        print(paint.dim(f"offers left today: {remain}"))
    label = f"offer {amount:.2f}€ on item {item_id}"
    if args.dry_run:
        announce(label, None)
        return 0
    announce(label, client.send_offer(item_id, amount, message=args.msg))
    return 0


def cmd_message(args: argparse.Namespace) -> int:
    conv = getattr(args, "conversation", None)
    if conv:
        label = f"reply in conversation {conv}"
        if args.dry_run:
            announce(label, None)
            print(f"      text: {args.text}")
            return 0
        client, _ = authed_client()
        report: dict[str, Any] = {}
        resp = client.reply_to_conversation(conv, args.text, report=report)
        announce(label, resp)
        if resp.ok:
            note = " (same text already sent, skipped)" if report.get("skipped") else ""
            print(f"      delivered via {report.get('path') or '?'}{note}")
            return 0
        data = resp.data if isinstance(resp.data, dict) else {}
        if data.get("delivery") == "unknown":
            eprint(paint.yellow("delivery is unknown: the message may or may not have reached the buyer"))
            eprint("      inspect the conversation before sending it again")
        return 1
    if not args.item_id:
        eprint("message: provide an item_id (to message a seller) or --conversation (to reply)")
        return 1
    item_hash = resolve_internal_id(args.item_id)
    label = f"message seller of item {item_hash}"
    if args.dry_run:
        announce(label, None)
        print(f"      text: {args.text}")
        return 0
    client, _ = authed_client()
    started = time.monotonic()
    report: dict[str, Any] = {}
    browser_fallback = bool(getattr(args, "browser_fallback", False))
    resp = client.send_message(
        item_hash, args.text, report=report, allow_browser_fallback=browser_fallback
    )
    elapsed = time.monotonic() - started
    announce(label, resp)
    if resp.ok:
        whom = (resp.data or {}).get("with_user") or {}
        name = whom.get("name") or "?"
        note = " (same text already sent, skipped)" if report.get("skipped") else ""
        path = report.get("path") or "?"
        print(f"      delivered to {name} via {path} in {elapsed:.1f}s total {note}")
        if report.get("browser_s"):
            print(f"      browser send itself took {report['browser_s']:.1f}s")
        return 0
    data = resp.data if isinstance(resp.data, dict) else {}
    if data.get("delivery") == "unknown":
        eprint(paint.yellow("delivery is unknown: the message may or may not have reached the seller"))
        conv = data.get("conversation_id")
        if conv:
            eprint(f"      conversation: {conv}")
        if data.get("outcome") == "not_sent":
            eprint("      the message was not sent: it is safe to send it again")
        else:
            eprint("      inspect the conversation before sending the message again")
    return 1


def cmd_replies(args: argparse.Namespace) -> int:
    client, _ = authed_client()

    if args.json:
        if args.conversation:
            resp = client.conversation_thread(args.conversation)
            if not resp.ok:
                eprint(f"conversation {args.conversation} failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
                return 1
            print(json.dumps(resp.data, ensure_ascii=False, indent=2))
            return 0
        resp = client.inbox()
        if not resp.ok:
            eprint(f"inbox failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
            return 1
        print(json.dumps(resp.data, ensure_ascii=False, indent=2))
        return 0

    unread = client.unread_count()

    resp = client.inbox()
    if not resp.ok:
        eprint(f"inbox failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        return 1
    convs = (resp.data or {}).get("conversations") or []
    if not convs:
        print("empty inbox")
        return 0
    if args.conversation:
        resp = client.conversation_thread(args.conversation)
        if not resp.ok:
            eprint(f"conversation {args.conversation} failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
            return 1
        data = resp.data or {}
        with_user = (data.get("with_user") or {}).get("name") or ""
        item = data.get("item") or {}
        title = item.get("title") or ""
        sale = (data.get("sale") or {}).get("status") or item.get("status") or ""
        if sale:
            sale = f"  ·  sale {sale}"
        print(f"conversation {args.conversation} with {with_user}  ·  {title}{sale}")
        msgs = (((data.get("messages") or {}).get("messages")) or [])
        msgs.sort(key=lambda m: m.get("timestamp") or 0)
        for m in msgs:
            print(fmt_thread_message(m))
        print(paint.dim(f"{len(msgs)} messages total"))
        return 0

    n_unread = 0
    if unread.ok and isinstance(unread.data, dict) and (unread.data.get("unread_counter") or 0):
        n_unread = unread.data.get("unread_counter") or 0
    print(paint.bold(f"inbox · {len(convs)} conversations · {n_unread} unread"))
    for c in convs:
        item = c.get("item") or {}
        title = str(item.get("title") or "")[:44]
        price = item.get("price")
        amount = price.get("amount") if isinstance(price, dict) else ""
        cur = (price.get("currency") or "EUR") if isinstance(price, dict) else ""
        status = item.get("status") or ""
        if status:
            status = f" [{status}]"
        who = (c.get("with_user") or {}) or {}
        name = str(who.get("name") or "")
        slug = who.get("slug") or ""
        rr = who.get("response_rate") or ""
        if rr:
            rr = f" rr={rr}"
        msgs = ((c.get("messages") or {}).get("messages")) or []
        msgs.sort(key=lambda m: m.get("timestamp") or 0)
        last = msgs[-1] if msgs else {}
        if last:
            last_from = "->" if last.get("from_self") else "<-"
            stamp = last.get("timestamp")
            lt = datetime.datetime.fromtimestamp(stamp / 1000).strftime("%m-%d %H:%M") if stamp else ""
            text = str(last.get("text") or "")[:80] or offer_payload_summary(last.get("payload"))[:80]
            lastline = f"  {paint.dim('last')} {last_from} {lt}{msg_type_label(last)}  {text}"
        else:
            lastline = ""
        unread_c = c.get("unread_messages", 0)
        un = paint.yellow(f"· {unread_c} unread") if unread_c else ""
        ch = c.get("channel") or ""
        if c.get("map_enabled"):
            ch = f" map=on"
        print(f"{paint.cyan(c['hash'])}  {name:<24} {slug}{rr}")
        items_n = c.get("items_count") or 1
        print(f"      {title}{f' x{items_n}' if items_n > 1 else ''} price {amount or '?'}{cur}{status}{un}")
        if lastline:
            print(lastline)
    return 0


def _print_transactions(kind: str, tab: str, items: list[dict]) -> None:
    print(paint.bold(f"{kind} · {tab}"))
    if not items:
        print("   (nothing here)")
        return
    for t in items:
        price = t.get("price") or ""
        title = t.get("title") or "?"
        head = f"{price}  {title}" if price else title
        print(head.strip())
        detail = t.get("detail") or ""
        url = t.get("url") or ""
        lines = []
        if detail:
            lines.append(detail)
        for d in t.get("details") or []:
            if d.get("title"):
                lines.append(f"{d['title']}: {d['description']}")
            else:
                lines.append(d["description"])
        if url:
            lines.append(url)
        for line in lines:
            print(paint.dim(f"     {line}"))
        print()


def cmd_purchases(args: argparse.Namespace) -> int:
    tab = "finished" if args.finished else "ongoing"
    client, _ = authed_client()
    resp = client.purchases(tab)
    if not resp.ok:
        eprint(f"purchases failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        return 1
    items = parse_sdui_transactions(resp.data)
    if args.json:
        emit_json(items)
        return 0
    _print_transactions("purchases", "ongoing" if tab == "ongoing" else "finished", items)
    return 0


def cmd_selling(args: argparse.Namespace) -> int:
    tab = "finished" if args.finished else "ongoing"
    client, _ = authed_client()
    resp = client.selling(tab)
    if not resp.ok:
        eprint(f"selling failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        return 1
    items = parse_sdui_transactions(resp.data)
    if args.json:
        emit_json(items)
        return 0
    _print_transactions("selling", "ongoing" if tab == "ongoing" else "finished", items)
    return 0


def cmd_reserve(args: argparse.Namespace) -> int:
    item_id = resolve_internal_id(args.item)
    client, _ = authed_client()
    r = client.reserve(item_id, not args.remove)
    announce("reserved" if not args.remove else "unreserved", r)
    return 0 if r.ok else 1


def cmd_sold(args: argparse.Namespace) -> int:
    item_id = resolve_internal_id(args.item)
    client, _ = authed_client()
    r = client.mark_sold(item_id, not args.undo)
    announce("marked as sold" if not args.undo else "unmarked as sold", r)
    return 0 if r.ok else 1


def cmd_edit(args: argparse.Namespace) -> int:
    item_id = resolve_internal_id(args.item)
    client, _ = authed_client()
    r = client.edit_item(
        item_id,
        price=args.price,
        title=args.title,
        description=args.description,
        condition=args.condition,
        brand=args.brand,
        model=args.model,
        color=args.color,
        max_weight_kg=args.max_weight_kg,
    )
    announce("edited", r)
    if not r.ok:
        return 1
    if args.json and r.data:
        emit_json(r.data)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    item_id = resolve_internal_id(args.item)
    if not args.yes:
        ans = input(f"permanently delete {item_id}? (y/N): ").strip().lower()
        if ans not in ("y", "yes"):
            eprint("cancelled")
            return 1
    client, _ = authed_client()
    r = client.delete_item(item_id)
    announce("deleted (DELETE /items/{id})", r)
    return 0 if r.ok else 1


def cmd_publish(args: argparse.Namespace) -> int:
    """Publish an item: pure API by default, or the real web wizard with --browser."""
    if args.mode == "browser":
        if not getattr(args, "shipping_allowed", True):
            eprint("--no-shipping is supported only in API mode; omit --mode browser")
            return 1
        # the price keeps its cents: 19.95 is published as 19.95
        return _cmd_publish_browser(args, args.price)
    return _cmd_publish_api(args, args.price)


def _cmd_publish_browser(args: argparse.Namespace, price: float) -> int:
    from ..publish import publish_item  # noqa: PLC0415 (optional playwright dep)

    print(paint.bold(f"publishing {args.photos_dir} — {args.summary} @ {price:g} EUR ({args.condition})"))
    capture = getattr(args, "capture_body", None)
    try:
        result = publish_item(
            photos_dir=args.photos_dir,
            summary=args.summary,
            price=price,
            condition=args.condition,
            weight_tier=args.weight_tier,
            max_photos=args.max_photos,
            headless=args.headless,
            capture_body_path=capture,
        )
    except Exception as exc:  # noqa: BLE001
        eprint(paint.red(f"publish failed: {exc}"))
        return 1
    url = result["url"]
    item_id = result["item_id"]
    if item_id:
        print(paint.green(f"published item {item_id}"))
    print(paint.bold(f"final page: {url}"))
    return 0 if "/published" in url else 1


def _pick_photos(photos_dir: str, max_n: int = 10) -> list[str]:
    exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    files = sorted(
        os.path.join(photos_dir, f)
        for f in os.listdir(photos_dir)
        if f.lower().endswith(exts) and not f.startswith(".")
    )
    return files[:max_n]


def _cmd_publish_api(args: argparse.Namespace, price: float) -> int:
    photos = _pick_photos(args.photos_dir, max_n=10)
    if not photos:
        eprint(f"no photo found in {args.photos_dir}")
        return 1
    client, _ = authed_client()
    print(paint.bold(f"publishing {len(photos)} photo(s) from {args.photos_dir} — {args.summary} @ {price} EUR ({args.condition})"))
    resp = client.publish(
        photo_paths=photos,
        title=args.summary,
        price=price,
        condition=args.condition,
        description=args.description,
        brand=args.brand,
        model=args.model,
        color=args.color,
        max_weight_kg=args.max_weight_kg,
        shipping_allowed=getattr(args, "shipping_allowed", True),
    )
    announce("published via API", resp)
    data = resp.data if isinstance(resp.data, dict) else {}
    item_id = data.get("id") or data.get("item_id") or ""
    if item_id:
        print(paint.green(f"item {item_id}"))
    if getattr(resp, "partial", False):
        eprint(paint.yellow(
            f"partial publish: {data.get('photos_completed')} of {data.get('photos_total')} photos uploaded"
        ))
        if data.get("retry"):
            eprint(f"      retry: {data['retry']}")
        return 1
    if data.get("outcome") == "unknown":
        eprint(paint.yellow(
            "publish outcome unknown: the listing may or may not have been created; "
            "check your listings before publishing it again"
        ))
        return 1
    if resp.ok:
        return 0
    return 1


def saved_search_params(client: Wallapop, args: argparse.Namespace, keyword: str) -> dict:
    """Build the search filter params a saved search is stored with.

    Mirrors what ``client.search`` sends to ``/search/components``, but as a
    plain dict (no API call) so it can be posted to
    ``/searchalerts/savedsearch``.
    """
    params = {
        "keywords": keyword,
        "source": "search_box",
        "filters_source": "search_box",
        "order_by": ORDER_ALIASES.get(args.order, "most_relevance"),
    }
    if getattr(args, "category", None):
        params["category_id"] = client.top_level_category(args.category)
    if getattr(args, "subcat", None):
        params["subcategory_ids"] = int(args.subcat)
    if getattr(args, "condition", None):
        params["condition"] = args.condition
    if getattr(args, "min_price", None) is not None:
        params["min_sale_price"] = args.min_price
    if getattr(args, "max_price", None) is not None:
        params["max_sale_price"] = args.max_price
    if getattr(args, "ship_only", None):
        params["is_shippable"] = "true"
    lat, lon = resolve_geo(args)
    if lat is not None and lon is not None:
        params["latitude"] = lat
        params["longitude"] = lon
    return params


def cmd_alert(args: argparse.Namespace) -> int:
    """Manage saved searches (search alerts): the sniper's alert list."""
    action = getattr(args, "action", "") or ""
    if action in ("save", "check"):
        keyword = " ".join(args.keyword or [])
        if not keyword:
            eprint("provide a keyword: wallapop alert save 'nintendo switch 2' --max-price 300")
            return 1
        client, _ = authed_client()
        params = saved_search_params(client, args, keyword)
        if action == "save":
            search_id = client.save_search(params)
            print(f"[ok] search saved (id {search_id})")
            print(f"     wallapop alert remove --id {search_id}   (to delete it)")
            return 0
        search_id = client.saved_search_exists(params)
        if search_id is None:
            print(f"not saved: {keyword!r}")
            return 0
        print(f"saved (id {search_id}): {keyword!r}")
        return 0
    client, _ = authed_client()
    if action == "hits":
        hits = client.saved_search_hits()
        print(f"saved-search hits (new listings): {hits}")
        return 0
    if action == "remove":
        search_id = getattr(args, "search_id", None)
        if not search_id:
            eprint("provide the id: wallapop alert remove --id <id>")
            return 1
        r = client.remove_saved_search(str(search_id))
        announce("search {search_id} removed".format(search_id=search_id), r)
        return 0 if r.ok else 1
    eprint(f"unknown alert action: {action} (use save, check, remove or hits)")
    return 1


def cmd_favorites(args: argparse.Namespace) -> int:
    """List the items you favorited (GET /api/v3/favorite-lists)."""
    client, _ = authed_client()
    items = client.favorite_lists(pages=args.pages)
    if args.json:
        emit_json([it.to_dict() for it in items])
    else:
        print(paint.bold(f"{len(items)} favorited items"))
        print_items(items)
    return 0


def cmd_seller_items(args: argparse.Namespace) -> int:
    """List every published item of a user (GET /users/{id}/items)."""
    client = Wallapop()
    items = client.seller_items(args.user, pages=args.pages, max_items=args.max)
    if args.json:
        emit_json([it.to_dict() for it in items])
    else:
        print(paint.bold(f"{len(items)} items from seller {args.user}"))
        print_items(items)
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    """Autocomplete suggestions for a search keyword (GET /suggesters/nonlocated)."""
    client = Wallapop()
    keyword = " ".join(args.keyword)
    suggestions = client.suggest(keyword, limit=args.top)
    if args.json:
        emit_json(suggestions)
        return 0
    if not suggestions:
        print(f"no suggestions for {keyword!r}")
        return 0
    print(paint.bold(f"suggestions for {keyword!r}:"))
    for idx, s in enumerate(suggestions, 1):
        subtitle = f"  ({s['subtitle']})" if s.get("subtitle") else ""
        print(f"  {idx:>2}. {s['text']}{subtitle}")
    return 0


def cmd_inactivate(args: argparse.Namespace) -> int:
    """Pause one of your published items (leaves the marketplace, keeps it in your catalog)."""
    item_id = resolve_internal_id(args.item)
    client, _ = authed_client()
    r = client.inactivate_item(item_id)
    announce("paused (PUT /items/{id}/inactivate)", r)
    return 0 if r.ok else 1
