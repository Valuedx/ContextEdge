"""Multimodal interpretation of document figures.

In support documentation a screenshot routinely carries information that
appears nowhere in the prose. Verified on the real KB corpus: an ActiveMQ
article's resolution section says "output similar to the image below",
and the image is a config file containing ``admin readwrite`` /
``monitorRole readonly`` / ``controlRole readwrite`` — the actual values
an engineer needs, present only as pixels. Without this pass those
articles are unanswerable.

**Multimodal model, no OCR engine.** OCR can report which words appear;
it cannot say which button is being clicked, what the navigation path
was, whether a state is before or after, or that a red icon means the
step failed. Since a model is required for the parts that matter, a
second OCR dependency would add a component that answers only the easy
half. Where a scanned page must be read, the same model reads it — and
the result is marked ``vision``, never passed off as parsed text.

**Cost is the design constraint, not accuracy.** A folder of 47 SOPs at
one vision call per page would exhaust a tenant's daily LLM budget in a
single upload. So the pass is:

- **selective** — only figures the parser flagged, which are already
  filtered by area, never whole pages that carry a text layer;
- **bounded** — ``MAX_FIGURES_PER_DOCUMENT`` per file, with the excess
  logged rather than silently dropped;
- **metered** — every call goes through ``ai/provider.llm_complete``
  with ``tenant_id`` + ``db``, so the per-tenant budget gate and
  ``/admin/cost`` see vision spend like any other;
- **fail-soft** — a figure that cannot be interpreted keeps its
  placeholder. Losing an interpretation degrades a document; raising
  would lose the document.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any

import structlog

from contextedge.services.documents.base import (
    ELEMENT_FIGURE,
    EXTRACTION_VISION,
    DocumentElement,
)

logger = structlog.get_logger()

# Per-document ceiling. A document with more figures than this gets its
# first N interpreted and the rest logged — a bound that is announced is
# a known limitation; a bound that is silent reads as "there were only N".
MAX_FIGURES_PER_DOCUMENT = 20

# Render resolution. High enough that UI labels and small config text are
# legible, low enough that a full-width screenshot stays well inside the
# provider's per-image budget.
RENDER_RESOLUTION = 110

# Skip figures too small to carry readable content — icons, rules, logos.
# The parser already filters at 5,000 px²; this is the second gate after
# rendering, when the true pixel size is known.
MIN_RENDERED_BYTES = 2_000

VISION_SYSTEM_PROMPT = """\
You describe figures from IT support documentation so an engineer who \
cannot see the image can still follow the procedure.

Report only what is visibly present. Never infer a value that is not \
shown, and never complete a partially visible string — a fabricated \
hostname or credential is worse than an absent one.

