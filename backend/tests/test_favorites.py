"""«Избранное»: a person's own starred posts, kept past the retention period (web/posts.py)."""

from datetime import timedelta

from app.db import SessionLocal
from app.models import Post, utcnow
from app.web import members as members_api
from app.worker import maintenance

from test_members import member_login
from test_topics import _post
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


async def _channel_id(link: str) -> int:
    from sqlalchemy import select

    from app.models import Channel

    async with SessionLocal() as s:
        return await s.scalar(select(Channel.id).where(Channel.link == link))


async def _age(post_id: int, days: int) -> None:
    async with SessionLocal() as s:
        (await s.get(Post, post_id)).posted_at = utcnow() - timedelta(days=days)
        await s.commit()


async def _exists(post_id: int) -> bool:
    async with SessionLocal() as s:
        return await s.get(Post, post_id) is not None


def test_a_person_stars_posts_to_come_back_to(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9451)
    assert owner.post("/api/channels", json={"links": "@uc_fav"}).json()["added"]
    run(client, _activate, "@uc_fav", 94511, 0)
    channel = run(client, _channel_id, "@uc_fav")
    first = run(client, _post, channel, 1, "Важное")
    second = run(client, _post, channel, 2, "Пригодится")

    feed = {p["id"]: p for p in owner.get("/api/posts").json()["items"]}
    assert feed[first]["favorite"] is False
    assert owner.put(f"/api/posts/{second}/favorite").json() == {"favorite": True}
    assert owner.put(f"/api/posts/{second}/favorite").json() == {"favorite": True}  # once
    assert [p["id"] for p in owner.get("/api/posts", params={"favorites": True}).json()["items"]] == [second]
    assert owner.get(f"/api/posts/{second}").json()["favorite"] is True
    assert owner.put("/api/posts/999999/favorite").status_code == 404

    # a staff member has their own
    password = owner.post("/api/members", json={"login": "fav.anna"}).json()["password"]
    staff, _ = member_login(app, "fav.anna", password)
    assert staff.get("/api/posts", params={"favorites": True}).json()["items"] == []
    staff.put(f"/api/posts/{first}/favorite")
    assert [p["id"] for p in staff.get("/api/posts", params={"favorites": True}).json()["items"]] == [first]
    assert [p["id"] for p in owner.get("/api/posts", params={"favorites": True}).json()["items"]] == [second]

    # a starred post outlives the retention period; the others go
    run(client, _age, first, 400)
    run(client, _age, second, 400)
    staff.delete(f"/api/posts/{first}/favorite")
    run(client, maintenance.retention_step)
    assert not run(client, _exists, first) and run(client, _exists, second)
    assert owner.delete(f"/api/posts/{second}/favorite").json() == {"favorite": False}
    assert owner.get("/api/posts", params={"favorites": True}).json()["items"] == []
