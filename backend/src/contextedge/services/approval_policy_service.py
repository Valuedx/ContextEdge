"""Evaluation of playbook approval policies.

A playbook may reference a ``TenantPolicy`` row with
``policy_type == "approval"``. Until now that reference was validated at
playbook create/update time but never *evaluated* — ``start_execution``
derived gating purely from roles and automation mode, so two-person rules
or self-approval bans configured by a tenant had no effect.

Recognised ``config`` keys (all optional; unknown keys are ignored so
tenants can carry vendor-specific metadata):

- ``approver_roles``: list[str] — the decider must hold at least one of
  these roles to approve/deny gated steps.
- ``forbid_self_approval``: bool — the run initiator may not decide
  approvals on their own run.
- ``require_approval_min_safety_class``: str — steps at or above this
  safety class always require approval, regardless of the caller's
  role-derived cap.
- ``max_automation_mode``: str — the playbook may not execute in a more
  autonomous mode than this (order per ``AUTOMATION_MODES``).

A dangling, inactive, or wrong-type policy reference fails closed with
``ApprovalPolicyViolation`` — a broken governance pointer must never
silently disable governance.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.execution import SAFETY_CLASSES
from contextedge.models.playbook import AUTOMATION_MODES
from contextedge.models.policy import TenantPolicy


class ApprovalPolicyViolation(RuntimeError):
    """The configured approval policy forbids the attempted action."""


@dataclass(slots=True)
class ApprovalPolicy:
    policy_id: uuid.UUID | None
    approver_roles: tuple[str, ...] = ()
    forbid_self_approval: bool = False
    require_approval_min_safety_class: str | None = None
    max_automation_mode: str | None = None
    # Carried so a recorded check keys on the policy VERSION rather than the
    # policy row (F3) — a later edit must not rewrite the history of what a
    # run was judged under.
    version: int | None = None

    @property
    def is_configured(self) -> bool:
        return self.policy_id is not None


NO_POLICY = ApprovalPolicy(policy_id=None)


async def load_approval_policy(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID | None,
) -> ApprovalPolicy:
    if policy_id is None:
        return NO_POLICY
    row = await db.get(TenantPolicy, policy_id)
    if (
        row is None
        or row.tenant_id != tenant_id
        or row.policy_type != "approval"
        or not row.is_active
    ):
        raise ApprovalPolicyViolation(
            f"Playbook references approval policy {policy_id} which is "
            "missing, inactive, or not an approval policy — refusing to "
            "execute without its governance rules"
        )
    config = row.config or {}
    min_class = config.get("require_approval_min_safety_class")
    if min_class is not None and min_class not in SAFETY_CLASSES:
        raise ApprovalPolicyViolation(
            f"Approval policy {policy_id} has unknown safety class {min_class!r}"
        )
    max_mode = config.get("max_automation_mode")
    if max_mode is not None and max_mode not in AUTOMATION_MODES:
        raise ApprovalPolicyViolation(
            f"Approval policy {policy_id} has unknown automation mode {max_mode!r}"
        )
    approver_roles = tuple(
        str(role) for role in (config.get("approver_roles") or []) if role
    )
    return ApprovalPolicy(
        policy_id=policy_id,
        approver_roles=approver_roles,
        forbid_self_approval=bool(config.get("forbid_self_approval", False)),
        require_approval_min_safety_class=min_class,
        max_automation_mode=max_mode,
        version=getattr(row, "version", None),
    )


def check_automation_mode(policy: ApprovalPolicy, automation_mode: str) -> None:
    if policy.max_automation_mode is None:
        return
    if AUTOMATION_MODES.index(automation_mode) > AUTOMATION_MODES.index(
        policy.max_automation_mode
    ):
        raise ApprovalPolicyViolation(
            f"Approval policy caps automation at "
            f"{policy.max_automation_mode!r}; playbook requests "
            f"{automation_mode!r}"
        )


def step_requires_policy_approval(policy: ApprovalPolicy, step_safety_class: str) -> bool:
    if policy.require_approval_min_safety_class is None:
        return False
    return SAFETY_CLASSES.index(step_safety_class) >= SAFETY_CLASSES.index(
        policy.require_approval_min_safety_class
    )


def check_decider(
    policy: ApprovalPolicy,
    *,
    decided_by: uuid.UUID,
    run_initiated_by: uuid.UUID | None,
    decider_roles: tuple[str, ...] | list[str] | None,
) -> None:
    if not policy.is_configured:
        return
    if (
        policy.forbid_self_approval
        and run_initiated_by is not None
        and decided_by == run_initiated_by
    ):
        raise ApprovalPolicyViolation(
            "Approval policy forbids the run initiator deciding their own approvals"
        )
    if policy.approver_roles:
        held = set(decider_roles or ())
        if not held.intersection(policy.approver_roles):
            raise ApprovalPolicyViolation(
                "Approval policy requires one of roles "
                f"{sorted(policy.approver_roles)} to decide this approval"
            )
