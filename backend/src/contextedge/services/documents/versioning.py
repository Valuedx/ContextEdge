"""Detect duplicates and versions in a batch of uploaded documents.

A folder upload of SOPs realistically looks like this:

    VPN SOP.docx
    VPN SOP Final.docx
    VPN SOP Final v2.docx
    VPN SOP Updated.pdf
    Old/VPN SOP.docx

Ingested naively, all five become independent evidence, all five rank in
search, and a playbook generated from them cites whichever the embedding
happened to favour — quite possibly the one in ``Old/``. The failure is
not that duplicates waste storage; it is that **retrieval silently
returns superseded guidance** and nothing marks it as superseded.

Three signals, cheapest first, and deliberately no more:

1. **Identical bytes** — the same file uploaded twice. Certain.
2. **Identical normalised text** — a PDF export of a Word document, or a
   re-save that changed only metadata. Certain enough: the words are the
   same, so the guidance is.
3. **Same document family by name, different version marker** — the
   ``Final v2`` case. This is a *suggestion*, not a merge: names lie,
   and two genuinely different documents can share a stem.

What this does NOT do is decide which version is authoritative on
content. Ranking by "which text looks newer" is exactly the kind of
inference that produces a confident wrong answer, and the effective-date
metadata that would settle it is a phase-3 concern. Where the evidence is
certain (1 and 2) it marks a duplicate; where it is circumstantial (3) it
records a relationship for a human to confirm.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

# Version markers people actually put in filenames. Ordered so the more
# specific pattern wins: "v2" inside "Final v2" should read as version 2,
# not as the word "final".
# Lookbehind rather than \b before the marker: "_" is a word character,
# so \brev cannot match in "VPN_SOP_rev3" — the underscore separator
# people actually use defeats it.
_VERSION_PATTERNS = (
    re.compile(r"(?<![a-z0-9])v(?:er(?:sion)?)?[\s._-]*(\d+(?:\.\d+)*)\b", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])rev(?:ision)?[\s._-]*(\d+(?:\.\d+)*)\b", re.IGNORECASE),
    # Windows "SOP (2).docx". Anchored to the end of the STEM, so callers
    # must strip the extension first — anchoring to end-of-string meant
    # ".docx" defeated it and every Windows copy looked unversioned.
    re.compile(r"\(\s*(\d+)\s*\)\s*$"),
)

_EXTENSION_RE = re.compile(r"\.[a-z0-9]{1,5}$", re.IGNORECASE)


def _stem(filename: str) -> str:
    return _EXTENSION_RE.sub("", (filename or "").strip())

# Words that mark a revision without numbering it. Ranked: a document
# labelled "final" supersedes one labelled "draft", and "updated"
# supersedes plain. Equal rank means no opinion.
_QUALIFIER_RANK = {
    "old": -2,
    "archive": -2,
    "archived": -2,
    "superseded": -2,
    "draft": -1,
    "final": 1,
    "updated": 1,
    "new": 1,
    "latest": 2,
    "current": 2,
}

# Tokens stripped before comparing families. Matched one word at a time,
# so multi-word entries would never fire — "of" is listed because the
# "Copy of X" pattern otherwise leaves it behind and splits the family.
# Stripping it consistently keeps families stable; the risk is only that
# "Ministry of Health SOP" and "Ministry Health SOP" collide, and those
# are almost certainly the same document anyway.
_NOISE_TOKENS = frozenset(
    {"copy", "of", "final", "updated", "new", "latest", "current",
     "draft", "old", "archive", "archived", "superseded", "rev", "revision",
     "version", "ver", "v"}
)

_WORD_RE = re.compile(r"[a-z0-9]+")
_WS_RE = re.compile(r"\s+")


@dataclass(slots=True)
class DocumentIdentity:
    """What we can say about one uploaded file before reading it deeply."""

    filename: str
    content_hash: str
    text_hash: str | None = None
    family: str = ""
    version: tuple[int, ...] | None = None
    qualifier_rank: int = 0
    folder_hint: str = ""


@dataclass(slots=True)
class DuplicateGroup:
    """Files that are the same document, or the same document family."""

    family: str
    relation: str  # "identical_bytes" | "identical_text" | "same_family"
    members: list[DocumentIdentity] = field(default_factory=list)
    # Index into ``members``. None when the signals do not order them —
    # guessing an order is how superseded guidance becomes authoritative.
    primary_index: int | None = None
    needs_review: bool = False

    @property
    def primary(self) -> DocumentIdentity | None:
        if self.primary_index is None:
            return None
        return self.members[self.primary_index]


def normalize_text(text: str | None) -> str:
    """Whitespace- and case-normalised text for equality comparison.

    Aggressive on purpose: the question is "are these the same words",
    and a PDF export of a Word file differs in line breaks, ligatures,
    and trailing spaces while saying exactly the same thing.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).lower()
    return _WS_RE.sub(" ", folded).strip()