Respond with JSON only:
{
  "figure_type": "ui_screenshot" | "terminal" | "config_file" | "diagram" | "log_output" | "other",
  "summary": "one sentence on what the figure shows",
  "visible_text": "text legible in the image, preserving line structure",
  "application": "product or screen name if visible, else null",
  "actions": ["navigation or click steps the figure demonstrates"],
  "warnings": ["any error or warning text visible"],
  "contains_sensitive": true | false,
  "confidence": 0.0-1.0
}"""


@dataclass(slots=True)
class FigureInterpretation:
    summary: str
    visible_text: str = ""
    figure_type: str = "other"
    application: str | None = None
    actions: list[str] | None = None
    warnings: list[str] | None = None
    contains_sensitive: bool = False
    confidence: float = 0.0

    def to_text(self) -> str:
        """Renderable body for the element, ordered by usefulness."""
        parts: list[str] = [f"[figure: {self.summary}]"]
        if self.application:
            parts.append(f"Screen: {self.application}")
        if self.actions:
            parts.extend(f"- {a}" for a in self.actions)
        if self.visible_text.strip():
            parts.append(self.visible_text.strip())
        if self.warnings:
            parts.extend(f"Warning: {w}" for w in self.warnings)
        return "\n".join(parts)


def render_figure(pdf_bytes: bytes, element: DocumentElement) -> bytes | None:
    """Crop a figure's region out of its page and render it to PNG.

    Returns ``None`` when the element has no usable box or the render is
    too small to be worth a model call.
    """
    if element.bounding_box is None or element.page_number is None:
        return None

    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if element.page_number > len(pdf.pages):
                return None
            page = pdf.pages[element.page_number - 1]
            # Clamp to the page: a bounding box that overflows raises
            # rather than rendering, and some generators emit boxes a
            # fraction of a point outside the media box.
            x0, top, x1, bottom = element.bounding_box
            box = (
                max(0, x0),
                max(0, top),
                min(float(page.width), x1),
                min(float(page.height), bottom),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                return None
            image = page.crop(box).to_image(resolution=RENDER_RESOLUTION)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data = buffer.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "document.figure_render_failed",
            page=element.page_number,
            error_type=type(exc).__name__,
        )
        return None

    return data if len(data) >= MIN_RENDERED_BYTES else None


async def interpret_figure(
    image: bytes,
    *,
    context: str,
    tenant_id: Any,
    db: Any,
) -> FigureInterpretation | None:
    """One metered vision call. ``None`` on any failure."""
    from contextedge.ai.provider import llm_complete

    prompt = (
        "Describe this figure from a support article.\n\n"
        f"Surrounding context: {context[:500]}\n\nRespond with JSON only."
    )
    try:
        raw = await llm_complete(
            prompt,
            task="extraction",
            temperature=0.0,
            max_tokens=1200,
            system_prompt=VISION_SYSTEM_PROMPT,
            tenant_id=tenant_id,
            db=db,
            prompt_name="document_figure_vision",
            prompt_version="v1",
            images=[image],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "document.figure_vision_failed", error_type=type(exc).__name__
        )
        return None

    return _parse_interpretation(raw)


def _parse_interpretation(raw: str) -> FigureInterpretation | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Models fence JSON despite instructions; strip it before parsing.
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    summary = str(data.get("summary") or "").strip()
    if not summary:
        return None

    def _list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value[:12] if str(v).strip()]
        return []

    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return FigureInterpretation(
        summary=summary[:500],
        visible_text=str(data.get("visible_text") or "")[:4000],
        figure_type=str(data.get("figure_type") or "other")[:40],
        application=(str(data["application"])[:120] if data.get("application") else None),
        actions=_list(data.get("actions")),
        warnings=_list(data.get("warnings")),
        contains_sensitive=bool(data.get("contains_sensitive")),
        confidence=max(0.0, min(1.0, confidence)),
    )


async def interpret_document_figures(
    elements: list[DocumentElement],
    pdf_bytes: bytes,
    *,
    tenant_id: Any,
    db: Any,
) -> dict:
    """Interpret a document's figures in place.

    Mutates the figure elements: text becomes the interpretation,
    ``extraction_method`` becomes ``vision``, and confidence reflects the
    model's own. Returns counts for the caller to log.
    """
    figures = [
        e
        for e in elements
        if e.element_type == ELEMENT_FIGURE
        and (e.structured_content or {}).get("needs_vision")
    ]
    counts = {
        "figures": len(figures),
        "interpreted": 0,
        "skipped_render": 0,
        "failed": 0,
        "over_limit": max(0, len(figures) - MAX_FIGURES_PER_DOCUMENT),
        "sensitive": 0,
    }
    if counts["over_limit"]:
        logger.info(
            "document.figures_over_limit",
            total=len(figures),
            limit=MAX_FIGURES_PER_DOCUMENT,
        )

    for element in figures[:MAX_FIGURES_PER_DOCUMENT]:
        image = render_figure(pdf_bytes, element)
        if image is None:
            counts["skipped_render"] += 1
            continue

        context = " > ".join(element.section_path) or ""
        interpretation = await interpret_figure(
            image, context=context, tenant_id=tenant_id, db=db
        )
        if interpretation is None:
            counts["failed"] += 1
            continue

        element.text = interpretation.to_text()
        element.extraction_method = EXTRACTION_VISION
        element.confidence = interpretation.confidence
        element.structured_content = {
            **(element.structured_content or {}),
            "needs_vision": False,
            "figure_type": interpretation.figure_type,
            "application": interpretation.application,
            "actions": interpretation.actions or [],
            "warnings": interpretation.warnings or [],
            # Surfaced, not acted on: screenshots routinely show
            # usernames, hostnames, and tokens. Redaction runs over the
            # rendered text downstream; this flags the element so an
            # operator can find what was pulled out of an image.
            "contains_sensitive": interpretation.contains_sensitive,
        }
        counts["interpreted"] += 1
        if interpretation.contains_sensitive:
            counts["sensitive"] += 1

    return counts
