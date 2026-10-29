import json
from datetime import timedelta

from sqlalchemy import delete, select, update

from app import settings_store
from app.db import SessionLocal, engine, init_db
from app.models import Account, Proxy, utcnow
from app.worker import proxy_feed

DD = "dd104462821249bd7ac519130220c25d09"  # random padding: supported
EE = "ee54ce330e4690cc297d2b031ff3f288b06d742e616b656e61692e636c69636b"  # fake-TLS: not supported


def post(post_id: int, *servers: tuple[str, str], channel: str = "ProxyMTProto", tg_scheme: bool = False) -> str:
    """One message of a channel's web page, buttons as t.me/s renders them."""
    base = "tg://proxy?" if tg_scheme else "https://t.me/proxy?"
    buttons = "".join(
        f'<a class="tgme_widget_message_inline_button url_button" '
        f'href="{base}server={host}&amp;port=443&amp;secret={secret}">Connect</a>'
        for host, secret in servers
    )
    return f'<div class="tgme_widget_message" data-post="{channel}/{post_id}"><div>Proxy</div>{buttons}</div>'


def page(*posts: str) -> str:
    return "<html><body>" + "".join(posts) + "</body></html>"


def test_parse_feed_reads_buttons_per_post():
    html = page(post(10, ("a.example", DD), ("b.example", EE)), post(11), post(12, ("c.example", DD), ("c.example", DD)))
    assert proxy_feed.parse_feed(html, "ProxyMTProto") == [
        (10, [f"https://t.me/proxy?server=a.example&port=443&secret={DD}",
              f"https://t.me/proxy?server=b.example&port=443&secret={EE}"]),
        (11, []),
        (12, [f"https://t.me/proxy?server=c.example&port=443&secret={DD}"]),  # duplicates collapse
    ]
    # tg://proxy links count too
    assert proxy_feed.parse_feed(page(post(5, ("t.example", DD), channel="TProxyRU", tg_scheme=True)), "TProxyRU") == [
        (5, [f"tg://proxy?server=t.example&port=443&secret={DD}"])
    ]


class FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status != 200:
            raise proxy_feed.httpx.HTTPError(f"{self.status}")


def serve(monkeypatch, pages: dict[str, str]):
    """channel -> page html; a channel without a page answers like a closed one (redirect)."""

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            channel = url.removeprefix("https://t.me/s/")
            return FakeResponse(pages[channel]) if channel in pages else FakeResponse("", 302)

    monkeypatch.setattr(proxy_feed.httpx, "AsyncClient", Client)


async def _reset(last_post: str = ""):
    await engine.dispose()
    await init_db()
    async with SessionLocal() as s:
        await s.execute(update(Account).values(proxy_id=None))
        await s.execute(delete(Proxy))
        await settings_store.update(s, {"proxy_feed_enabled": True, "proxy_feed_last_post": last_post})


async def _hosts() -> list[str]:
    async with SessionLocal() as s:
        return sorted((await s.execute(select(Proxy.host))).scalars())


async def _sources() -> dict[str, str]:
    async with SessionLocal() as s:
        return dict((await s.execute(select(Proxy.host, Proxy.source))).all())


async def test_feed_adds_supported_proxies_from_new_posts_only(monkeypatch):
    await _reset()
    old_posts = [post(i, (f"old{i}.example", DD)) for i in range(1, 6)]

    # first run: only the latest few posts, fake-TLS skipped
    serve(monkeypatch, {"ProxyMTProto": page(*old_posts, post(6, ("p6.example", DD), ("tls.example", EE)))})
    await proxy_feed.feed_step()
    assert await _hosts() == ["old3.example", "old4.example", "old5.example", "p6.example"]
    async with SessionLocal() as s:
        auto = (await s.execute(select(Proxy))).scalars().all()
        assert {p.source for p in auto} == {"@ProxyMTProto"} and {p.kind for p in auto} == {"mtproto"}
        assert json.loads((await settings_store.get_all(s))["proxy_feed_last_post"]) == {"ProxyMTProto": 6}

    # next run: only posts after 6; a proxy already in the list is not added twice
    serve(monkeypatch, {"ProxyMTProto": page(*old_posts, post(6, ("p6.example", DD)), post(7, ("p7.example", DD), ("p6.example", DD)))})
    await proxy_feed.feed_step()
    assert await _hosts() == ["old3.example", "old4.example", "old5.example", "p6.example", "p7.example"]

    # switched off: nothing is fetched or added
    async with SessionLocal() as s:
        await settings_store.update(s, {"proxy_feed_enabled": False})
    serve(monkeypatch, {"ProxyMTProto": page(post(8, ("p8.example", DD)))})
    await proxy_feed.feed_step()
    assert "p8.example" not in await _hosts()


async def test_every_channel_keeps_its_own_place(monkeypatch):
    # stored before there were several channels: a bare post id of @ProxyMTProto
    await _reset(last_post="6")
    serve(monkeypatch, {
        "ProxyMTProto": page(post(6, ("seen.example", DD)), post(7, ("m7.example", DD))),
        "TProxyRU": page(post(40, ("t40.example", DD), channel="TProxyRU", tg_scheme=True)),
        # ProxyFree_Ru answers like a closed page: skipped, the others still work
    })
    await proxy_feed.feed_step()
    assert await _sources() == {"m7.example": "@ProxyMTProto", "t40.example": "@TProxyRU"}
    async with SessionLocal() as s:
        stored = json.loads((await settings_store.get_all(s))["proxy_feed_last_post"])
    assert stored == {"ProxyMTProto": 7, "TProxyRU": 40}


async def test_dead_auto_proxies_go_after_a_day_unless_used(monkeypatch):
    await _reset()
    old = utcnow() - timedelta(hours=30)
    async with SessionLocal() as s:
        acc = Account(name="feed", session="", status="active")
        s.add(acc)
        s.add_all([
            Proxy(kind="mtproto", host="dead-old", port=443, key="k1", status="dead", source="@ProxyMTProto", created_at=old),
            Proxy(kind="mtproto", host="dead-new", port=443, key="k2", status="dead", source="@ProxyMTProto"),
            Proxy(kind="mtproto", host="alive-old", port=443, key="k3", status="ok", source="@ProxyMTProto", created_at=old),
            Proxy(kind="socks5", host="mine-dead", port=1080, key="k4", status="dead", created_at=old),  # added by hand
            Proxy(kind="mtproto", host="used-dead", port=443, key="k5", status="dead", source="@ProxyMTProto", created_at=old),
        ])
        await s.flush()
        acc.proxy_id = (await s.execute(select(Proxy.id).where(Proxy.host == "used-dead"))).scalar_one()
        await s.commit()

    assert await proxy_feed.remove_dead_auto() == 1
    assert await _hosts() == ["alive-old", "dead-new", "mine-dead", "used-dead"]

    async with SessionLocal() as s:
        await s.execute(update(Account).where(Account.id == acc.id).values(proxy_id=None, enabled=False))
        await s.commit()
