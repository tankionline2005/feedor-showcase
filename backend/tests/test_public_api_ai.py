"""The external API and what the AI made: the smart feed, «Темы», and the project channel's «Статистика»."""

from sqlalchemy import select

from app import smart_feed
from app.db import SessionLocal
from app.models import Post, Project, Topic, TopicPost, TopicUpdate, User, utcnow

from test_smart_feed import _add_posts, _link, _own_channel, model  # noqa: F401  (model is a fixture)
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def api(browser, key: str, path: str, **params):
    return browser.get(f"/api/v1{path}", params=params, headers={"X-API-Key": key})


def test_the_smart_feed_through_the_api(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9991)
    key = owner.get("/api/api-key").json()["key"]
    assert owner.post("/api/channels", json={"links": "@uc_api_smart"}).json()["added"]
    channel_id = run(client, _activate, "@uc_api_smart", 99911, 1)  # one post from before

    off = api(owner, key, "/posts", smart="true")
    assert off.status_code == 409 and off.json()["error"]["code"] == "smart_feed_off"

    run(client, _link, 9991)
    owner.put("/api/smart", json={"enabled": True, "prompt": "Only posts about AI."})
    run(client, _add_posts, channel_id, ["AI startup raised a round", "weather today"])
    run(client, smart_feed.smart_feed_step)
    late = run(client, _add_posts, channel_id, ["new AI model"])  # not read yet

    items = api(owner, key, "/posts", smart="true").json()["items"]
    assert [(p["text"], p["smart"]["decided_by"]) for p in items] == [
        ("AI startup raised a round", "ai"), ("@uc_api_smart 0", None)]
    assert items[0]["smart"] == {"decided_by": "ai", "reason": "про ИИ", "score": 90}

    # in step: only what the AI has read, and the cursor past the turned-down ones
    synced = api(owner, key, "/posts/new", smart="true").json()
    assert [p["text"] for p in synced["items"]] == ["@uc_api_smart 0", "AI startup raised a round"]
    assert synced["last_id"] == late[0] - 1 and not synced["has_more"]
    assert api(owner, key, "/posts/new", smart="true", after_id=synced["last_id"]).json()["items"] == []
    run(client, smart_feed.smart_feed_step)
    again = api(owner, key, "/posts/new", smart="true", after_id=synced["last_id"]).json()
    assert [p["text"] for p in again["items"]] == ["new AI model"]


async def _topic(tg_id: int, post_ids: list[int], hidden: bool = False) -> int:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        t = Topic(project_id=project.id, title="Новая модель ИИ", summary="Компания выпустила модель.", importance=8,
                  updated_at=utcnow(), last_post_at=utcnow(), hidden_at=utcnow() if hidden else None)
        s.add(t)
        await s.flush()
        s.add_all([TopicPost(topic_id=t.id, post_id=i) for i in post_ids])
        s.add(TopicUpdate(topic_id=t.id, at=utcnow(), text="Модель представили утром"))
        await s.commit()
        return t.id


async def _post_ids(channel_id: int) -> list[int]:
    async with SessionLocal() as s:
        return list((await s.execute(select(Post.id).where(Post.channel_id == channel_id).order_by(Post.id))).scalars())


def test_topics_and_the_channel_s_stats_through_the_api(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9992)
    key = owner.get("/api/api-key").json()["key"]
    assert owner.post("/api/channels", json={"links": "@uc_api_topics"}).json()["added"]
    channel_id = run(client, _activate, "@uc_api_topics", 99921, 2)
    posts = run(client, _post_ids, channel_id)
    shown = run(client, _topic, 9992, posts)
    hidden = run(client, _topic, 9992, posts[:1], True)

    listed = api(owner, key, "/topics").json()
    assert [t["id"] for t in listed["items"]] == [shown] and not listed["has_more"]
    t = listed["items"][0]
    assert (t["title"], t["importance"], t["sources"], t["posts_count"], t["post_ids"]) == (
        "Новая модель ИИ", 8, 1, 2, posts[::-1])  # by the time they were published (the first is the newest)
    assert t["channels"][0]["username"] == "uc_api_topics" and t["channels"][0]["is_my_channel"] is False
    assert api(owner, key, "/topics", updated_after="2100-01-01T00:00:00Z").json()["items"] == []

    whole = api(owner, key, f"/topics/{shown}").json()
    assert whole["timeline"] == [{"at": whole["timeline"][0]["at"], "text": "Модель представили утром"}]
    assert [p["id"] for p in whole["posts"]] == posts  # the latest first
    assert api(owner, key, f"/topics/{hidden}").status_code == 404
    # someone else's topic is not there
    other = sign_in(app, client, 9993)
    assert api(other, other.get("/api/api-key").json()["key"], f"/topics/{shown}").status_code == 404

    # the channel's «Статистика»: once the project has its channel
    missing = api(owner, key, "/project/stats")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "no_my_channel"
    run(client, _own_channel, 9992)
    stats = api(owner, key, "/project/stats", period="7d").json()
    assert stats["period"] == "7d" and stats["my_channel"]["title"] == "Мой канал"
    assert {"subscribers", "days", "hours", "summary", "benchmark"} <= set(stats)
    assert api(owner, key, "/project/stats", period="1y").status_code == 422
