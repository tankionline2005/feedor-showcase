import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update

from app import orphans, plans, settings_store
from app.db import SessionLocal, engine
from app.logins import confirm_login
from app.models import Channel, LoginRequest, Post, Project, ProjectChannel, User, utcnow
from app.web import auth, live, public_api
from app.worker.collector import Collector
from app.worker.pool import AccountPool


@pytest.fixture
def ctx():
    asyncio.run(engine.dispose())
    from app.web.main import app

    auth._starts.clear()
    public_api._calls.clear()
    with TestClient(app) as client:

        async def reset():
            async with SessionLocal() as s:
                await s.execute(delete(LoginRequest))
                await s.execute(delete(User))
                await s.execute(delete(Channel).where(Channel.link.like("@uc_%")))
                await settings_store.update(s, {"notify_bot_username": "km_test_bot"})

        client.portal.call(reset)
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
        yield app, client


def sign_in(app, client, tg_id: int) -> TestClient:
    browser = TestClient(app)
    code = browser.post("/api/auth/tg/start").json()["link"].rsplit("login_", 1)[1]
    client.portal.call(confirm_login, code, {"id": tg_id, "first_name": f"u{tg_id}"})
    assert browser.get("/api/auth/tg/status").json()["status"] == "ok"
    client.portal.call(give_plan, tg_id)
    return browser


async def give_plan(tg_id: int, plan: str = "pro") -> None:
    """Nobody gets a plan on sign-up: the admin or a promo code gives one."""
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        project.plan = plan
        await s.commit()


async def own_ai(s, user_id: int) -> None:
    """The owner's AI on their own OpenRouter (dev:service:plan), as every owner's was before the service's."""
    from sqlalchemy import update

    await s.execute(update(Project).where(Project.owner_id == user_id).values(plan="dev", plan_expires_at=None))


def run(client, fn, *args):
    return client.portal.call(fn, *args)


async def _channel(link: str) -> Channel | None:
    async with SessionLocal() as s:
        return await s.scalar(select(Channel).where(Channel.link == link))


async def _activate(link: str, tg_id: int, posts: int) -> int:
    """What an account joining the channel would do: make it active with a few posts."""
    async with SessionLocal() as s:
        ch = await s.scalar(select(Channel).where(Channel.link == link))
        ch.status, ch.tg_id, ch.title = "active", tg_id, link.removeprefix("@")
        for i in range(posts):
            s.add(Post(channel_id=ch.id, msg_id=i + 1, posted_at=utcnow() - timedelta(minutes=i), text=f"{link} {i}"))
        await s.commit()
        return ch.id


def test_users_share_a_channel_but_see_only_their_own(ctx):
    app, client = ctx
    a, b = sign_in(app, client, 9101), sign_in(app, client, 9102)

    added = a.post("/api/channels", json={"links": "@uc_shared, @uc_only_a"}).json()
    assert [c["username"] for c in added["added"]] == ["uc_shared", "uc_only_a"]
    shared = run(client, _activate, "@uc_shared", 91001, 3)
    only_a = run(client, _activate, "@uc_only_a", 91002, 2)

    # B adds the same channel: no second copy, and it is ready at once with its history
    res = b.post("/api/channels", json={"links": "https://t.me/UC_Shared"}).json()
    assert [(c["id"], c["status"]) for c in res["added"]] == [(shared, "active")]
    assert b.post("/api/channels", json={"links": "@uc_shared"}).json()["duplicates"] == ["@uc_shared"]

    async def copies():
        async with SessionLocal() as s:
            return await s.scalar(select(func.count()).where(func.lower(Channel.username) == "uc_shared"))

    assert run(client, copies) == 1

    # everyone sees only their channels and posts; the admin sees all of them, and who tracks what
    assert {c["id"] for c in b.get("/api/channels").json()["items"]} == {shared}
    assert {c["id"] for c in a.get("/api/channels").json()["items"]} == {shared, only_a}
    feed = b.get("/api/posts?limit=100").json()["items"]
    assert {p["channel"]["id"] for p in feed} == {shared} and len(feed) == 3
    assert b.get(f"/api/posts?channel_id={only_a}").json()["items"] == []
    admin_view = {c["id"]: c for c in client.get("/api/channels").json()["items"]}
    assert admin_view[shared]["users"] == 2 and admin_view[only_a]["users"] == 1
    assert "users" not in a.get("/api/channels").json()["items"][0]

    # neither the aggregator copy nor an account is shown to a user
    async def reposted():
        async with SessionLocal() as s:
            await s.execute(update(Post).where(Post.channel_id == shared).values(repost_url="https://t.me/c/1/5"))
            await s.commit()

    run(client, reposted)
    post = b.get("/api/posts").json()["items"][0]
    assert post["url"] == f"https://t.me/uc_shared/{post['msg_id']}" and post["reposted"] is False
    assert client.get("/api/posts?channel_id=%d" % shared).json()["items"][0]["url"] == "https://t.me/c/1/5"
    assert all(c["account_id"] is None for c in a.get("/api/channels").json()["items"])

    # the external API follows the same rule
    key = b.get("/api/api-key").json()["key"]
    api = lambda path: b.get(f"/api/v1{path}", headers={"X-API-Key": key})  # noqa: E731
    assert {p["channel"]["id"] for p in api("/posts").json()["items"]} == {shared}
    assert api(f"/channels/{only_a}").status_code == 404
    assert api("/posts?channel=uc_only_a").json()["error"]["code"] == "unknown_channel"
    only_a_post = client.get(f"/api/posts?channel_id={only_a}").json()["items"][0]["id"]
    assert api(f"/posts/{only_a_post}").status_code == 404
    stats = api("/stats").json()
    assert stats["channels"]["total"] == 1 and stats["posts_total"] == 3 and "reposted_24h" not in stats
    assert "repost_url" not in api("/posts").json()["items"][0]
    assert "repost_status" not in api(f"/posts/{feed[0]['id']}").json()

    # live updates count only the user's posts
    assert run(client, live.signature, None)[0] > run(client, live.signature, 0)[0] == 0


