"""«Создать об этом пост для моего канала»: each person words the request their own way in each project, or keeps
the default; and the AI's default criteria, changed, reach whoever kept the old ones."""

from sqlalchemy import select

from app import db, smart_feed, topics
from app.db import SessionLocal, _new_default_prompts, engine
from app.models import SmartFeed, TopicState
from app.web import chat_api
from app.web import members as members_api

from test_members import member_login
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def test_each_person_has_their_own_template_in_each_project(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9971)
    first = owner.get("/api/auth/me").json()["project"]["id"]
    default = {"template": chat_api.POST_PROMPT, "default": chat_api.POST_PROMPT, "custom": False}
    assert owner.get("/api/chats/post-prompt").json() == default

    mine = "Напиши короткий пост про «{тема}», без эмодзи. Тема №{номер}."
    saved = owner.put("/api/chats/post-prompt", json={"template": f"  {mine}\r\n"}).json()
    assert (saved["template"], saved["custom"]) == (mine, True)
    assert owner.get("/api/chats/post-prompt").json()["template"] == mine

    # a staff member's is their own
    created = owner.post("/api/members", json={"login": "prompt.staff"}).json()
    staff, _ = member_login(app, "prompt.staff", created["password"])
    assert staff.get("/api/chats/post-prompt").json() == default

    # another project: its own template; back in the first one, the first one's
    owner.post("/api/projects")
    assert owner.get("/api/chats/post-prompt").json() == default
    owner.post(f"/api/projects/{first}/open")
    assert owner.get("/api/chats/post-prompt").json()["template"] == mine

    # back to the default: an empty one, or the default itself
    assert owner.put("/api/chats/post-prompt", json={"template": None}).json() == default
    owner.put("/api/chats/post-prompt", json={"template": mine})
    assert owner.put("/api/chats/post-prompt", json={"template": chat_api.POST_PROMPT}).json() == default
    assert owner.put("/api/chats/post-prompt", json={"template": "x" * 4001}).status_code == 422


async def _states(project_id: int, prompts: tuple[str, str] | None = None) -> tuple[str, str]:
    async with SessionLocal() as s:
        feed = await s.scalar(select(SmartFeed).where(SmartFeed.project_id == project_id, SmartFeed.member_id.is_(None)))
        topic = await s.get(TopicState, project_id)
        if prompts is not None:
            feed.prompt, topic.prompt = prompts
            await s.commit()
        return feed.prompt, topic.prompt


async def _upgrade() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_new_default_prompts)


def test_the_old_default_criteria_become_the_new_ones(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9972)
    project = owner.get("/api/auth/me").json()["project"]["id"]
    # the settings are made on first look
    owner.get("/api/smart"), owner.get("/api/topics/settings")
    old = db.OLD_DEFAULT_PROMPTS
    run(client, _states, project, (old["smart_feeds"], "Мой собственный запрос"))
    run(client, _upgrade)
    # the old default, kept as it was, is the new one; the person's own stays
    assert run(client, _states, project) == (smart_feed.DEFAULT_PROMPT, "Мой собственный запрос")
