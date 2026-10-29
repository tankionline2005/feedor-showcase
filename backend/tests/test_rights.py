"""What a staff member may do: reading always; the rest only with the switch the owner turned on."""

from sqlalchemy import select

from app import ai, smart_feed
from app.db import SessionLocal
from app.models import AiAccount, AiUsage, Channel, Member, Post, Project, SmartVerdict, Topic, User, utcnow
from app.web import members as members_api

from test_members import member_login
from test_user_channels import own_ai, _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


async def _setup(tg_id: int, link: str) -> dict:
    """The owner's AI linked, a topic, the smart feed on with one verdict."""
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        s.add(AiAccount(user_id=user.id, key_enc=ai.encrypt_key("sk-or-x"), connected_at=utcnow()))
        await own_ai(s, user.id)
        topic = Topic(project_id=project.id, title="Тема", summary="s", covered=True)
        s.add(topic)
        feed = await smart_feed.owner_feed(s, project.id)
        feed.enabled = True
        channel = await s.scalar(select(Channel).where(Channel.link == link))
        post = await s.scalar(select(Post).where(Post.channel_id == channel.id).limit(1))
        await s.flush()
        s.add(SmartVerdict(feed_id=feed.id, post_id=post.id, match=True, score=90, reason="да"))
        await s.commit()
        return {"topic": topic.id, "post": post.id, "project": project.id}


async def _spend(member_login_: str, cost: float) -> None:
    async with SessionLocal() as s:
        m = await s.scalar(select(Member).where(Member.login == member_login_))
        project = await s.get(Project, m.project_id)
        s.add(AiUsage(user_id=project.owner_id, project_id=project.id, member_id=m.id, operation="chat",
                      model="x", cost=cost))
        await s.commit()


def test_staff_need_a_switch_for_everything_but_reading(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9931_01)
    assert owner.post("/api/channels", json={"links": "@uc_rights"}).json()["added"]
    run(client, _activate, "@uc_rights", 9931011, 1)
    ids = run(client, _setup, 9931_01, "@uc_rights")
    made = owner.post("/api/members", json={"login": "rights.staff"}).json()
    member_id = made["member"]["id"]
    assert not made["member"]["ai_teach"] and made["member"]["ai_chat_limit_usd"] is None
    staff, _ = member_login(app, "rights.staff", made["password"])

    # reading: always
    assert staff.get("/api/topics").status_code == 200 and staff.get("/api/publications").status_code == 200
    settings = staff.get("/api/topics/settings").json()
    assert not settings["editable"] and not settings["can_run"] and not settings["can_teach"]
    assert settings["prompt"] is None

    # the rest: not without the switch
    topic, post = ids["topic"], ids["post"]
    assert staff.post(f"/api/topics/{topic}/hide", json={"reason": "не наше"}).status_code == 403
    assert staff.post(f"/api/topics/{topic}/not-covered").status_code == 403
    assert staff.put("/api/topics/settings", json={"enabled": True}).status_code == 403
    assert staff.put("/api/publications/settings", json={"topic_posts": True}).status_code == 403
    assert staff.get("/api/publications").json()["settings"] is None  # the owner's alone
    assert staff.post("/api/topics/run").status_code == 403
    assert staff.post(f"/api/smart/posts/{post}/correct", json={"show": False, "reason": "не то"}).status_code == 403
    assert not staff.get("/api/smart").json()["can_correct"]
    # the project's groups: every staff member sends there, no switch needed (a group not the project's: none)
    assert staff.get("/api/groups").status_code == 200
    assert staff.post(f"/api/posts/{post}/original", json={"chat_id": 1}).status_code in (404, 409)

    # switched on: allowed, and the project sees who did it
    rights = {"ai_teach": True, "ai_configure": True, "ai_chat": True, "ai_chat_limit_usd": 0.5}
    patched = owner.patch(f"/api/members/{member_id}", json=rights).json()
    assert all(patched[k] == v for k, v in rights.items())
    assert staff.get("/api/auth/me").json()["member"]["ai_teach"]
    assert staff.post(f"/api/topics/{topic}/hide", json={"reason": "не наше"}).json() == {"ok": True}
    assert staff.get("/api/topics?hidden=true").json()["items"][0]["hidden_by"] == "rights.staff"
    assert staff.post(f"/api/topics/{topic}/not-covered").json() == {"ok": True}
    assert staff.put("/api/topics/settings", json={"prompt": "Только ИИ"}).json()["prompt"] == "Только ИИ"
    assert staff.get("/api/smart").json()["can_correct"]  # the owner's feed: the project's
    assert staff.post(f"/api/smart/posts/{post}/correct", json={"show": False, "reason": "не то"}).json()
    rejected = owner.get("/api/posts", params={"smart": "rejected"}).json()["items"]
    assert rejected[0]["smart"]["mine_by"] == "rights.staff"
    assert staff.get("/api/groups").status_code == 200
    # running the AI by hand and seeing one's spending: the owner's only, no switch gives them
    assert staff.post("/api/topics/run").status_code == 403
    assert staff.put("/api/publications/settings", json={"topic_posts": True}).status_code == 403  # no switch
    assert not staff.get("/api/topics/settings").json()["can_run"]
    assert staff.get("/api/ai/mine").status_code in (404, 405)
    assert "ai_run" not in patched and "ai_see_spend" not in patched and "send_to_groups" not in patched

    # the chat limit: spent, the chat stops for them (only for them), and the owner sees what was spent
    run(client, _spend, "rights.staff", 0.6)
    chat_id = staff.post("/api/chats").json()["id"]
    refused = staff.post(f"/api/chats/{chat_id}/messages", json={"text": "привет"})
    assert refused.status_code == 409 and "лимит" in refused.json()["detail"]
    assert "лимит" in staff.get("/api/chats").json()["paused"]
    assert owner.get("/api/members").json()["items"][0]["chat_spent"] == 0.6
    # taken away: no limit of their own any more
    assert owner.patch(f"/api/members/{member_id}", json={"ai_chat_limit_usd": None}).json()["ai_chat_limit_usd"] is None
    assert staff.get("/api/chats").json()["paused"] is None
