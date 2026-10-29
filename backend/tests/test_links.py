import pytest

from app.tg.links import ChannelLink, parse_channel_link, split_links


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@durov", ChannelLink(username="durov")),
        ("durov", ChannelLink(username="durov")),
        ("https://t.me/durov", ChannelLink(username="durov")),
        ("t.me/durov/123", ChannelLink(username="durov")),
        ("https://t.me/s/durov", ChannelLink(username="durov")),
        ("https://telegram.me/durov?start=1", ChannelLink(username="durov")),
        ("https://t.me/+AbC-123_x", ChannelLink(invite_hash="AbC-123_x")),
        ("t.me/joinchat/AbC123", ChannelLink(invite_hash="AbC123")),
    ],
)
def test_parse_channel_link(raw, expected):
    assert parse_channel_link(raw) == expected


@pytest.mark.parametrize("raw", ["", "@ab", "https://example.com/durov", "1abc", "t.me/"])
def test_parse_channel_link_rejects(raw):
    with pytest.raises(ValueError):
        parse_channel_link(raw)


def test_split_links():
    assert split_links("@a1234, @b1234\n t.me/c1234;  ") == ["@a1234", "@b1234", "t.me/c1234"]


def test_key_is_case_insensitive():
    assert parse_channel_link("@Durov").key == parse_channel_link("durov").key