def test_plan_limit(ctx, monkeypatch):
    app, client = ctx
    monkeypatch.setitem(plans.PLANS, "pro", plans.Plan("pro", "Pro", 29, 2, True, 2))
    u = sign_in(app, client, 9103)
    res = u.post("/api/channels", json={"links": "@uc_l1\n@uc_l2\n@uc_l3"}).json()
    assert len(res["added"]) == 2 and res["over_limit"] == ["@uc_l3"] and res["limit"] == 2
    # the admin has no limit
    assert client.post("/api/channels", json={"links": "@uc_l3"}).json()["over_limit"] == []


def test_channel_is_left_only_when_nobody_needs_it(ctx):
    app, client = ctx
    a, b = sign_in(app, client, 9104), sign_in(app, client, 9105)
    a.post("/api/channels", json={"links": "@uc_keep\n@uc_never_joined\nt.me/+uc_private"})
    b.post("/api/channels", json={"links": "@uc_keep"})
    keep = run(client, _activate, "@uc_keep", 91004, 1)

    # one of two leaves: nothing changes
    assert a.delete(f"/api/channels/{keep}").status_code == 200
    assert a.delete(f"/api/channels/{keep}").status_code == 404  # not theirs any more
    assert run(client, _channel, "@uc_keep").orphaned_at is None

    # the last one leaves: it waits a day in case somebody wants it back
    b.delete(f"/api/channels/{keep}")
    assert run(client, _channel, "@uc_keep").orphaned_at is not None
    b.post("/api/channels", json={"links": "@uc_keep"})
    assert run(client, _channel, "@uc_keep").orphaned_at is None

    b.delete(f"/api/channels/{keep}")

    async def age(link: str, by: timedelta):
        async with SessionLocal() as s:
            ch = await s.scalar(select(Channel).where(Channel.link == link))
            ch.orphaned_at = utcnow() - by
            await s.commit()

    run(client, age, "@uc_keep", timedelta(hours=23))
    run(client, orphans.orphans_step)
    assert run(client, _channel, "@uc_keep").status == "active"
    run(client, age, "@uc_keep", timedelta(hours=25))
    run(client, orphans.orphans_step)
    assert run(client, _channel, "@uc_keep").status == "deleting"  # the worker leaves it now

    # never joined: goes at once
    never = run(client, _channel, "@uc_never_joined").id
    a.delete(f"/api/channels/{never}")
    assert run(client, _channel, "@uc_never_joined") is None

    # a private channel waits a month: getting back in may not be possible
    run(client, _activate, "t.me/+uc_private", 91005, 1)
    private = run(client, _channel, "t.me/+uc_private").id
    a.delete(f"/api/channels/{private}")
    run(client, age, "t.me/+uc_private", timedelta(days=10))
    run(client, orphans.orphans_step)
    assert run(client, _channel, "t.me/+uc_private").status == "active"

    # the admin's own channels are held and never left this way
    client.post("/api/channels", json={"links": "@uc_held"})
    run(client, _activate, "@uc_held", 91006, 0)
    assert run(client, _channel, "@uc_held").held is True


