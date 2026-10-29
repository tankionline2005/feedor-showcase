"""«Триггер-слова»: the words found in their forms or as written, a feed per trigger, the bot telling."""

import pytest
from sqlalchemy import select

from app import settings_store, triggers
from app.db import SessionLocal
from app.models import Channel, Post, Project, User, utcnow
from app.web import members as members_api

from test_members import member_login
from test_user_channels import give_plan, _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def found(words: list[str], mode: str, text: str) -> list[str]:
    return triggers.find(triggers.compile_words(words, mode), text)


def test_words_are_found_in_their_forms_or_as_written():
    # the forms of a word, not other words made of it
    assert found(["взрыв"], "forms", "Ночью прогремели взрывы. Звук взрыва слышали все") == ["взрывы", "взрыва"]
    assert found(["взрыв"], "forms", "Взрывной рост подписчиков") == []
    # as written: the very word, in any case
    assert found(["взрыв"], "exact", "ВЗРЫВ на заводе, потом взрывы") == ["ВЗРЫВ"]
    # abbreviations and words with digits are taken as they are
    assert found(["БПЛА"], "forms", "Сбито три БПЛА над городом") == ["БПЛА"]
    assert found(["Ту-22"], "forms", "Вылет Ту-22 и Ту-95") == ["Ту-22"]
    # a phrase: its words one after another, each in any form; ё is е
    assert found(["сбит беспилотник"], "forms", "ПВО сбили беспилотники над морем") == ["сбили беспилотники"]
    assert found(["сбит беспилотник"], "forms", "сбили, но не беспилотник") == []
    assert found(["ёлка"], "forms", "Новогодние елки на площади") == ["елки"]
    # several words: any of them
    assert found(["БПЛА", "дрон", "шахед"], "forms", "Атака дронов и шахедов") == ["дронов", "шахедов"]


async def _setup(tg_id: int, link: str) -> int:
    async with SessionLocal() as s:
        await settings_store.update(s, {"notify_bot_token": "123:test"})
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        return (await s.scalar(select(Project).where(Project.owner_id == user.id))).id


async def _post(link: str, msg_id: int, text: str, ocr: str | None = None, ocr_status: str | None = None) -> int:
    async with SessionLocal() as s:
        ch = await s.scalar(select(Channel).where(Channel.link == link))
        p = Post(channel_id=ch.id, msg_id=msg_id, posted_at=utcnow(), text=text, repost_status="failed",
                 extracted_text_from_media=ocr, ocr_status=ocr_status)
        s.add(p)
        await s.commit()
        return p.id


async def _ocr_done(post_id: int, text: str) -> None:
    async with SessionLocal() as s:
        p = await s.get(Post, post_id)
        p.ocr_status, p.extracted_text_from_media = "done", text
        await s.commit()


@pytest.fixture
def bot(monkeypatch):
    sent: list[dict] = []

    async def fake(token, method, **params):
        sent.append({"method": method, **params})
        return {}

    monkeypatch.setattr(triggers, "bot_call", fake)
    return sent


