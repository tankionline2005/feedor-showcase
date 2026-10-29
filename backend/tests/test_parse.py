from datetime import datetime, timezone

from telethon.tl import types

import pytest

from app.tg.parse import compute_er, compute_fr, group_messages, invite_chat, metrics, pick_thumb, post_fields, public_username

DATE = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def msg(id, text="", grouped_id=None, views=0, forwards=0, reactions=(), comments=None, media=None):
    return types.Message(
        id=id,
        peer_id=types.PeerChannel(1),
        date=DATE,
        message=text,
        grouped_id=grouped_id,
        views=views,
        forwards=forwards,
        media=media,
        reactions=types.MessageReactions(
            results=[types.ReactionCount(reaction=r, count=c) for r, c in reactions]
        )
        if reactions
        else None,
        replies=types.MessageReplies(replies=comments, replies_pts=0, comments=True)
        if comments is not None
        else None,
    )


def test_compute_er():
    assert compute_er(1000, 30, 10, 10) == 5.0
    assert compute_er(0, 5, 5, 5) == 0.0


def test_group_messages_merges_albums_and_skips_service():
    service = types.MessageService(id=9, peer_id=types.PeerChannel(1), date=DATE, action=types.MessageActionPinMessage())
    groups = group_messages([msg(5, grouped_id=7), msg(3), msg(4, grouped_id=7), service])
    assert [[m.id for m in g] for g in groups] == [[3], [4, 5]]


def test_post_fields_album_takes_caption_from_any_message():
    fields = post_fields([msg(4, grouped_id=7), msg(5, "caption", grouped_id=7)])
    assert fields["msg_id"] == 4
    assert fields["album_ids"] == [4, 5]
    assert fields["text"] == "caption"
    assert fields["posted_at"].tzinfo is None


def test_fr_is_forwards_of_views():
    assert compute_fr(5000, 50) == 1.0
    assert compute_fr(3, 1) == 33.333
    assert compute_fr(0, 4) == 0.0  # the count of views hidden


def test_metrics_sums_reactions_and_counts_comments():
    m = metrics(
        [
            msg(
                1,
                views=1000,
                forwards=20,
                comments=10,
                reactions=[
                    (types.ReactionEmoji("👍"), 50),
                    (types.ReactionEmoji("🔥"), 15),
                    (types.ReactionCustomEmoji(document_id=1), 5),
                ],
            )
        ]
    )
    assert m["views"] == 1000
    assert m["reactions"] == 70
    assert m["comments"] == 10
    assert m["er"] == 10.0
    assert m["fr"] == 2.0  # 20 forwards of 1000 views
    assert m["reactions_top"][0] == {"e": "👍", "c": 50}
    assert {"e": "custom", "c": 5} in m["reactions_top"]


def test_pick_thumb_prefers_480_and_up():
    sizes = [
        types.PhotoStrippedSize(type="i", bytes=b"x"),
        types.PhotoSize(type="m", w=320, h=240, size=1),
        types.PhotoSizeProgressive(type="x", w=800, h=600, sizes=[1, 2]),
        types.PhotoSizeProgressive(type="y", w=1280, h=960, sizes=[1, 2]),
    ]
    assert pick_thumb(sizes) == "x"
    assert pick_thumb(sizes[:2]) == "m"
    assert pick_thumb([]) is None


def test_public_username_falls_back_to_usernames_list():
    def chat(username=None, usernames=None):
        return types.Channel(id=1, title="Mash", photo=types.ChatPhotoEmpty(), date=None,
                             access_hash=1, broadcast=True, username=username, usernames=usernames)

    assert public_username(chat(username="rbc_news")) == "rbc_news"
    # several usernames (Fragment): Telegram leaves `username` empty
    assert public_username(chat(usernames=[
        types.Username(username="old_mash", active=False),
        types.Username(username="mash", active=True, editable=True),
    ])) == "mash"
    assert public_username(chat()) is None
    assert public_username(None) is None


def test_invite_chat_reads_old_and_new_join_results():
    group = types.Channel(id=77, title="Closed", photo=types.ChatPhotoEmpty(), date=None, access_hash=5, megagroup=True)
    updates = types.Updates(updates=[], users=[], chats=[group], date=None, seq=0)
    assert invite_chat(updates) is group  # older layers: plain Updates
    assert invite_chat(types.messages.ChatInviteJoinResultOk(updates=updates)) is group  # current layer
    with pytest.raises(ValueError, match="проверки ботом"):
        invite_chat(types.messages.ChatInviteJoinResultWebView(bot_id=1, query_id=2, users=[]))


def test_a_post_telegram_sends_empty_is_marked_unsupported():
    # a rich message (headings, tables, pictures in the text) reaches our older layer with nothing in it
    assert post_fields([msg(1)])["media_type"] == "unsupported"
    unsupported = post_fields([msg(2, media=types.MessageMediaUnsupported())])
    assert unsupported["media_type"] == "unsupported" and unsupported["media_count"] == 0
    assert post_fields([msg(3, text="just text")])["media_type"] is None
