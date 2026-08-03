"""Does this knowledge article apply to THIS environment?

Semantic similarity answers "is this about the same subject". It does not
answer "does it apply to the system in front of me", and those are
different questions with different failure modes. An article about one
plugin scores well against a fault in a different plugin because the
vocabulary overlaps almost entirely; an article written for an older
release reads as a perfect match right up to the point where the menu
path it describes no longer exists.

**Nothing here is product-specific.** The component vocabulary is
derived per tenant from data ContextEdge already holds — the entity
graph it builds from tickets (configuration items, business services,
assignment groups) plus the categories on the knowledge itself. A tenant
running Citrix and VMware gets Citrix and VMware terms; one running a
bespoke platform gets its own. A hardcoded product list would serve
exactly one customer and quietly degrade for every other.

Five facets, and the weight each carries reflects how reliably real
corpora carry it — measured on a live 18-article KB before choosing:

- **Component** — the strong signal, present in effectively every
  article, and the facet most confident to demote on.
- **Deployment** (cloud vs on-premise) — nearly as strong when stated.
  An article for a vendor's cloud edition is not merely dated with
  respect to the self-hosted one: the steps are unperformable. Cloud has
  no file system to edit, no service to restart, no server to log into,
  and the admin UI is a different product. Version is irrelevant to that
  gap — a *newer* cloud article is no more applicable to an on-premise
  estate than an older one.
- **Environment** (production, staging, QA, development) — rarely a
  reason to demote on its own, but it decides *which* version to compare
  against. A tenant is not on one version; they are on 9.4 in production
  and 9.12 in QA, and an article for 9.12 applies to one of those and
  not the other. Comparing against a single tenant-wide version answers
  the wrong question half the time.
- **Product version** — stated in only a third of articles. Useful when
  present on both sides, useless otherwise.
- **Platform** (OS family) — sparse; a weak tiebreaker.

**The rule that matters most: silence is not inapplicability.** Two
thirds of real articles state no version. Treating "no version
mentioned" as "does not apply here" would discard most of a corpus, and
an unversioned article is usually unversioned because it applies
broadly.

**And a mismatch flags rather than hides.** An article written for an
older release is often the only guidance that exists. The reviewer needs
to see it *and* the caveat — dropping it silently leaves them with
nothing and no idea anything was withheld.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

# OS families are genuinely universal, unlike product names, so this one
# small list is safe to fix.
_PLATFORMS: dict[str, tuple[str, ...]] = {
    "windows": ("windows", "win server", "services.msc", ".bat", "powershell"),
    "linux": ("linux", "unix", "ubuntu", "centos", "rhel", "red hat", ".sh"),
    "macos": ("macos", "mac os", "osx"),
}

# Deployment model. Universal across vendors — every SaaS-or-install
# product draws the same line, whatever it calls the two editions
# (Cloud/Server, Cloud/Data Center, Online/On-Premises, SaaS/self-hosted).
#
# Deliberately NOT including bare "server": it appears in almost every
# infrastructure sentence ever written ("the server rebooted") and would
# mark most of a corpus on-premise. A cue has to be unambiguous about
# meaning the *edition* rather than a machine.
_DEPLOYMENTS: dict[str, tuple[str, ...]] = {
    "cloud": (
        "cloud", "saas", "software as a service", "hosted instance",
        "cloud edition", "cloud-hosted", "online edition", "multi-tenant",
    ),
    "onprem": (
        "on-prem", "on prem", "on-premise", "on premise", "on-premises",
        "on premises", "self-hosted", "self hosted", "server edition",
        "data center edition", "datacenter edition", "locally installed",
        "local installation", "installed on the server",
    ),
}

# Deployment tiers. Word-bounded because the short forms are substrings
# of ordinary words — unbounded "prod" matches "product" and would mark
# every product mention as production.
_ENVIRONMENTS: dict[str, tuple[str, ...]] = {
    "production": (r"\bprod\b", r"\bproduction\b", r"\blive\s+environment\b"),
    "staging": (r"\bstaging\b", r"\bstage\s+environment\b", r"\bpre-?prod(?:uction)?\b"),
    "qa": (r"\bqa\b", r"\buat\b", r"\btest\s+environment\b", r"\btesting\s+environment\b"),
    "development": (r"\bdev\b", r"\bdevelopment\s+environment\b", r"\bsandbox\b"),
}
_ENVIRONMENT_RES: dict[str, tuple[re.Pattern, ...]] = {
    tier: tuple(re.compile(cue, re.IGNORECASE) for cue in cues)
    for tier, cues in _ENVIRONMENTS.items()
}

# How a tenant's own environment labels map onto those tiers. Entity
# .environment is free text populated by whatever CMDB fed it.
_ENVIRONMENT_ALIASES: dict[str, str] = {
    "prod": "production", "production": "production", "prd": "production",
    "live": "production", "p": "production",
    "stage": "staging", "staging": "staging", "stg": "staging",
    "preprod": "staging", "pre prod": "staging", "pre production": "staging",
    "pre prd": "staging",
    "qa": "qa", "uat": "qa", "test": "qa", "tst": "qa", "sit": "qa",
    "dev": "development", "development": "development", "sandbox": "development",
}
_ENVIRONMENT_PHRASES: tuple[str, ...] = tuple(
    sorted(_ENVIRONMENT_ALIASES, key=len, reverse=True)
)
_NEGATED_ENVIRONMENT_RE = re.compile(r"\bnon\s?(?:prod|production|prd|live)\b")

# "<product name> <version>" in prose. Product-agnostic: captures the
# token(s) immediately preceding a version number so "Tomcat 9.0.65" and
# "GlobalProtect 6.2" both yield a (product, version) pair without either
# being known in advance.
#
# The trailing ``(?!\.\d)`` is load-bearing: IT knowledge bases are full
# of IP addresses, and "10.10.10.51" or "0.0.0.0" otherwise parses as the
# version 10.10.10. Measured on the live corpus, dotted quads were the
# single largest source of false version matches — an article "written
# for 10.10.10" that is actually naming a host.
_PRODUCT_VERSION_RE = re.compile(
    r"\b([A-Za-z][\w.\-]{2,30}(?:\s+[A-Z][\w.\-]{1,20})?)"
    r"[\s\-]+v?(?<![\d.])(\d+\.\d+(?:\.\d+)?)(?!\.?\d)"
)

# Version-bearing custom fields. Matched on the NAME's shape rather than
# a fixed key, because the slug is per-portal.
_VERSION_FIELD_RE = re.compile(
    r"(?:^|_)(?:product|app|application|build|release|sw|software|sys|system)?_?"
    r"(?:version|ver|build|release)(?:$|_)",
    re.IGNORECASE,
)
_LOOSE_VERSION_RE = re.compile(r"^\s*v?(\d+\.\d+(?:\.\d+)?)\s*$", re.IGNORECASE)

# Words that precede a version but name nothing — without this,
# "upgraded to 7.5.1" yields the product "upgraded".
_NON_PRODUCT_TOKENS = frozenset(
    {
        "version", "ver", "build", "release", "upgraded", "updated", "running",
        "install", "installed", "since", "before", "after", "from", "to", "on",
        "using", "use", "the", "a", "an", "is", "was", "in", "at", "and", "or",
        "for", "with", "of", "by", "as", "not", "no", "all", "any", "see",
        "step", "port", "error", "code", "line", "page", "figure", "table",
        # Boilerplate that trails a version in almost every vendor doc.
        # "Apache License 2.0" is a licence, not the product's release.
        "license", "licence", "copyright", "edition", "revision", "spec",
        "schema", "protocol", "format", "standard", "level", "phase",
        # Schedule words, which sit before a number in report prose
        # ("Daily 2.5") and name no product at all.
        "daily", "weekly", "monthly", "hourly", "every", "each",
    }
)

MIN_TOKEN_CHARS = 3

# Bounds on the corpus scan that harvests product names. Generous enough
# to reach the "Applies to" section of a long article — the live corpus's
# SHORTEST document is 8.3k characters — and bounded so a large knowledge
# base does not turn one retrieval into a full-table read.
CORPUS_VOCABULARY_DOCS = 300
CORPUS_VOCABULARY_CHARS = 40_000

# Key a platform-wide version is filed under when nobody has said which
# product it belongs to. Deliberately unmatchable by any article, so the
# version is visible without being compared against a component that
# numbers itself independently.
PLATFORM_KEY = "_platform"

APPLIES = "applies"
UNKNOWN = "unknown"
MISMATCH = "mismatch"


@dataclass(slots=True)
class Applicability:
    """What a document or an incident says about where it applies."""

    components: set[str] = field(default_factory=set)
    platforms: set[str] = field(default_factory=set)
    versions: set[str] = field(default_factory=set)
    # (product, version) pairs, so a component's version is not compared
    # against an unrelated product's.
    product_versions: dict[str, str] = field(default_factory=dict)
    # "cloud" / "onprem". Both present means the text discusses the
    # difference rather than being scoped to one, which is why an overlap
    # anywhere is treated as compatible.
    deployments: set[str] = field(default_factory=set)
    # production / staging / qa / development.
    environments: set[str] = field(default_factory=set)
    # "applies to X and later" / "removed in X". A range is not a point,
    # and collapsing one to the other is what turns an article that
    # explicitly covers your release into a version-conflict warning.
    version_floor: dict[str, str] = field(default_factory=dict)
    version_ceiling: dict[str, str] = field(default_factory=dict)
    # "llm" or "rules". Carried so a reviewer can tell a read from a
    # regex match, and so coverage of the two can be compared.
    extracted_by: str = "rules"

    def to_payload(self) -> dict:
        """Persistable form. Mirrors the extraction schema so a stored
        record round-trips through ``applicability_from_payload``."""
        return {
            "components": sorted(self.components),
            "platforms": sorted(self.platforms),
            "environments": sorted(self.environments),
            "deployment": sorted(self.deployments)[0] if self.deployments else "unknown",
            "product_versions": dict(self.product_versions),
            "version_floor": dict(self.version_floor),
            "version_ceiling": dict(self.version_ceiling),
            "extracted_by": self.extracted_by,
        }

    def is_silent(self) -> bool:
        return not (
            self.components
            or self.platforms
            or self.versions
            or self.deployments
            or self.environments
        )


@dataclass(slots=True)
class ApplicabilityMatch:
    verdict: str = UNKNOWN
    component_overlap: set[str] = field(default_factory=set)
    component_conflict: tuple[str, str] | None = None
    deployment_conflict: tuple[str, str] | None = None
    environment_conflict: tuple[str, str] | None = None
    version_conflict: tuple[str, str] | None = None
    platform_conflict: tuple[str, str] | None = None
    # Which environment's version the comparison used, when the incident
    # named one. Reported so a reviewer can see the article was judged
    # against QA's release rather than production's.
    version_environment: str | None = None
    # True when the article documents a RELEASE THE ENVIRONMENT HAS NOT
    # REACHED — the direction where steps may reference things that do
    # not exist yet.
    version_ahead_of_environment: bool = False
    # Products whose version was settled by a stated range, so the
    # point-version rule does not then second-guess it.
    version_range_checked: set[str] = field(default_factory=set)
    # Multiplier on the semantic distance. >1 pushes a result down the
    # ranking; it never removes it.
    rank_penalty: float = 1.0

    def notes(self) -> list[str]:
        out: list[str] = []
        if self.component_overlap:
            out.append(f"same component ({', '.join(sorted(self.component_overlap))})")
        if self.component_conflict:
            article, target = self.component_conflict
            # Demoting without saying why leaves a reviewer unable to
            # tell a low-ranked article from an irrelevant one.
            out.append(f"covers {article}; incident is about {target}")
        if self.deployment_conflict:
            article, target = self.deployment_conflict
            out.append(
                f"written for the {_DEPLOYMENT_LABELS[article]} deployment; "
                f"this estate is {_DEPLOYMENT_LABELS[target]} — the steps may "
                f"not exist there"
            )
        if self.environment_conflict:
            article, target = self.environment_conflict
            out.append(f"scoped to {article}; incident is in {target}")
        if self.version_conflict:
            article, target = self.version_conflict
            where = f" ({self.version_environment})" if self.version_environment else ""
            if self.version_ahead_of_environment:
                out.append(
                    f"written for version {article}, but this environment"
                    f"{where} runs {target} — steps may reference features it "
                    f"does not have yet"
                )
            else:
                out.append(
                    f"written for version {article}; environment{where} runs "
                    f"{target}"
                )
        if self.platform_conflict:
            article, target = self.platform_conflict
            out.append(f"describes {article}; environment is {target}")
        return out


_DEPLOYMENT_LABELS = {"cloud": "cloud/SaaS", "onprem": "on-premise/self-hosted"}


def normalize_environment(label: str | None) -> str | None:
    """A tenant's own environment label mapped onto a tier.

    Returns ``None`` for anything unrecognised rather than guessing: an
    unmapped label compared against a tier would manufacture conflicts
    out of a naming convention nobody told us about.
    """
    key = re.sub(r"[^a-z0-9]+", " ", (label or "").lower()).strip()
    if not key:
        return None

    # "Non-Prod" is a negation, not a tier. It says which environment
    # this is NOT, and naming no tier is the honest answer — mapping it
    # to production would be exactly inverted, and mapping it to a guess
    # would pick a version from the wrong environment.
    if _NEGATED_ENVIRONMENT_RE.search(key):
        return None

    # Instance numbers are not tiers: QA2 and QA are the same tier.
    key = re.sub(r"\b([a-z]+)\d+\b", r"\1", key).strip()
    if key in _ENVIRONMENT_ALIASES:
        return _ENVIRONMENT_ALIASES[key]

    # Longest phrase first, so "Pre-Prod EU" resolves to staging rather
    # than matching the "prod" inside it. A qualifier that inverts the
    # meaning always contains the word it qualifies.
    tokens = key.split()
    for alias in _ENVIRONMENT_PHRASES:
        if alias in tokens or f" {alias} " in f" {key} ":
            return _ENVIRONMENT_ALIASES[alias]
    return None


def normalize_component(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


async def tenant_vocabulary(db, tenant_id) -> set[str]:
    """Component names this tenant's own data already knows about.

    Sourced from the entity graph ContextEdge builds from tickets — CIs,
    business services, assignment groups — so the vocabulary is whatever
    the tenant actually runs, and grows as their estate does. No product
    list to maintain and nothing to configure on day one.
    """
    from sqlalchemy import select

    from contextedge.models.entity import Entity

    try:
        names = (
            (
                await db.execute(
                    select(Entity.name).where(Entity.tenant_id == tenant_id).limit(2000)
                )
            )
            .scalars()
            .all()
        )
    except Exception:  # noqa: BLE001 - vocabulary is an enhancement, not a gate
        return set()

    vocabulary: set[str] = set()
    for name in names:
        normalized = normalize_component(str(name))
        # Single short tokens ("db", "01") match everything; hostnames
        # with digits identify one machine, not a component class.
        if len(normalized) >= MIN_TOKEN_CHARS and not normalized.isdigit():
            vocabulary.add(normalized)

    vocabulary |= await _products_named_in_corpus(db, tenant_id)
    return vocabulary


async def _products_named_in_corpus(db, tenant_id) -> set[str]:
    """Product names harvested from the tenant's own knowledge corpus.

    The entity graph is the better source but it is only as rich as the
    connectors that filled it. Measured on a live tenant, it held five
    entities — three hostnames and two assignment groups — while the
    articles themselves discussed a dozen components by name. The
    component facet was structurally correct and completely inert.

    So take the one lexical position that identifies a product with
    near-certainty: the token immediately before a version number.
    "aeagent 7.5.1", "activemq-client 5.15.8", "Tomcat 9.0.65" — whatever
    sits there is a product, in any corpus, for any vendor, with no list
    to maintain. It bootstraps from nothing and sharpens as more of the
    tenant's own documentation lands.
    """
    from sqlalchemy import select

    from contextedge.models.evidence import EvidenceItem
    from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES

    try:
        rows = (
            await db.execute(
                select(EvidenceItem.title, EvidenceItem.body_text)
                .where(
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.evidence_type.in_(KNOWLEDGE_EVIDENCE_TYPES),
                )
                .limit(CORPUS_VOCABULARY_DOCS)
            )
        ).all()
    except Exception:  # noqa: BLE001 - an enhancement, never a gate
        return set()

    products: set[str] = set()
    for title, body in rows:
        text = f"{title or ''}\n{(body or '')[:CORPUS_VOCABULARY_CHARS]}"
        products |= set(extract_applicability(text).product_versions)
    return {p for p in products if len(p) >= MIN_TOKEN_CHARS}


async def tenant_environment_inventory(db, tenant_id) -> dict[str, dict[str, str]]:
    """What version of what runs in which environment, per tenant.

    ``{"production": {"jira": "9.4"}, "qa": {"jira": "9.12"}}``

    Built from the entity graph rather than configured, for the same
    reason the component vocabulary is: an estate map that has to be
    maintained by hand is one that is wrong within a quarter. ``Entity``
    already carries ``environment`` as a first-class column and version
    facts in ``os_version``/``attributes``, because the CMDB and ticket
    connectors populate them.

    Returns ``{}`` on any failure — the caller then compares against
    whatever the ticket and prose state, which is the behaviour that
    shipped before this existed.
    """
    from sqlalchemy import select

    from contextedge.models.entity import Entity

    try:
        rows = (
            await db.execute(
                select(
                    Entity.name,
                    Entity.environment,
                    Entity.os_version,
                    Entity.attributes,
                )
                .where(
                    Entity.tenant_id == tenant_id,
                    Entity.environment.is_not(None),
                    Entity.is_active.is_(True),
                )
                .limit(2000)
            )
        ).all()
    except Exception:  # noqa: BLE001 - an enhancement, never a gate
        return {}

    inventory: dict[str, dict[str, str]] = {}
    for name, environment, os_version, attributes in rows:
        tier = normalize_environment(environment)
        if not tier:
            continue
        component = normalize_component(str(name or ""))
        if len(component) < MIN_TOKEN_CHARS or component.isdigit():
            continue

        version = None
        for candidate in (
            (attributes or {}).get("version") if isinstance(attributes, dict) else None,
            (attributes or {}).get("product_version")
            if isinstance(attributes, dict)
            else None,
            os_version,
        ):
            found = _LOOSE_VERSION_RE.match(str(candidate or ""))
            if found:
                version = found.group(1)
                break
        if version is None:
            continue

        # Keyed on the last token so it lines up with how prose versions
        # are keyed ("Jira Software" and "jira" resolve alike).
        inventory.setdefault(tier, {})[component.split()[-1]] = version
    return inventory


def extract_applicability(
    text: str | None, vocabulary: set[str] | None = None
) -> Applicability:
    """Facets stated anywhere in the text.

    ``vocabulary`` is the tenant's component terms. Without it the
    component facet is empty and only version and platform apply — which
    degrades to today's behaviour rather than to wrong answers.

    Deliberately lexical: a model call per article would be more precise
    and would cost a call per article per sync, and these are stable
    proper nouns rather than open-ended prose.
    """
    raw = text or ""
    blob = raw.lower()
    if not blob.strip():
        return Applicability()

    components = {
        term for term in (vocabulary or set()) if term and term in blob
    }
    platforms = {
        canonical
        for canonical, aliases in _PLATFORMS.items()
        if any(alias in blob for alias in aliases)
    }
    deployments = {
        canonical
        for canonical, aliases in _DEPLOYMENTS.items()
        if any(alias in blob for alias in aliases)
    }
    environments = {
        tier
        for tier, patterns in _ENVIRONMENT_RES.items()
        if any(pattern.search(blob) for pattern in patterns)
    }

    product_versions: dict[str, str] = {}
    versions: set[str] = set()
    for product, version in _PRODUCT_VERSION_RE.findall(raw):
        tokens = normalize_component(product).split()
        if not tokens:
            continue
        # The word immediately before the number governs what the number
        # counts. "Apache License 2.0" and "Version 2.0" are not the
        # product's release, so the whole match is rejected rather than
        # backed off to an earlier word.
        if tokens[-1] in _NON_PRODUCT_TOKENS:
            continue
        # Otherwise the FIRST meaningful token is the product, and later
        # ones qualify it: "activemq-client-5.15.8" is ActiveMQ, not some
        # component called "client". Keying on the last word produced
        # exactly that on live data — generic qualifiers entering the
        # vocabulary, where they match everything and mean nothing.
        named = [
            token
            for token in tokens
            if token not in _NON_PRODUCT_TOKENS and len(token) >= MIN_TOKEN_CHARS
        ]
        if not named:
            continue
        product_versions[named[0]] = version
        versions.add(version)

    return Applicability(
        components=components,
        platforms=platforms,
        versions=versions,
        product_versions=product_versions,
        deployments=deployments,
        environments=environments,
    )


MAX_EXTRACTION_CHARS = 12_000


async def extract_applicability_llm(
    title: str | None,
    body: str | None,
    *,
    tenant_id=None,
    db=None,
) -> Applicability | None:
    """Read an article's applicability with a model. ``None`` on failure.

    Runs ONCE per article at ingest and is persisted, which is what makes
    a model call affordable here — the cost is per document, not per
    document per retrieval.

    A model reads what the rules cannot. It knows an article telling you
    to edit a config file and restart a service is describing an
    on-premise install even when the word never appears; that
    "Apache License 2.0" is a licence; that 10.10.10.51 is a host. And it
    can represent "8.0 and later" as a floor rather than a point, which
    is the difference between correctly ranking an article and inventing
    a version conflict against it.

    Returns ``None`` rather than raising so the caller falls back to the
    lexical extractor: degraded applicability is the behaviour that
    shipped, a failed ingest is not.
    """
    from contextedge.ai.prompts import get_prompt
    from contextedge.ai.provider import llm_complete_json

    text = (body or "").strip()
    if not text and not (title or "").strip():
        return None

    prompt = get_prompt("knowledge_applicability", tenant_id)
    try:
        result = await llm_complete_json(
            prompt.format_user(
                title=title or "", body=text[:MAX_EXTRACTION_CHARS]
            ),
            task="extraction",
            system_prompt=prompt.system,
            tenant_id=tenant_id,
            db=db,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "applicability.llm_failed", error_type=type(exc).__name__
        )
        return None

    if not isinstance(result, dict):
        return None
    return applicability_from_payload(result)


def applicability_from_payload(payload: dict) -> Applicability:
    """Build an ``Applicability`` from extracted/persisted JSON.

    Shared by the model path and by rehydration from the database, so a
    stored record and a fresh extraction can never drift apart.
    """
    def _versions(key: str) -> dict[str, str]:
        raw = payload.get(key)
        if not isinstance(raw, dict):
            return {}
        out = {}
        for product, version in raw.items():
            token = normalize_component(str(product))
            found = _LOOSE_VERSION_RE.match(str(version or ""))
            if token and found:
                out[token] = found.group(1)
        return out

    def _terms(key: str, allowed: set[str] | None = None) -> set[str]:
        raw = payload.get(key)
        if not isinstance(raw, list):
            return set()
        terms = {normalize_component(str(v)) for v in raw}
        terms = {t for t in terms if len(t) >= MIN_TOKEN_CHARS}
        return {t for t in terms if t in allowed} if allowed else terms

    deployment = str(payload.get("deployment") or "unknown").lower()
    # "both" and "unknown" are stored as no constraint. Recording "both"
    # as {cloud, onprem} would make it overlap everything, which is the
    # same outcome by a more confusing route.
    deployments = {deployment} if deployment in ("cloud", "onprem") else set()

    product_versions = _versions("product_versions")
    return Applicability(
        components=_terms("components"),
        platforms=_terms("platforms", set(_PLATFORMS)),
        environments=_terms("environments", set(_ENVIRONMENTS)),
        deployments=deployments,
        product_versions=product_versions,
        versions=set(product_versions.values()),
        version_floor=_versions("version_floor"),
        version_ceiling=_versions("version_ceiling"),
        extracted_by="llm" if payload else "rules",
    )


def _version_tuple(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.split(".")[:3])
    except (ValueError, IndexError):
        return None


def compare(article: Applicability, target: Applicability) -> ApplicabilityMatch:
    """Line an article's stated applicability up against an environment.

    Only *stated* facets can count against an article.
    """
    match = ApplicabilityMatch()

    if article.components and target.components:
        overlap = article.components & target.components
        match.component_overlap = overlap
        if overlap:
            match.verdict = APPLIES
        else:
            # Both sides named their component and they disagree. The one
            # facet confident enough to demote on: two articles about
            # different subsystems share almost all their vocabulary.
            match.verdict = MISMATCH
            match.component_conflict = (
                ", ".join(sorted(article.components)[:3]),
                ", ".join(sorted(target.components)[:3]),
            )
            match.rank_penalty *= 1.6

    # Deployment. Nearly as heavy as a component mismatch, and for a
    # sharper reason: the article is about the right product but its
    # steps cannot be carried out. "Edit the config file and restart the
    # service" has no cloud equivalent, and no version of a cloud
    # article makes it applicable to a self-hosted estate.
    #
    # Requiring BOTH sides to be scoped to a single model is what makes
    # this safe to weight so heavily: an on-premise article that merely
    # mentions cloud in passing carries both markers, overlaps, and is
    # never demoted.
    if article.deployments and target.deployments:
        if not (article.deployments & target.deployments):
            match.deployment_conflict = (
                sorted(article.deployments)[0],
                sorted(target.deployments)[0],
            )
            match.verdict = MISMATCH
            match.rank_penalty *= 1.5
        elif match.verdict == UNKNOWN:
            match.verdict = APPLIES

    # Environment decides WHICH version to compare against before the
    # version check runs: a tenant is on 9.4 in production and 9.12 in
    # QA, so "does this article match our version" has no answer until
    # you know which environment the incident is in.
    if target.environments:
        match.version_environment = sorted(target.environments)[0]

    # Compare versions for the SAME product only. An article naming
    # Tomcat 9 is not in conflict with an environment on platform 7 —
    # they are versions of different things.
    #
    # Direction is the whole game here, and it is asymmetric:
    #
    #   article NEWER than the environment — the article documents a
    #     release this environment has not reached. The menu path, flag
    #     or endpoint it names may simply not exist yet. This is the
    #     damaging direction, and it is the one that shows up when a
    #     tenant runs 9.4 in production and 9.12 in QA: an article
    #     written against QA is actively wrong in production.
    #
    #   article OLDER than the environment — usually still fine. Most
    #     guidance survives an upgrade, and an older article is very
    #     often the only one that exists. Only a whole major release
    #     behind is worth flagging.
    #
    # Comparing majors alone missed this entirely: 9.4 and 9.12 share a
    # major and can be years and several feature releases apart.
    # Stated ranges are checked first and are authoritative: an article
    # that says "8.0 and later" has told us exactly what it covers, so a
    # 9.12 environment is INSIDE it. Falling through to the point-version
    # comparison would read 8.0 as "written for 8.0", call it a major
    # behind, and warn about an article that explicitly includes the
    # reader's release.
    for product, floor in sorted(article.version_floor.items()):
        running = target.product_versions.get(product)
        if not running:
            continue
        floor_v, running_v = _version_tuple(floor), _version_tuple(running)
        if floor_v and running_v and running_v < floor_v:
            match.version_conflict = (f"{floor}+", running)
            match.version_ahead_of_environment = True
            match.verdict = MISMATCH
            match.rank_penalty *= 1.35
        match.version_range_checked.add(product)

    for product, ceiling in sorted(article.version_ceiling.items()):
        running = target.product_versions.get(product)
        if not running:
            continue
        ceiling_v, running_v = _version_tuple(ceiling), _version_tuple(running)
        if ceiling_v and running_v and running_v > ceiling_v:
            match.version_conflict = (f"up to {ceiling}", running)
            match.verdict = MISMATCH
            match.rank_penalty *= 1.2
        match.version_range_checked.add(product)

    shared = (
        set(article.product_versions)
        & set(target.product_versions)
    ) - match.version_range_checked
    for product in sorted(shared):
        article_version = _version_tuple(article.product_versions[product])
        target_version = _version_tuple(target.product_versions[product])
        if article_version is None or target_version is None:
            continue
        if article_version == target_version:
            continue

        if article_version > target_version:
            penalty, ahead = 1.35, True
        elif article_version[0] != target_version[0]:
            penalty, ahead = 1.2, False
        else:
            continue  # article behind within the same major: still applies

        match.version_conflict = (
            article.product_versions[product],
            target.product_versions[product],
        )
        match.version_ahead_of_environment = ahead
        match.verdict = MISMATCH
        match.rank_penalty *= penalty
        break

    # Environment is a weak demoter on purpose. Most articles name an
    # environment incidentally ("in production you would also...") rather
    # than being scoped to it, so a disagreement is worth a note and a
    # nudge, not a real penalty. Its load-bearing job was choosing the
    # version above.
    if article.environments and target.environments:
        if not (article.environments & target.environments):
            match.environment_conflict = (
                ", ".join(sorted(article.environments)),
                ", ".join(sorted(target.environments)),
            )
            match.rank_penalty *= 1.1

    if article.platforms and target.platforms:
        if not (article.platforms & target.platforms):
            match.platform_conflict = (
                ", ".join(sorted(article.platforms)),
                ", ".join(sorted(target.platforms)),
            )
            match.rank_penalty *= 1.1
            if match.verdict == UNKNOWN:
                match.verdict = MISMATCH

    return match


def versions_from_custom_fields(
    custom_fields: dict | None, explicit_field: str | None = None
) -> set[str]:
    """Product version from a ticket's custom fields.

    The reliable source, and preferred over prose. A version typed into a
    dedicated field is an assertion about the environment; the same
    digits inside a stack trace are usually a *library* version and mean
    something else entirely.

    ``explicit_field`` wins when configured (``source_config
    ["version_field"]``). Otherwise any field whose name looks
    version-ish and whose value parses as a version is taken.
    """
    if not isinstance(custom_fields, dict):
        return set()

    if explicit_field:
        value = custom_fields.get(explicit_field)
        found = _LOOSE_VERSION_RE.match(str(value or ""))
        return {found.group(1)} if found else set()

    out: set[str] = set()
    for name, value in custom_fields.items():
        if not _VERSION_FIELD_RE.search(str(name)):
            continue
        found = _LOOSE_VERSION_RE.match(str(value or ""))
        if found:
            out.add(found.group(1))
    return out


_ENVIRONMENT_FIELD_RE = re.compile(
    r"(?:^|_)(?:environment|env|tier|stage|landscape)(?:$|_)", re.IGNORECASE
)


def _environment_from_custom_fields(
    custom_fields: dict | None, explicit_field: str | None = None
) -> str | None:
    """The environment tier from a ticket's custom fields.

    Same shape-matching as the version field, and for the same reason:
    the slug is per-portal, so "cf_environment", "cf_env" and
    "cf_target_tier" all have to work without configuration — while
    ``explicit_field`` covers the portal whose slug looks like nothing.
    """
    if not isinstance(custom_fields, dict):
        return None

    if explicit_field:
        return normalize_environment(str(custom_fields.get(explicit_field) or ""))

    for name, value in custom_fields.items():
        if _ENVIRONMENT_FIELD_RE.search(str(name)):
            tier = normalize_environment(str(value or ""))
            if tier:
                return tier
    return None


def describe_target(
    *,
    pattern_title: str | None = None,
    pattern_description: str | None = None,
    episode_summaries: list[dict] | None = None,
    ci_traits: dict | None = None,
    custom_fields: dict | None = None,
    version_field: str | None = None,
    vocabulary: set[str] | None = None,
    environment_inventory: dict[str, dict[str, str]] | None = None,
    environment_field: str | None = None,
    version_product: str | None = None,
) -> Applicability:
    """The environment an incident actually happened in.

    Sources in decreasing reliability:

    1. ``custom_fields`` — the version and environment fields on the
       ticket. Explicit assertions, and the only ones written by someone
       who knew which system they meant.
    2. ``environment_inventory`` — what the graph says runs in that
       environment. Resolves "which version" once the tier is known.
    3. ``ci_traits`` — observed facts about the host.
    4. Prose in the pattern and episodes — a last resort, because digits
       in a description are as likely to be a library version as the
       product's.
    """
    parts = [pattern_title or "", pattern_description or ""]
    for episode in (episode_summaries or [])[:5]:
        for key in ("title", "root_cause", "outcome"):
            value = episode.get(key)
            if isinstance(value, str):
                parts.append(value)

    applicability = extract_applicability("\n".join(parts), vocabulary)

    for key in ("os", "os_version", "product_version", "model", "manufacturer"):
        value = (ci_traits or {}).get(key)
        if isinstance(value, str) and value.strip():
            extra = extract_applicability(value, vocabulary)
            applicability.components |= extra.components
            applicability.platforms |= extra.platforms
            applicability.versions |= extra.versions
            applicability.product_versions.update(extra.product_versions)

    # The CI's own environment and deployment, which beat prose: the
    # graph records what the host IS, while a description records what
    # someone typed at 3am.
    ci_tier = normalize_environment((ci_traits or {}).get("environment"))
    if ci_tier:
        applicability.environments = {ci_tier}
    ci_deployment = extract_applicability(
        str((ci_traits or {}).get("deployment_model") or "")
    ).deployments
    if ci_deployment:
        applicability.deployments = ci_deployment

    # A ticket field naming the environment outranks both — it is the
    # one statement about this specific incident.
    field_tier = _environment_from_custom_fields(custom_fields, environment_field)
    if field_tier:
        applicability.environments = {field_tier}

    # With the tier known, the inventory says what actually runs there.
    # This is the point of the environment facet: an estate on 9.4 in
    # production and 9.12 in QA has no single "our version", and
    # comparing an article against the wrong one is worse than comparing
    # against nothing, because it produces a confident verdict.
    if environment_inventory:
        for tier in applicability.environments:
            for component, version in (environment_inventory.get(tier) or {}).items():
                applicability.product_versions.setdefault(component, version)
                applicability.versions.add(version)

    # Structured version last so it OVERRIDES anything scraped from
    # prose: a stack trace mentioning 7.4.1 must not outvote the field
    # where someone recorded that this ticket is about 7.5.1.
    field_versions = versions_from_custom_fields(custom_fields, version_field)
    if field_versions:
        applicability.versions = field_versions
        chosen = sorted(field_versions)[-1]
        # The field states the PLATFORM's version, and only the platform's.
        #
        # Applying it to every component named on the incident looked
        # reasonable and was wrong: bundled components version
        # independently. On live data a ticket stamped "7.5.1" was
        # compared against an article about ActiveMQ 5.15.8 and reported
        # a version conflict, when 5.15.8 was simply ActiveMQ's own
        # numbering inside a 7.5.1 platform. A confident, wholly
        # fabricated warning.
        #
        # So it is keyed to the product it actually describes, when the
        # operator has said which that is. Otherwise it is recorded and
        # visible but compared against nothing, because there is no
        # generic way to know which component a bare version belongs to —
        # and a silent non-comparison beats an invented conflict.
        applicability.product_versions[
            normalize_component(version_product) if version_product else PLATFORM_KEY
        ] = chosen

    return applicability
