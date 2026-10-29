"""«Файл-список отслеживаемых каналов»: the bot sends the project's channels as channel-list.md."""

from app import notify, settings_store
from app.db import SessionLocal
from app.web import members as members_api

from test_members import member_login
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


async def _bot() -> None:
    async with SessionLocal() as s:
        await settings_store.update(s, {"notify_bot_token": "123:test"})


def test_the_bot_sends_the_list_of_channels(ctx, monkeypatch):
    app, client = ctx
    members_api._failed.clear()
    sent: list[dict] = []

    async def fake(token, chat_id, filename, data, caption=""):
        sent.append({"chat_id": chat_id, "filename": filename, "text": data.decode(), "caption": caption})
        return {}

    monkeypatch.setattr(notify, "bot_document", fake)
    run(client, _bot)
    owner = sign_in(app, client, 9961)
    for link, tg in (("@uc_list_b", 99612), ("@uc_list_a", 99611)):
        assert owner.post("/api/channels", json={"links": link}).json()["added"]
        run(client, _activate, link, tg, 0)

    assert owner.post("/api/channels/list-file").json() == {"ok": True, "count": 2}
    assert sent[0]["chat_id"] == 9961 and sent[0]["filename"] == "channel-list.md"
    links = [line for line in sent[0]["text"].splitlines() if line.startswith("- ")]
    assert links == ["- https://t.me/uc_list_a", "- https://t.me/uc_list_b"]

    # a staff member: to their own Telegram, once it is linked
    created = owner.post("/api/members", json={"login": "list.staff"}).json()
    staff, _ = member_login(app, "list.staff", created["password"])
    refused = staff.post("/api/channels/list-file")
    assert refused.status_code == 409 and "Привяжите Telegram" in refused.json()["detail"]
    assert len(sent) == 1