def test_users_see_channel_problems_of_the_channel_only(ctx):
    app, client = ctx
    u = sign_in(app, client, 9106)
    u.post("/api/channels", json={"links": "@uc_typo\n@uc_ours\n@uc_paused"})

    async def statuses():
        async with SessionLocal() as s:
            await s.execute(update(Channel).where(Channel.link == "@uc_typo").values(status="error", status_detail="Канал не найден"))
            await s.execute(update(Channel).where(Channel.link == "@uc_ours").values(
                status="error", status_detail="Ошибка: Cannot send requests while disconnected"))
            await s.execute(update(Channel).where(Channel.link == "@uc_paused").values(status="paused"))
            await s.commit()

    run(client, statuses)
    seen = {c["link"]: (c["status"], c["status_detail"]) for c in u.get("/api/channels").json()["items"]}
    assert seen == {
        "@uc_typo": ("error", "Канал не найден"),
        "@uc_ours": ("pending", None),  # our problem: it just looks like it is still connecting
        "@uc_paused": ("pending", None),
    }
    admin = {c["link"]: c["status"] for c in client.get("/api/channels").json()["items"]}
    assert admin["@uc_ours"] == "error" and admin["@uc_paused"] == "paused"

    # adding a failed channel again retries it
    typo = run(client, _channel, "@uc_typo").id
    u.delete(f"/api/channels/{typo}")
    u.post("/api/channels", json={"links": "@uc_typo"})
    assert run(client, _channel, "@uc_typo").status == "pending"


def test_a_channel_added_by_two_links_is_merged(ctx):
    app, client = ctx
    a, b = sign_in(app, client, 9107), sign_in(app, client, 9108)
    a.post("/api/channels", json={"links": "t.me/+uc_link_one"})
    b.post("/api/channels", json={"links": "t.me/+uc_link_two"})
    first = run(client, _activate, "t.me/+uc_link_one", 91008, 1)
    second = run(client, _channel, "t.me/+uc_link_two").id

    # an account joined by the second link and found the very same channel
    collector = Collector(AccountPool())
    run(client, collector._merge, second, first)
    assert run(client, _channel, "t.me/+uc_link_two") is None
    assert [c["id"] for c in b.get("/api/channels").json()["items"]] == [first]

    async def subscribers():
        async with SessionLocal() as s:
            return sorted((await s.execute(
                select(ProjectChannel.project_id).where(ProjectChannel.channel_id == first)
            )).scalars())

    assert len(run(client, subscribers)) == 2


def test_channels_are_removed_by_a_list(ctx):
    app, client = ctx
    a, b = sign_in(app, client, 9121), sign_in(app, client, 9122)
    a.post("/api/channels", json={"links": "@uc_list_one\n@uc_list_two\nt.me/+uc_list_private\n@uc_list_stays"})
    b.post("/api/channels", json={"links": "@uc_list_one"})
    one = run(client, _activate, "@uc_list_one", 91201, 1)
    links = "https://t.me/uc_list_one, @UC_LIST_TWO\nhttps://t.me/+uc_list_private\n@uc_not_mine\nhello!"

    # first only what would go: nothing goes
    dry = a.post("/api/channels/remove", json={"links": links, "dry": True}).json()
    assert dry == {"removed": 3, "not_found": ["@uc_not_mine"], "invalid": ["hello!"], "own": []}
    assert len(a.get("/api/channels").json()["items"]) == 4

    assert a.post("/api/channels/remove", json={"links": links}).json()["removed"] == 3
    assert [c["username"] for c in a.get("/api/channels").json()["items"]] == ["uc_list_stays"]
    # the other project keeps its channel, and the shared one is not left
    assert [c["id"] for c in b.get("/api/channels").json()["items"]] == [one]
    assert run(client, _channel, "@uc_list_one").orphaned_at is None
    assert run(client, _channel, "@uc_list_two") is None  # never joined: gone at once
    # the admin removes channels one by one only (from the whole system)
    assert client.post("/api/channels/remove", json={"links": "@uc_list_stays"}).status_code == 400