def test_a_trigger_has_its_feed_and_the_bot_tells_of_new_posts(ctx, bot):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9951)
    for link, tg in (("@uc_trig_a", 99511), ("@uc_trig_b", 99512)):
        assert owner.post("/api/channels", json={"links": link}).json()["added"]
        run(client, _activate, link, tg, 0)
    run(client, _setup, 9951, "@uc_trig_a")
    run(client, triggers.triggers_step)  # the worker begins from now
    old = run(client, _post, "@uc_trig_a", 1, "Вчера был взрыв на складе")

    # made: its feed at once, from the posts already there; nothing new, nothing told
    made = owner.post("/api/triggers", json={"words": ["взрыв", " взрыв ", "ВЗРЫВ", "детонация"], "notify": True})
    trig = next(t for t in made.json()["items"] if t["id"] == made.json()["id"])
    assert trig["name"] == "взрыв" and trig["words"] == ["взрыв", "детонация"]  # the same word once
    assert (trig["total"], trig["new"]) == (1, 0)
    feed = owner.get("/api/posts", params={"trigger": trig["id"]}).json()["items"]
    assert [(p["id"], p["trigger_found"]) for p in feed] == [(old, ["взрыв"])]

    # a new post: in the feed, on the badge, and told by the bot: the prefix, then the post
    new = run(client, _post, "@uc_trig_a", 2, "Сообщают о взрывах в порту")
    run(client, _post, "@uc_trig_a", 3, "Погода хорошая")
    run(client, triggers.triggers_step)
    assert [p["id"] for p in owner.get("/api/posts", params={"trigger": trig["id"]}).json()["items"]] == [new, old]
    assert owner.get("/api/badges").json()["triggers"] == 1
    assert len(bot) == 1 and "Триггер-слово «взрыв»" in bot[0]["text"] and "«взрывах»" in bot[0]["text"]
    assert "Сообщают о взрывах в порту" in bot[0]["text"]  # no copy to forward: its text
    run(client, triggers.triggers_step)
    assert len(bot) == 1  # once

    # the word only on its picture: found once the picture is read
    pic = run(client, _post, "@uc_trig_a", 4, "Смотрите фото", None, "pending")
    run(client, triggers.triggers_step)
    assert pic not in [p["id"] for p in owner.get("/api/posts", params={"trigger": trig["id"]}).json()["items"]]
    run(client, _ocr_done, pic, "ДЕТОНАЦИЯ БОЕПРИПАСОВ")
    run(client, triggers.triggers_step)
    assert pic in [p["id"] for p in owner.get("/api/posts", params={"trigger": trig["id"]}).json()["items"]]

    # looked at: the badge goes
    assert owner.post(f"/api/triggers/{trig['id']}/seen").json() == {"ok": True}
    assert owner.get("/api/badges").json()["triggers"] == 0

    # only one channel: the other one's posts are not its; the feed is made anew
    channels = owner.get("/api/triggers/channels").json()["items"]
    only_b = next(c["id"] for c in channels if c["title"] == "uc_trig_b")
    body = {"words": ["взрыв"], "channels_mode": "only", "channel_ids": [only_b], "notify": False}
    owner.put(f"/api/triggers/{trig['id']}", json=body)
    assert owner.get("/api/posts", params={"trigger": trig["id"]}).json()["items"] == []
    assert owner.put(f"/api/triggers/{trig['id']}", json={**body, "channel_ids": []}).status_code == 422
    assert owner.post("/api/triggers", json={"words": ["  "]}).status_code == 422

    # a staff member's triggers are their own
    created = owner.post("/api/members", json={"login": "trig.staff"}).json()
    staff, _ = member_login(app, "trig.staff", created["password"])
    assert staff.get("/api/triggers").json()["items"] == []
    assert staff.get("/api/posts", params={"trigger": trig["id"]}).status_code == 404
    assert staff.post("/api/triggers", json={"words": ["БПЛА"], "notify_chat_id": 1}).status_code == 404  # not a group of the project

    assert owner.delete(f"/api/triggers/{trig['id']}").json()["items"] == []


def test_the_plan_limits_the_project_s_triggers(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9952)
    run(client, give_plan, 9952, "starter")  # 10 for the project, the staff's count too
    assert owner.post("/api/channels", json={"links": "@uc_trig_limit"}).json()["added"]
    run(client, _activate, "@uc_trig_limit", 99521, 0)
    for i in range(9):
        assert owner.post("/api/triggers", json={"words": [f"слово{i}"]}).status_code == 200
    created = owner.post("/api/members", json={"login": "trig.limit"}).json()
    staff, _ = member_login(app, "trig.limit", created["password"])
    assert staff.post("/api/triggers", json={"words": ["десятое"]}).status_code == 200
    state = staff.get("/api/triggers").json()
    assert (state["used"], state["limit"]) == (10, 10)
    refused = owner.post("/api/triggers", json={"words": ["одиннадцатое"]})
    assert refused.status_code == 409 and "не больше 10" in refused.json()["detail"]
    assert owner.get("/api/auth/me").json()["plan"]["triggers"] == 10
