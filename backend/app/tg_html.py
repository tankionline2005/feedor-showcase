"""A post's text as Telegram takes it: its HTML formatting (parse_mode HTML), and nothing else.

Telegram's HTML: <b>, <i>, <u>, <s>, <code>, <pre>, <tg-spoiler>, <a href>, <blockquote> (expandable too);
line breaks are plain new lines; the text is escaped (&lt; &gt; &amp;). Whatever comes — from the AI, the
editor, a pasted page — is cleaned to that: the synonyms become those tags (<strong> → <b>), blocks become new
lines, other tags go and their text stays, nothing is put inside <code>/<pre> (Telegram allows no formatting
there), a link keeps only a web, tg: or mailto: address.

Its length is counted the way Telegram counts it: the visible text, in UTF-16 units (an emoji is two). A post's
caption under pictures takes 1024 of them, a text message 4096.
"""

import html
import re
from html.parser import HTMLParser

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096

_SAME = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "ins": "u", "s": "s", "strike": "s", "del": "s",
         "code": "code", "pre": "pre", "tg-spoiler": "tg-spoiler", "blockquote": "blockquote", "a": "a"}
_BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "tr", "section", "article"}
_LINK = re.compile(r"^(https?://|tg://|mailto:)", re.I)


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []  # Telegram's tags open now, in order
        self.hidden = 0  # inside <script>/<style>: not text at all
        self.after_block = False  # a quote or code block just closed: what follows starts on a new line

    def _in_code(self) -> bool:
        return any(t in ("code", "pre") for t in self.open)

    def _newline(self, n: int = 1) -> None:
        text = "".join(self.out)
        have = len(text) - len(text.rstrip("\n"))
        if text.strip() and have < n:
            self.out.append("\n" * (n - have))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in ("script", "style"):
            self.hidden += 1
            return
        if tag == "br":
            self.out.append("\n")
            return
        if tag in _BLOCKS:
            self._newline(2 if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6") else 1)
            if tag == "li":
                self.out.append("• ")
            return
        name = _SAME.get(tag)
        if tag == "span" and "tg-spoiler" in (values.get("class") or ""):
            name = "tg-spoiler"
        if name is None or self._in_code() or (name == "a" and not _LINK.match((values.get("href") or "").strip())):
            return  # its text stays
        self._break()
        if name in ("blockquote", "pre"):
            self._newline()
        if name == "a":
            self.out.append(f'<a href="{html.escape((values.get("href") or "").strip(), quote=True)}">')
        elif name == "blockquote" and "expandable" in values:
            self.out.append("<blockquote expandable>")
        else:
            self.out.append(f"<{name}>")
        self.open.append(name)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
            return
        if tag in _BLOCKS:
            self._newline(2 if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6") else 1)
        name = _SAME.get(tag) or ("tg-spoiler" if tag == "span" else None)
        if name is None or name not in self.open:
            return  # one that was dropped
        # close what was opened inside it too, the way the browser would
        while self.open:
            top = self.open.pop()
            self.out.append(f"</{top}>")
            if top == name:
                break
        if name in ("blockquote", "pre"):
            self.after_block = True

    def _break(self) -> None:
        if self.after_block:
            self.after_block = False
            self._newline()

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        if self.after_block and data.strip():
            self.after_block = False
            if not data.startswith("\n"):
                self._newline()
        self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        # zero-width junk models copy from channels' posts (a byte-order mark, zero-width spaces) goes; the joiner
        # inside emoji stays
        text = re.sub("[﻿​]", "", "".join(self.out)).replace("\r\n", "\n").replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # empty tags say nothing
        for _ in range(3):
            text = re.sub(r"<(b|i|u|s|code|pre|tg-spoiler|blockquote)( expandable)?>\s*</\1>", "", text)
        return text.strip()


def clean(text: str) -> str:
    """Telegram's HTML of `text` (see the module)."""
    parser = _Cleaner()
    parser.feed(text or "")
    parser.close()
    return parser.result()


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain(text: str) -> str:
    """What the post reads as, without its formatting."""
    parser = _Text()
    parser.feed(text or "")
    parser.close()
    return "".join(parser.parts)


def length(text: str) -> int:
    """The post's length as Telegram counts it: visible characters in UTF-16 units."""
    return len(plain(text).encode("utf-16-le")) // 2


def limit(with_media: bool) -> int:
    return CAPTION_LIMIT if with_media else TEXT_LIMIT


_FENCE = re.compile(r"```[\w+-]*\n?(.*?)```", re.S)


def from_markdown(md: str) -> str:
    """An AI answer written in Markdown, as Telegram's HTML: bold, italic, strikethrough, spoilers (||…||), code,
    links, quotes (> …); headings become bold lines, list items «• ». For «Оформить как пост»."""
    blocks: list[str] = []

    def keep(match: re.Match) -> str:
        blocks.append(f"<pre>{html.escape(match.group(1).strip(), quote=False)}</pre>")
        return f"\x00{len(blocks) - 1}\x00"

    text = _FENCE.sub(keep, (md or "").replace("\r\n", "\n"))
    out: list[str] = []
    quote: list[str] = []

    def inline(line: str) -> str:
        codes: list[str] = []

        def code(match: re.Match) -> str:
            codes.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
            return f"\x01{len(codes) - 1}\x01"

        line = re.sub(r"`([^`\n]+)`", code, line)
        line = html.escape(line, quote=False)
        line = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)",
                      lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', line)
        line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])",
                      lambda m: f"<i>{m.group(1) or m.group(2)}</i>", line)
        line = re.sub(r"~~(.+?)~~", r"<s>\1</s>", line)
        line = re.sub(r"\|\|(.+?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", line)
        return re.sub(r"\x01(\d+)\x01", lambda m: codes[int(m.group(1))], line)

    def flush_quote() -> None:
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()

    for raw in text.split("\n"):
        line = raw.rstrip()
        if re.match(r"^\s*>\s?", line):
            quote.append(inline(re.sub(r"^\s*>\s?", "", line)))
            continue
        flush_quote()
        if m := re.match(r"^\s*#{1,6}\s+(.*)$", line):
            out.append(f"<b>{inline(m.group(1))}</b>")
        elif m := re.match(r"^(\s*)[-*+]\s+(.*)$", line):
            out.append(f"{m.group(1)}• {inline(m.group(2))}")
        elif re.fullmatch(r"\s*([-*_]\s*){3,}", line):
            out.append("")
        else:
            out.append(inline(line))
    flush_quote()
    result = re.sub(r"\x00(\d+)\x00", lambda m: blocks[int(m.group(1))], "\n".join(out))
    return clean(result)
