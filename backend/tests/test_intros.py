"""The welcome cards of the sections: each person sees a section's once, wherever they sign in (web/auth.py)."""

from app.web import members as members_api

from test_members import member_login
from test_user_channels import ctx, sign_in  # noqa: F401  (ctx is a fixture)


def test_each_person_sees_a_section_s_welcome_once(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9441)
    assert owner.get("/api/auth/me").json()["intros_seen"] == []
    assert owner.post("/api/auth/intros", json={"key": "feed"}).json() == {"intros_seen": ["feed"]}
    assert owner.post("/api/auth/intros", json={"key": "feed"}).json() == {"intros_seen": ["feed"]}  # once
    owner.post("/api/auth/intros", json={"key": "topics"})
    assert owner.get("/api/auth/me").json()["intros_seen"] == ["feed", "topics"]
    assert owner.post("/api/auth/intros", json={"key": "secret"}).status_code == 422

    # a staff member has their own
    password = owner.post("/api/members", json={"login": "intro.anna"}).json()["password"]
    staff, r = member_login(app, "intro.anna", password)
    assert r.status_code == 200
    assert staff.get("/api/auth/me").json()["intros_seen"] == []
    staff.post("/api/auth/intros", json={"key": "channels"})
    assert staff.get("/api/auth/me").json()["intros_seen"] == ["channels"]
    assert owner.get("/api/auth/me").json()["intros_seen"] == ["feed", "topics"]
