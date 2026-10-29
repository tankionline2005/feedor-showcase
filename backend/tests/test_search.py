"""The feed's search: the post's text, and the text read from its pictures (the card then says so)."""

from app.web import members as members_api

from test_triggers import _post
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def test_the_search_finds_the_text_on_pictures_too(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9981)
    assert owner.post("/api/channels", json={"links": "@uc_search"}).json()["added"]
    run(client, _activate, "@uc_search", 99811, 0)
    in_text = run(client, _post, "@uc_search", 1, "Скидки на кофе в центре")
    on_picture = run(client, _post, "@uc_search", 2, "Смотрите фото", "SALE: кофе за полцены", "done")
    run(client, _post, "@uc_search", 3, "Погода хорошая", "Солнечно", "done")

    found = {p["id"]: p.get("found_on_picture", False)
             for p in owner.get("/api/posts", params={"q": "кофе"}).json()["items"]}
    assert found == {in_text: False, on_picture: True}
    # in any case (SQLite, the tests' database, folds only Latin letters; PostgreSQL folds Cyrillic too)
    assert [p["id"] for p in owner.get("/api/posts", params={"q": "Sale"}).json()["items"]] == [on_picture]
