"""«Обложки»: the project's cover style and a post's cover, made by the server (covers.py)."""

import io
from functools import partial

from PIL import Image

from app import covers, publications

from test_chat_posts import _bot_token, _project_channel, _setup, bot, channel_bot, model, talk  # noqa: F401
from test_publications import _posts, _project, _tool_answer, _topics
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(out, "PNG")
    return out.getvalue()


def test_every_template_draws_a_cover():
    for template in covers.TEMPLATES:
        for size, (w, h) in covers.SIZES.items():
            data = covers.render({"template": template, "size": size, "accent": "#ff9500", "font": "serif",
                                  "position": "top", "show_channel": True}, "Заголовок " * 20, "Канал",
                                 photo=None, logo=_png())
            img = Image.open(io.BytesIO(data))
            assert img.format == "JPEG" and img.size == (w, h)


def test_the_owner_sets_the_style_and_a_post_goes_with_its_cover(ctx, model, bot, channel_bot):
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9981)
    ids = run(client, _setup, 9981)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))

    style = owner.get("/api/covers").json()
    assert style["mode"] == "off" and style["template"] == "gradient" and len(style["templates"]) == 16 and len(style["fonts"]) == 10
    assert owner.put("/api/covers", json={"template": "nope"}).status_code == 422
    assert owner.put("/api/covers", json={"accent": "red"}).status_code == 422
    style = owner.put("/api/covers", json={"template": "photo", "accent": "#FF3B30", "size": "4:5"}).json()
    assert style["accent"] == "#ff3b30" and style["size"] == "4:5"
    r = owner.get("/api/covers/preview.jpg", params={"template": "solid"})
    assert r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    tile = owner.get("/api/covers/preview.jpg", params={"template": "news", "size": "4:5", "width": 240})
    assert Image.open(io.BytesIO(tile.content)).size == (240, 300)
    up = owner.post("/api/covers/logo", files={"file": ("logo.png", _png(), "image/png")}).json()
    assert up["has_logo"] is True
    assert owner.post("/api/covers/logo", files={"file": ("x.png", b"not a picture", "image/png")}).status_code == 422

    # a post with a cover: its first picture, made when it goes out
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "<b>Ракета</b> стартовала", "source_post_ids": [ids["other"]]}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]
    card = owner.put(f"/api/chat-posts/{post_id}/cover", json={"on": True, "title": "  Старт  ракеты "}).json()
    assert card["cover"] and card["cover_title"] == "Старт ракеты" and card["cover_url"].startswith(f"/api/chat-posts/{post_id}/cover.jpg")
    picture = Image.open(io.BytesIO(owner.get(card["cover_url"]).content))
    assert picture.size == (1080, 1350)
    # another style: another address, so the browser does not show the old picture
    owner.put("/api/covers", json={"accent": "#34c759"})
    again = owner.put(f"/api/chat-posts/{post_id}/cover", json={"on": True}).json()
    assert again["cover_url"] != card["cover_url"] and again["cover_title"] == "Старт ракеты"
    bot.clear()
    assert owner.post(f"/api/chat-posts/{post_id}/publish").json()["published"]
    method, params, files = bot[-1]
    assert method == "sendMediaGroup" and params["media"][0]["media"] == "attach://cover" and "cover" in files
    assert len(params["media"]) == 2  # the cover and the source's photo

    # off again; an empty title means the post's first line
    card = owner.put(f"/api/chat-posts/{post_id}/cover", json={"on": False, "title": ""}).json()
    assert card["cover"] is False and card["cover_url"] is None and card["cover_title"] == "Ракета стартовала"


def test_autoposts_take_a_cover_by_the_project_s_mode(ctx, model):
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9982)
    ids = run(client, _setup, 9982)
    project = run(client, partial(_project, ids["project"]))
    t0, t1 = run(client, partial(_topics, ids["project"], ids["other"], 2))
    owner.put("/api/publications/settings", json={"topic_posts": True})
    owner.put("/api/covers", json={"mode": "no_photo"})
    # one post with the source's photo, one without any: only that one takes a cover
    script += [_tool_answer("С фото", [ids["other"]]), _tool_answer("Без фото", [])]
    run(client, partial(publications.after_topics, project, [t0, t1]))
    rows = {p.text: p for p in run(client, partial(_posts, ids["project"]))}
    assert rows["С фото"].media and not rows["С фото"].cover
    assert rows["Без фото"].cover is (not rows["Без фото"].media)
