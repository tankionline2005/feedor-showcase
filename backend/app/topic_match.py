"""Which of a project's topics the new posts may be about: picked by the words they share, without the AI.

The model of a topics run sees only the topics picked here (topics.py), so the topics of the last two weeks
stay within its reach however many there are, while the request stays the same size. Words are taken in their
dictionary forms (pymorphy3: «ракеты», «ракету» → «ракета»); function words are dropped; a word few topics
have weighs more than a common one (IDF). A topic's words in its title count a little more.
"""

import math
import re
from collections import Counter
from functools import lru_cache

WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:-[0-9A-Za-zА-Яа-яЁё]+)*")
# parts of speech that say nothing of what a text is about
DROP = {"PREP", "CONJ", "PRCL", "INTJ", "NPRO", "PRED", "COMP", "ADVB", "NUMR"}
# frequent words that are no clue either: the channels' everyday vocabulary
STOP = {
    "это", "весь", "который", "свой", "такой", "наш", "ваш", "год", "день", "время", "человек", "сообщать",
    "стать", "мочь", "быть", "сказать", "заявить", "также", "новый", "один", "два", "первый", "очень", "самый",
    "подписаться", "канал", "telegram", "http", "https", "www", "com", "t", "me", "читать", "источник",
}
TITLE_BONUS = 0.5  # a shared word that is in the topic's title counts this much more

_analyzer = None


def _morph():
    global _analyzer
    if _analyzer is None:
        import pymorphy3

        _analyzer = pymorphy3.MorphAnalyzer()
    return _analyzer


@lru_cache(maxsize=300_000)
def lemma(word: str) -> str | None:
    """The word's dictionary form, or None for a word that is no clue to the subject."""
    plain = word.lower().replace("ё", "е")
    if any(ch.isdigit() for ch in plain):
        return plain if len(plain) >= 2 else None  # numbers, models, years: as written
    if len(plain) < 3:
        return None
    if not re.search(r"[а-я]", plain):  # Latin: names, brands
        return None if plain in STOP else plain
    parse = _morph().parse(plain)[0]
    if parse.tag.POS in DROP:
        return None
    form = parse.normal_form.replace("ё", "е")
    return None if form in STOP or len(form) < 3 else form


def terms(text: str) -> frozenset[str]:
    return frozenset(t for t in (lemma(w) for w in WORD.findall(text or "")) if t)


class Index:
    """The candidate topics' words, and how rare each word is among them."""

    def __init__(self, topics: list[tuple[int, str, str]]):
        """topics: (id, title, text about it: the summary)."""
        self.title = {tid: terms(title) for tid, title, _ in topics}
        self.words = {tid: self.title[tid] | terms(text) for tid, _, text in topics}
        df = Counter(w for ws in self.words.values() for w in ws)
        n = len(self.words)
        self.idf = {w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()}

    def score(self, post_terms: frozenset[str], tid: int) -> float:
        shared = post_terms & self.words[tid]
        title = self.title[tid]
        return sum(self.idf[w] * (1 + TITLE_BONUS * (w in title)) for w in shared)

    def best(self, post_terms: frozenset[str], top: int, min_score: float) -> list[tuple[float, int]]:
        scored = [(self.score(post_terms, tid), tid) for tid in self.words]
        return sorted([x for x in scored if x[0] >= min_score], reverse=True)[:top]


def pick(topics: list[tuple[int, str, str]], posts: list[str], *, limit: int, per_post: int = 3,
         min_score: float = 6.0) -> list[int]:
    """The ids of the topics the posts may be about, the likeliest first, `limit` at most: for every post its
    `per_post` best matches scoring at least `min_score`."""
    if not topics or not posts:
        return []
    index = Index(topics)
    best: dict[int, float] = {}
    for text in posts:
        for score, tid in index.best(terms(text), per_post, min_score):
            best[tid] = max(best.get(tid, 0.0), score)
    return [tid for tid, _ in sorted(best.items(), key=lambda x: -x[1])][:limit]
