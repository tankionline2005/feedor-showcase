"""The blue badges: what came since the person last looked, each person on their own."""

from sqlalchemy import select

from app import ai
from app.db import SessionLocal
from app.models import AiAccount, Channel, ChatMessage, ChatPost, Post, Project, Topic, User, utcnow
from app.web import members as members_api

from test_members import member_login
from test_user_channels import own_ai, _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


async def _project(tg_id: int) -> tuple[int, int]:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        s.add(AiAccount(user_id=user.id, key_enc=ai.encrypt_key("sk-or-x"), connected_at=utcnow()))
        await own_ai(s, user.id)
        await s.commit()
        return project.id, user.id


async def _news(project_id: int, link: str, posts: int, topics: int, ai_posts: int) -> None:
    async with SessionLocal() as s:
        channel = await s.scalar(select(Channel).where(Channel.link == link))
        top = await s.scalar(select(Post.msg_id).where(Post.channel_id == channel.id).order_by(Post.msg_id.desc()).limit(1))
        s.add_all([Post(channel_id=channel.id, msg_id=(top or 0) + i + 1, posted_at=utcnow(), text=f"новость {i}")
                   for i in range(posts)])
        made = [Topic(project_id=project_id, title=f"Тема {i}", summary="s", ranked_at=utcnow())
                for i in range(topics)]
        s.add_all(made)
        await s.flush()
        # the AI's posts by topics («Публикации»)
        s.add_all([ChatPost(project_id=project_id, topic_id=made[0].id, ai_text="Пост", text="Пост", media=[])
                   for _ in range(ai_posts)])
        await s.commit()


async def _answer(thread_id: int) -> None:
    async with SessionLocal() as s:
        s.add(ChatMessage(thread_id=thread_id, role="assistant", content="Ответ",
                          created_at=utcnow()))
        await s.commit()


def test_badges_count_what_came_since_the_person_looked(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9941)
    assert owner.post("/api/channels", json={"links": "@uc_badges"}).json()["added"]
    run(client, _activate, "@uc_badges", 99411, 2)
    project_id, _ = run(client, _project, 9941)

    # the first look: nothing is new yet
    assert owner.get("/api/badges").json() == {"feed": 0, "topics": 0, "posts": 0, "chats": 0, "triggers": 0, "most": 99}

    created = owner.post("/api/members", json={"login": "badges.staff"}).json()
    staff, _ = member_login(app, "badges.staff", created["password"])
    assert staff.get("/api/badges").json()["feed"] == 0

    run(client, _news, project_id, "@uc_badges", 3, 2, 1)
    assert owner.get("/api/badges").json() == {"feed": 3, "topics": 2, "posts": 1, "chats": 0, "triggers": 0, "most": 99}

    # the owner looks at the feed and the topics: theirs go; the staff member's stay
    assert owner.post("/api/badges/seen", json={"kind": "feed"}).json() == {"ok": True}
    assert owner.post("/api/badges/seen", json={"kind": "topics"}).json() == {"ok": True}
    assert owner.get("/api/badges").json() == {"feed": 0, "topics": 0, "posts": 1, "chats": 0, "triggers": 0, "most": 99}
    staff_badges = staff.get("/api/badges").json()
    assert (staff_badges["feed"], staff_badges["topics"], staff_badges["posts"]) == (3, 2, 1)
    assert staff_badges["chats"] == 0  # no chat allowed: never counted

    # a chat: an answer after it was last opened is unread, opening it reads it
    chat_id = owner.post("/api/chats").json()["id"]
    owner.get(f"/api/chats/{chat_id}")
    run(client, _answer, chat_id)
    assert owner.get("/api/badges").json()["chats"] == 1
    assert [c["unread"] for c in owner.get("/api/chats").json()["items"]] == [True]
    owner.get(f"/api/chats/{chat_id}")
    assert owner.get("/api/badges").json()["chats"] == 0
    assert [c["unread"] for c in owner.get("/api/chats").json()["items"]] == [False]