def text_fingerprint(text: str | None) -> str | None:
    normalized = normalize_text(text)
    # Very short documents are not distinctive; two one-line files
    # sharing a sentence are not the same document.
    if len(normalized) < 200:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def document_family(filename: str) -> str:
    """The stem with version markers and revision words removed.

    "VPN SOP Final v2.docx" and "VPN SOP.docx" share the family "vpn sop".
    """
    stem = _stem(filename)
    for pattern in _VERSION_PATTERNS:
        stem = pattern.sub(" ", stem)
    words = [w for w in _WORD_RE.findall(stem.lower()) if w not in _NOISE_TOKENS]
    return " ".join(words)


def parse_version(filename: str) -> tuple[int, ...] | None:
    stem = _stem(filename)
    for pattern in _VERSION_PATTERNS:
        match = pattern.search(stem)
        if match:
            try:
                return tuple(int(p) for p in match.group(1).split("."))
            except ValueError:
                continue
    return None


def qualifier_rank(filename: str, folder: str = "") -> int:
    """Net revision signal from words in the name and the folder.

    Folder counts: a file under ``Old/`` is marked old regardless of what
    the filename claims, and that is usually the more reliable signal
    because nobody renames files when archiving them.
    """
    haystack = f"{folder} {filename}".lower()
    return sum(
        rank
        for word, rank in _QUALIFIER_RANK.items()
        if re.search(rf"\b{re.escape(word)}\b", haystack)
    )


def identify(filename: str, data: bytes, text: str | None = None,
             folder: str = "") -> DocumentIdentity:
    return DocumentIdentity(
        filename=filename,
        content_hash=hashlib.sha256(data).hexdigest(),
        text_hash=text_fingerprint(text),
        family=document_family(filename),
        version=parse_version(filename),
        qualifier_rank=qualifier_rank(filename, folder),
        folder_hint=folder,
    )


def group_documents(identities: list[DocumentIdentity]) -> list[DuplicateGroup]:
    """Duplicate groups and version families for a batch.

    These are ORTHOGONAL facts, not a partition, and computing them as
    competing buckets was wrong: excluding byte-duplicates from family
    grouping left "which VPN SOP is authoritative" with two answers —
    one from the duplicate pair, one from the remaining versions, never
    compared. Duplication is about *these files being the same*;
    family is about *which document supersedes which*.

    So exact-duplicate detection is exclusive (a file is byte-identical
    to one group or none), and family grouping runs over **every**
    identity regardless.
    """
    groups: list[DuplicateGroup] = []
    claimed: set[int] = set()

    def _emit(indexes: list[int], relation: str) -> None:
        members = [identities[i] for i in indexes]
        group = DuplicateGroup(
            family=members[0].family or members[0].filename,
            relation=relation,
            members=members,
        )
        _order(group)
        groups.append(group)

    # Exact duplicates: bytes first, then normalised text. Exclusive,
    # because a file identical by bytes is trivially identical by text
    # and reporting both says nothing extra.
    for key_fn, relation in (
        (lambda d: ("bytes", d.content_hash), "identical_bytes"),
        (lambda d: ("text", d.text_hash) if d.text_hash else None, "identical_text"),
    ):
        buckets: dict[tuple, list[int]] = {}
        for index, identity in enumerate(identities):
            if index in claimed:
                continue
            key = key_fn(identity)
            if key is None:
                continue
            buckets.setdefault(key, []).append(index)
        for indexes in buckets.values():
            if len(indexes) > 1:
                _emit(indexes, relation)
                claimed.update(indexes)

    # Families over everything, so supersession is answered once.
    family_buckets: dict[str, list[int]] = {}
    for index, identity in enumerate(identities):
        if identity.family:
            family_buckets.setdefault(identity.family, []).append(index)
    for indexes in family_buckets.values():
        if len(indexes) > 1:
            _emit(indexes, "same_family")

    return groups


def _order(group: DuplicateGroup) -> None:
    """Pick the primary member, or decline to.

    Byte- and text-identical members are interchangeable, so the first is
    as good as any. A version family is ordered by explicit version
    number, then by revision words. When neither separates them the
    group is left unordered and flagged: choosing arbitrarily is how the
    copy in ``Old/`` becomes the one a playbook cites.
    """
    if group.relation in ("identical_bytes", "identical_text"):
        group.primary_index = 0
        return

    versioned = [
        (index, member)
        for index, member in enumerate(group.members)
        if member.version is not None
    ]
    if versioned:
        best = max(versioned, key=lambda pair: pair[1].version or ())
        rivals = [p for p in versioned if (p[1].version or ()) == (best[1].version or ())]
        if len(rivals) == 1:
            group.primary_index = best[0]
            group.needs_review = len(versioned) != len(group.members)
            return

    ranks = [m.qualifier_rank for m in group.members]
    if ranks.count(max(ranks)) == 1:
        group.primary_index = ranks.index(max(ranks))
        group.needs_review = True
        return

    group.primary_index = None
    group.needs_review = True
