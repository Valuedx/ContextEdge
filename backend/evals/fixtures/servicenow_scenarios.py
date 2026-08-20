"""Purpose-built ServiceNow fixtures for the situation-intelligence roadmap.

A ServiceNow PDI ships ~600 randomly generated records. They are useful as
*noise* -- the adversarial control a correlator must not over-merge -- but they
encode no causality: no change precedes the incident it caused, no CI depends
on another, no incident is a duplicate of its neighbour. Correlation logic
validated against them proves the code runs, not that it works, which is
exactly the failure mode INCIDENT_DIAGNOSIS_ROADMAP warns about.

This module authors the scenarios that carry the causality, using the canonical
Acme VPN incident the codewiki is written around: KB5032190 breaking the
certificate chain on ``vpn-gw-east-01``, failing with ``AUTH_CERT_EXPIRED``,
first reported by jsmith@acme.com.

Every scenario states the assertion it exists to support:

  S1 cert-expiry     one change, one major incident, five duplicates, one
                     problem. H3 must group them into ONE situation; H6 must
                     rank CHG-A first; D3 must read parent_incident.
  S2 coincidental    an unrelated change on an unrelated CI inside the same
                     window. H6 must rank it BELOW CHG-A. This is the
                     precision test -- a correlator that fires on time
                     proximity alone passes S1 and fails here.
  S3 blast-radius    the incident is on a dependency; the symptoms are on the
                     dependent service. Same-CI matching cannot connect them;
                     only a topology walk can (C1/H4).
  S4 hub             four unrelated incidents on one shared domain controller.
                     H3 must NOT collapse them -- a shared CI is not a shared
                     occurrence.
  S5 problem-mgmt    a known error with a documented workaround, and three
                     incidents spread across three separate weeks that all
                     carry problem_id. These are RECURRENCE, not one
                     occurrence: H3 must not merge them into a single
                     situation the way it merges S1, and H8 must read them as
                     a repeating known error rather than a reopen. S1 and S5
                     together are what separate "many tickets, one occurrence"
                     from "many occurrences, one root cause".
  S6 request-lane    a requested item and its catalog task. Requests are not
                     incidents: nothing broke, and a facet that counts them as
                     operational history overstates load, while a pattern
                     miner fed requests learns "onboarding" as a failure mode.
                     Present so the request lane is distinguishable rather
                     than assumed absent.
  S7 knowledge-lane  two articles pulling in opposite directions on purpose.
                     One documents what actually resolved S1 and should attach
                     to the pattern those incidents form. The other recommends
                     the restart workaround that S5's three recurrences show
                     does not hold -- the knowledge-drift case, where
                     documented advice stands against observed outcomes.
  S8 change-window   a change approved for a weekend maintenance slot and
                     actually executed on a weekday morning, four days later,
                     with an incident on the same CI 35 minutes after it. Two
                     changes now touch vpn-gw-east-01, so ranking cannot just
                     answer "a change on this CI" -- it has to prefer the one
                     whose EXECUTION sits near the incident, and it can say
                     something stronger than proximity: this change ran
                     outside the window it was approved for.

                     Change freeze and maintenance schedules are modelled on
                     the change record rather than in cmn_schedule, because
                     this instance refuses schedule spans over REST
                     ("Schedule Item validate"). The approved window is
                     start_date/end_date and the actual execution is
                     work_start/work_end, so out-of-window is a comparison
                     between two fields on one record instead of a join
                     against a calendar -- which is also the only form
                     available on an ITSM source that does not publish its
                     freeze calendar.
  C2 ownership       criticality, owning team and accountable owner on the
                     fixture CIs. A stock PDI populates none of these -- 0 of
                     400 sampled CIs carry business criticality, owner or
                     environment -- so blast radius has nothing to prioritise
                     by. Note where criticality sits: ServiceNow defines
                     `busines_criticality` [sic, one 's'] on cmdb_ci_service
                     ONLY, not on the cmdb_ci base table. That is semantically
                     right -- a business service is critical, a switch is
                     critical because of what depends on it -- so the fixture
                     sets it on acme-vpn-service and lets the gateway inherit
                     it through the dependency edge rather than stamping it
                     everywhere.

Instance constraint (measured, 2026-08-21). ServiceNow guards the change and
problem lifecycles with model business rules -- "Change Model: Check State
Transition", "Problem Model: Check State Transition", "Abort changes on group",
"Prevent Type change to Standard" -- and every one of them rejects a
REST-driven state move, including on the initial insert. A change posted with
``state=3`` lands in ``New``; a problem posted with ``state=103`` is refused
outright. Walking the transitions one at a time does not help: each step is
refused in turn, with and without the model's mandatory planning fields.

So these fixtures do not set ``state`` or ``close_code`` on changes and
problems. What happened is expressed through the fields the instance does
accept -- ``start_date`` / ``end_date`` and ``work_start`` / ``work_end`` for
the execution window, ``description`` and ``workaround`` for the account -- and
those are what correlation reads anyway. A record asserting "state=New,
close_code=successful" would be incoherent, and incoherent fixtures are the
thing this module exists to avoid. Incidents are unaffected: their state and
close fields set normally.

Idempotent: every record is keyed by ``correlation_id`` (``ce-fix:<key>``), so
re-running updates in place rather than duplicating. ``--teardown`` deletes
exactly what the manifest records, and nothing else.

Usage::

    python -m evals.fixtures.servicenow_scenarios --build
    python -m evals.fixtures.servicenow_scenarios --teardown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

MANIFEST = Path(__file__).with_name("servicenow_manifest.json")
TAG = "ce-fix"

# Anchor T0: the change window. Everything else is expressed relative to it so
# the whole scenario always lands inside the 365-day backfill window. S5 walks
# 21 days backwards from t0; S3 reaches 6 days forward of it. The anchor has to
# leave room on the forward side or S3 lands in the future -- which it did on
# the first build, producing incidents opened two days from now.
MAX_FORWARD_DAYS = 7
DEFAULT_ANCHOR_DAYS_AGO = 10


def _fmt(dt: datetime) -> str:
    """ServiceNow REST datetime, UTC, display_value=false format."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


class Snow:
    """Thin ServiceNow Table API client with upsert-by-correlation-id."""

    def __init__(self, base: str, user: str, password: str) -> None:
        self.base = base.rstrip("/")
        self.auth = (user, password)
        self.client = httpx.Client(timeout=60.0)

    def _url(self, table: str, sys_id: str = "") -> str:
        return f"{self.base}/api/now/table/{table}" + (f"/{sys_id}" if sys_id else "")

    @staticmethod
    def _check(r: httpx.Response, what: str) -> httpx.Response:
        """Raise with ServiceNow's own explanation attached.

        A bare raise_for_status turns "Data Policy Exception: the following
        fields are mandatory: Assigned to" into "403 Forbidden", which is the
        difference between a two-minute fix and an afternoon.
        """
        if r.is_success:
            return r
        try:
            detail = r.json().get("error", {})
            msg = f"{detail.get('message', '')} {detail.get('detail', '')}".strip()
        except Exception:
            msg = r.text[:300]
        raise RuntimeError(f"{what} -> HTTP {r.status_code}: {msg}")

    def _first(self, table: str, query: str) -> str | None:
        r = self.client.get(
            self._url(table),
            auth=self.auth,
            params={
                "sysparm_query": query,
                "sysparm_fields": "sys_id",
                "sysparm_limit": "1",
            },
        )
        self._check(r, f"GET {table} ({query})")
        rows = r.json().get("result", [])
        return rows[0]["sys_id"] if rows else None

    def _find_verified(self, table: str, field: str, value: str) -> str | None:
        """Find one record by field=value, and verify the match.

        ServiceNow does not reject an unknown field in ``sysparm_query`` -- it
        drops that term and answers the *rest* of the query. A lookup on a
        field the table does not have therefore matches EVERY row and returns
        an arbitrary one, and an upsert built on it overwrites a stranger's
        record while reporting success. That is not hypothetical: keying
        kb_knowledge on correlation_id (which that table lacks) selected an
        unrelated demo article, and only an ACL stopped the write.

        So the match is verified: the field is read back and compared. A
        response that does not carry the field at all means the table cannot
        be keyed this way, and that is an error, not a miss.
        """
        r = self.client.get(
            self._url(table),
            auth=self.auth,
            params={
                "sysparm_query": f"{field}={value}",
                "sysparm_fields": f"sys_id,{field}",
                "sysparm_limit": "1",
            },
        )
        self._check(r, f"GET {table} ({field}={value})")
        rows = r.json().get("result", [])
        if not rows:
            return None
        row = rows[0]
        if field not in row:
            raise RuntimeError(
                f"{table} has no field {field!r}: the query was silently ignored "
                f"and matched an unrelated record ({row.get('sys_id')}). "
                f"Key this table on a field it actually has."
            )
        got = row.get(field)
        got = got.get("value") if isinstance(got, dict) else got
        if got != value:
            return None
        return row["sys_id"]

    def upsert(
        self,
        table: str,
        key: str,
        payload: dict[str, Any],
        key_field: str = "correlation_id",
    ) -> str:
        """Create or update, keyed on ``key_field``. Returns sys_id.

        ``key_field`` exists because not every table carries correlation_id --
        kb_knowledge does not -- and guessing wrong is a silent overwrite
        rather than a failure. See ``_find_verified``.
        """
        body = dict(payload)
        value = f"{TAG}:{key}"
        if key_field == "correlation_id":
            body["correlation_id"] = value
        else:
            value = str(payload.get(key_field, ""))
            if not value:
                raise RuntimeError(
                    f"{table} keyed on {key_field!r} but the payload has no value "
                    f"for it; the lookup would match everything."
                )
        existing = self._find_verified(table, key_field, value)
        if existing:
            r = self.client.put(self._url(table, existing), auth=self.auth, json=body)
            self._check(r, f"PUT {table} {key}")
            return existing
        r = self.client.post(self._url(table), auth=self.auth, json=body)
        self._check(r, f"POST {table} {key}")
        return r.json()["result"]["sys_id"]

    def upsert_ci(self, table: str, name: str, payload: dict[str, Any]) -> str:
        """CI tables key on ``name`` -- it is what the connector resolves
        entities by, and what a human reads in a blast-radius answer."""
        body = dict(payload)
        body["name"] = name
        existing = self._first(table, f"name={name}")
        if existing:
            r = self.client.put(self._url(table, existing), auth=self.auth, json=body)
            self._check(r, f"PUT {table} {name}")
            return existing
        r = self.client.post(self._url(table), auth=self.auth, json=body)
        self._check(r, f"POST {table} {name}")
        return r.json()["result"]["sys_id"]

    def default_assignee(self) -> str:
        """sys_id of the account these fixtures are authored by.

        Problem records need an assignee to leave state New, and a fixture
        that hardcodes a sys_id breaks on the next instance.
        """
        found = self._first("sys_user", "user_name=admin")
        if not found:
            raise RuntimeError("cannot resolve an assignee: no admin user")
        return found

    def lookup(self, table: str, query: str) -> str | None:
        """Resolve a sys_id the fixture must not hardcode.

        Every reference below is looked up by a human-readable key, because a
        sys_id copied from one instance is a silent mis-reference on the next:
        ServiceNow stores an unresolvable reference as empty rather than
        rejecting it, so the fixture would build clean and mean nothing.
        """
        return self._first(table, query)

    def rel_type(self, name: str) -> str | None:
        return self._first("cmdb_rel_type", f"name={name}")

    def upsert_rel(self, parent: str, child: str, type_sys_id: str) -> str:
        existing = self._first(
            "cmdb_rel_ci", f"parent={parent}^child={child}^type={type_sys_id}"
        )
        if existing:
            return existing
        r = self.client.post(
            self._url("cmdb_rel_ci"),
            auth=self.auth,
            json={"parent": parent, "child": child, "type": type_sys_id},
        )
        self._check(r, "POST cmdb_rel_ci")
        return r.json()["result"]["sys_id"]

    def delete(self, table: str, sys_id: str) -> bool:
        r = self.client.delete(self._url(table, sys_id), auth=self.auth)
        if r.status_code in (200, 204):
            return True
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return False


def build(sn: Snow, anchor: datetime) -> dict[str, Any]:
    created: dict[str, Any] = {"records": [], "relationships": []}

    def note(table: str, key: str, sys_id: str) -> str:
        created["records"].append({"table": table, "key": key, "sys_id": sys_id})
        return sys_id

    t0 = anchor  # the change lands
    # Problem records need a real assignee (see the Data Policy note below).
    assignee = sn.default_assignee()
    grp_network = sn.lookup("sys_user_group", "name=Network")
    grp_servicedesk = sn.lookup("sys_user_group", "name=Service Desk")
    kb_known_error = sn.lookup("kb_knowledge_base", "title=Known Error")
    kb_it = sn.lookup("kb_knowledge_base", "title=IT")
    cat_laptop = sn.lookup("sc_cat_item", "name=Standard Laptop")

    # ---- CIs -------------------------------------------------------------
    # A chain deep enough that blast radius needs more than a same-CI match:
    #   acme-vpn-service -> vpn-gw-east-01 -> radius-auth-01
    #                       vpn-gw-east-01 -> esx-host-04 (runs on)
    ci: dict[str, str] = {}
    ci["vpn_gw"] = note(
        "cmdb_ci_netgear",
        "ci:vpn-gw-east-01",
        sn.upsert_ci(
            "cmdb_ci_netgear",
            "vpn-gw-east-01",
            {
                "owned_by": assignee,
                "support_group": grp_network,
                "short_description": "Acme east-region VPN concentrator",
                "operational_status": "1",
                "install_status": "1",
            },
        ),
    )
    ci["radius"] = note(
        "cmdb_ci_server",
        "ci:radius-auth-01",
        sn.upsert_ci(
            "cmdb_ci_server",
            "radius-auth-01",
            {
                "owned_by": assignee,
                "support_group": grp_network,
                "short_description": "RADIUS authentication server (VPN back end)",
                "operational_status": "1",
                "install_status": "1",
            },
        ),
    )
    ci["esx"] = note(
        "cmdb_ci_server",
        "ci:esx-host-04",
        sn.upsert_ci(
            "cmdb_ci_server",
            "esx-host-04",
            {
                "owned_by": assignee,
                "support_group": grp_network,
                "short_description": "ESX host carrying the east-region network VMs",
                "operational_status": "1",
                "install_status": "1",
            },
        ),
    )
    ci["vpn_service"] = note(
        "cmdb_ci_service",
        "ci:acme-vpn-service",
        sn.upsert_ci(
            "cmdb_ci_service",
            "acme-vpn-service",
            {
                "short_description": "Remote access VPN business service",
                "operational_status": "1",
                "install_status": "1",
                # `busines_criticality` is ServiceNow's own spelling, single
                # 's'. It exists on cmdb_ci_service and not on cmdb_ci, so
                # this is the only fixture CI that can carry it.
                "busines_criticality": "1 - most critical",
                "owned_by": assignee,
                "support_group": grp_network,
            },
        ),
    )
    ci["print"] = note(
        "cmdb_ci_server",
        "ci:print-srv-02",
        sn.upsert_ci(
            "cmdb_ci_server",
            "print-srv-02",
            {
                "owned_by": assignee,
                "support_group": grp_servicedesk,
                "short_description": "Floor-2 print server (S2 control CI)",
                "operational_status": "1",
                "install_status": "1",
            },
        ),
    )
    ci["dc"] = note(
        "cmdb_ci_server",
        "ci:acme-ad-dc-01",
        sn.upsert_ci(
            "cmdb_ci_server",
            "acme-ad-dc-01",
            {
                "owned_by": assignee,
                "support_group": grp_servicedesk,
                "short_description": "Active Directory domain controller (S4 hub CI)",
                "operational_status": "1",
                "install_status": "1",
            },
        ),
    )

    # ---- CI relationships ------------------------------------------------
    depends_on = sn.rel_type("Depends on::Used by")
    runs_on = sn.rel_type("Runs on::Runs")
    if depends_on:
        for parent, child in (
            (ci["vpn_service"], ci["vpn_gw"]),
            (ci["vpn_gw"], ci["radius"]),
        ):
            created["relationships"].append(
                {
                    "table": "cmdb_rel_ci",
                    "sys_id": sn.upsert_rel(parent, child, depends_on),
                }
            )
    if runs_on:
        created["relationships"].append(
            {
                "table": "cmdb_rel_ci",
                "sys_id": sn.upsert_rel(ci["vpn_gw"], ci["esx"], runs_on),
            }
        )

    # ---- S1: the change that caused it -----------------------------------
    chg_a = note(
        "change_request",
        "s1:chg-kb5032190",
        sn.upsert(
            "change_request",
            "s1:chg-kb5032190",
            {
                "short_description": (
                    "Deploy Windows update KB5032190 to VPN gateway fleet"
                ),
                "description": (
                    "Monthly security rollup KB5032190 applied to the east-region "
                    "VPN concentrators. Standard patch window. The update was "
                    "applied to vpn-gw-east-01 and the gateway rebooted cleanly. "
                    "Post-change validation was limited to ICMP reachability and an "
                    "admin console login -- no client handshake was tested, which is "
                    "why the certificate regression was not caught in the window."
                ),
                "type": "normal",
                "category": "Software",
                "cmdb_ci": ci["vpn_gw"],
                # See the instance-constraint note in the module docstring:
                # state and close_code are not settable over REST here.
                "start_date": _fmt(t0 - timedelta(minutes=30)),
                "end_date": _fmt(t0),
                "work_start": _fmt(t0 - timedelta(minutes=30)),
                "work_end": _fmt(t0),
            },
        ),
    )

    # ---- S2: the coincidental change (precision control) -----------------
    chg_b = note(
        "change_request",
        "s2:chg-print-firmware",
        sn.upsert(
            "change_request",
            "s2:chg-print-firmware",
            {
                "short_description": "Firmware refresh on print-srv-02",
                "description": (
                    "Routine printer firmware refresh. Unrelated to the VPN estate "
                    "-- present so change correlation has to discriminate on more "
                    "than 'a change happened near this time'."
                ),
                # `type` is omitted deliberately: a third business rule,
                # "Prevent Type change to Standard", blocks setting it on
                # update, and the change type is not what S2 controls for --
                # the discriminator is the CI, not the change class.
                "category": "Hardware",
                "cmdb_ci": ci["print"],
                "start_date": _fmt(t0 - timedelta(minutes=10)),
                "end_date": _fmt(t0 + timedelta(minutes=15)),
                "work_start": _fmt(t0 - timedelta(minutes=10)),
                "work_end": _fmt(t0 + timedelta(minutes=15)),
            },
        ),
    )

    # ---- S1: the problem -------------------------------------------------
    prb = note(
        "problem",
        "s1:prb-cert-chain",
        sn.upsert(
            "problem",
            "s1:prb-cert-chain",
            {
                "short_description": (
                    "KB5032190 breaks the VPN certificate chain (AUTH_CERT_EXPIRED)"
                ),
                "description": (
                    "KB5032190 hardens certificate chain validation. The intermediate "
                    "CA certificate on vpn-gw-east-01 was issued with a legacy "
                    "signature algorithm the hardened validator rejects, so every "
                    "client handshake terminates with AUTH_CERT_EXPIRED even though "
                    "the leaf certificate is inside its validity window."
                ),
                "cmdb_ci": ci["vpn_gw"],
                "rfc": chg_a,
                # A Data Policy makes assigned_to mandatory for any state past
                # New (101); omitting it returns 403, not a validation warning.
                "assigned_to": assignee,
            },
        ),
    )

    # ---- S1: the major incident ------------------------------------------
    inc_major = note(
        "incident",
        "s1:inc-major",
        sn.upsert(
            "incident",
            "s1:inc-major",
            {
                "short_description": (
                    "VPN authentication failing for remote users - AUTH_CERT_EXPIRED"
                ),
                "description": (
                    "Remote users cannot establish a VPN session to vpn-gw-east-01. "
                    "The client reports AUTH_CERT_EXPIRED and the gateway log shows "
                    "certificate chain validation failure on every handshake attempt. "
                    "Started shortly after the KB5032190 patch window. First reported "
                    "by jsmith@acme.com. Approximately 200 remote staff affected."
                ),
                "cmdb_ci": ci["vpn_gw"],
                "caused_by": chg_a,
                "problem_id": prb,
                "assignment_group": grp_network,
                "category": "Network",
                "priority": "1",
                "impact": "1",
                "urgency": "1",
                "state": "6",
                # `close_code` is the field the Data Policy calls
                # "Resolution code". Its values are a bounded choice list --
                # an off-list value is stored as empty and then trips the
                # policy as a missing mandatory field, which reads as a
                # permissions error and is not one.
                "close_code": "Solution provided",
                "close_notes": (
                    "Root cause: KB5032190 enforces stricter certificate chain "
                    "validation and rejected the legacy intermediate CA. Fix: "
                    "reissued the intermediate CA certificate with SHA-256 and "
                    "reloaded the gateway trust store. Verified with a test client "
                    "handshake before reopening access. Rolling KB5032190 back was "
                    "considered and rejected -- it reintroduces the security exposure "
                    "the update closes."
                ),
                "opened_at": _fmt(t0 + timedelta(minutes=40)),
                "resolved_at": _fmt(t0 + timedelta(hours=5)),
            },
        ),
    )

    # ---- S1: the five duplicates -----------------------------------------
    children = [
        (
            "s1:inc-child-1",
            45,
            "Cannot connect to VPN this morning",
            "VPN client just spins and then fails. It says the certificate expired "
            "but mine renewed last month. jsmith@acme.com",
        ),
        (
            "s1:inc-child-2",
            62,
            "VPN error AUTH_CERT_EXPIRED",
            "Getting AUTH_CERT_EXPIRED when connecting from home. Worked yesterday.",
        ),
        (
            "s1:inc-child-3",
            88,
            "Remote access down for the whole sales team",
            "Nobody on the sales floor can get onto the VPN. Same certificate error "
            "for all of us.",
        ),
        (
            "s1:inc-child-4",
            140,
            "VPN not working after Windows update",
            "Laptop installed updates overnight and now the VPN refuses to connect.",
        ),
        (
            "s1:inc-child-5",
            180,
            "Unable to reach internal apps from home",
            "No VPN, so no access to any internal application. The VPN client shows "
            "a certificate error.",
        ),
    ]
    for key, offset_min, title, body in children:
        note(
            "incident",
            key,
            sn.upsert(
                "incident",
                key,
                {
                    "short_description": title,
                    "description": body,
                    "cmdb_ci": ci["vpn_gw"],
                    "parent_incident": inc_major,
                    "assignment_group": grp_servicedesk,
                    "category": "Network",
                    "priority": "3",
                    "state": "6",
                    # The choice list carries the exact semantics these
                    # two scenarios need: S1's children really are duplicates,
                    # and S5's recurrences really were closed on a workaround.
                    "close_code": "Duplicate",
                    "close_notes": (
                        "Duplicate of the AUTH_CERT_EXPIRED gateway incident."
                    ),
                    "opened_at": _fmt(t0 + timedelta(minutes=offset_min)),
                    "resolved_at": _fmt(t0 + timedelta(hours=5, minutes=10)),
                },
            ),
        )

    # ---- S3: blast radius across the dependency edge ---------------------
    note(
        "incident",
        "s3:inc-radius",
        sn.upsert(
            "incident",
            "s3:inc-radius",
            {
                "short_description": (
                    "RADIUS authentication latency above threshold on radius-auth-01"
                ),
                "description": (
                    "radius-auth-01 is answering access-requests in 4-8 seconds "
                    "against a 300ms baseline. The authentication queue is backing up."
                ),
                "cmdb_ci": ci["radius"],
                "category": "Network",
                "priority": "2",
                "state": "2",
                "opened_at": _fmt(t0 + timedelta(days=6, hours=2)),
            },
        ),
    )
    note(
        "incident",
        "s3:inc-service",
        sn.upsert(
            "incident",
            "s3:inc-service",
            {
                "short_description": "Remote access service degraded - logins time out",
                "description": (
                    "Users report the remote access service hangs at 'authenticating' "
                    "and eventually times out. No certificate error this time, and "
                    "the gateway itself is reachable."
                ),
                "cmdb_ci": ci["vpn_service"],
                "category": "Network",
                "priority": "2",
                "state": "2",
                "opened_at": _fmt(t0 + timedelta(days=6, hours=2, minutes=25)),
            },
        ),
    )

    # ---- S4: the hub CI that must not collapse ---------------------------
    hub = [
        (
            "s4:inc-hub-1",
            "Password reset request for contractor account",
            "New contractor needs an initial password set on the domain.",
        ),
        (
            "s4:inc-hub-2",
            "DNS resolution slow for the finance subnet",
            "Name lookups from the finance VLAN take several seconds.",
        ),
        (
            "s4:inc-hub-3",
            "Group policy not applying to the new laptop build",
            "The imaged laptops are not picking up the drive mapping GPO.",
        ),
        (
            "s4:inc-hub-4",
            "Disk space warning on the domain controller",
            "Monitoring flagged the C: volume above 90 percent.",
        ),
    ]
    for idx, (key, title, body) in enumerate(hub):
        note(
            "incident",
            key,
            sn.upsert(
                "incident",
                key,
                {
                    "short_description": title,
                    "description": body,
                    "cmdb_ci": ci["dc"],
                    "assignment_group": grp_servicedesk,
                    "category": "Inquiry / Help",
                    "priority": "4",
                    "state": "2",
                    "opened_at": _fmt(t0 + timedelta(days=2, hours=idx * 5)),
                },
            ),
        )

    # ---- S5: problem management, recurrence not occurrence ---------------
    # The distinction S1 cannot express. S1's five children share a
    # parent_incident: one occurrence seen five times in three hours. S5's
    # three incidents share a problem_id and nothing else: three separate
    # occurrences, weeks apart, of one unresolved known error. A correlator
    # that treats "shares a problem" as "is the same situation" merges them
    # and reports one three-week outage that never happened.
    prb_known = note(
        "problem",
        "s5:prb-known-error",
        sn.upsert(
            "problem",
            "s5:prb-known-error",
            {
                "short_description": (
                    "Known error: RADIUS pool exhaustion causes AUTH_TIMEOUT at peak"
                ),
                "description": (
                    "radius-auth-01 runs a fixed pool of 128 authentication worker "
                    "threads. At Monday-morning peak the pool is exhausted and "
                    "clients receive AUTH_TIMEOUT after 30 seconds. Root cause is "
                    "understood and a permanent fix (raising the pool ceiling and "
                    "moving to dynamic sizing) is pending a maintenance window."
                ),
                "cmdb_ci": ci["radius"],
                "known_error": "true",
                "assigned_to": assignee,
                "workaround": (
                    "Restart the radius-auth service to drain the queue. This "
                    "restores service in about two minutes and holds until the next "
                    "peak. It does not prevent recurrence."
                ),
            },
        ),
    )
    recurrences = [
        ("s5:inc-recur-1", 21, "VPN logins timing out on Monday morning"),
        ("s5:inc-recur-2", 14, "AUTH_TIMEOUT again for remote users at 09:10"),
        ("s5:inc-recur-3", 7, "Authentication timeouts during the morning peak"),
    ]
    for key, days_before, title in recurrences:
        note(
            "incident",
            key,
            sn.upsert(
                "incident",
                key,
                {
                    "short_description": title,
                    "description": (
                        "Remote users report AUTH_TIMEOUT during the morning login "
                        "peak. Authentication recovers after the radius-auth service "
                        "is restarted. Same signature as the previous occurrences."
                    ),
                    "cmdb_ci": ci["radius"],
                    "problem_id": prb_known,
                    "category": "Network",
                    "priority": "2",
                    "state": "6",
                    "close_code": "Workaround provided",
                    "close_notes": (
                        "Applied the documented workaround: restarted radius-auth, "
                        "queue drained, logins recovered within two minutes. "
                        "Underlying known error remains open."
                    ),
                    "opened_at": _fmt(t0 - timedelta(days=days_before, hours=-3)),
                    "resolved_at": _fmt(
                        t0 - timedelta(days=days_before, hours=-3) + timedelta(hours=1)
                    ),
                },
            ),
        )

    # ---- S6: the request lane --------------------------------------------
    # Nothing broke here. A request is a fulfilment record, and any facet built
    # over this corpus has to be able to say so.
    ritm = note(
        "sc_req_item",
        "s6:ritm-laptop",
        sn.upsert(
            "sc_req_item",
            "s6:ritm-laptop",
            {
                "short_description": "Standard Laptop for new starter",
                "description": (
                    "New joiner in the east-region sales team starts Monday and "
                    "needs the standard laptop build with the VPN client "
                    "preinstalled."
                ),
                "cat_item": cat_laptop,
                "assignment_group": grp_servicedesk,
                "priority": "3",
                "opened_at": _fmt(t0 + timedelta(days=1, hours=3)),
            },
        ),
    )
    note(
        "sc_task",
        "s6:sctask-image",
        sn.upsert(
            "sc_task",
            "s6:sctask-image",
            {
                "short_description": "Image the laptop and join it to the domain",
                "description": (
                    "Apply the standard image, join acme-ad-dc-01, install the VPN "
                    "client and hand over to the requester."
                ),
                "request_item": ritm,
                "assignment_group": grp_servicedesk,
                "priority": "3",
                "opened_at": _fmt(t0 + timedelta(days=1, hours=4)),
            },
        ),
    )

    # ---- S7: the knowledge lane ------------------------------------------
    if kb_it:
        note(
            "kb_knowledge",
            "s7:kb-cert-chain",
            sn.upsert(
                "kb_knowledge",
                "s7:kb-cert-chain",
                {
                    "short_description": (
                        "Resolving AUTH_CERT_EXPIRED on VPN gateways after KB5032190"
                    ),
                    "text": (
                        "<p>Symptom: VPN clients fail the handshake with "
                        "AUTH_CERT_EXPIRED although the leaf certificate is inside "
                        "its validity window.</p><p>Cause: KB5032190 hardens "
                        "certificate chain validation and rejects intermediate CA "
                        "certificates signed with legacy algorithms.</p>"
                        "<p>Resolution: reissue the intermediate CA certificate "
                        "using SHA-256 and reload the gateway trust store. Verify "
                        "with a test client handshake before restoring user "
                        "access.</p><p>Do not roll back KB5032190 -- doing so "
                        "reopens the vulnerability the update closes.</p>"
                    ),
                    "kb_knowledge_base": kb_it,
                    "workflow_state": "published",
                },
                key_field="short_description",
            ),
        )
    if kb_known_error:
        note(
            "kb_knowledge",
            "s7:kb-radius-restart",
            sn.upsert(
                "kb_knowledge",
                "s7:kb-radius-restart",
                {
                    "short_description": (
                        "Known error: restart radius-auth to clear AUTH_TIMEOUT"
                    ),
                    "text": (
                        "<p>Symptom: remote users receive AUTH_TIMEOUT during the "
                        "morning login peak.</p><p>Workaround: restart the "
                        "radius-auth service on radius-auth-01. Authentication "
                        "recovers within two minutes.</p><p>This is the "
                        "recommended first response for this signature.</p>"
                    ),
                    "kb_knowledge_base": kb_known_error,
                    "workflow_state": "published",
                },
                key_field="short_description",
            ),
        )

    # ---- S8: approved window vs actual execution -------------------------
    # CHG-A executed inside its approved window; this one did not. Both touch
    # vpn-gw-east-01, which is the point: a correlator that answers "was there
    # a change on this CI" returns both and has said nothing useful.
    chg_c = note(
        "change_request",
        "s8:chg-acl-out-of-window",
        sn.upsert(
            "change_request",
            "s8:chg-acl-out-of-window",
            {
                "short_description": (
                    "Firewall ACL update for site-to-site peers on vpn-gw-east-01"
                ),
                "description": (
                    "Tighten the site-to-site peer ACL on vpn-gw-east-01. Approved "
                    "for the Saturday 22:00-02:00 maintenance window. The engineer "
                    "was unavailable that weekend and applied it on the following "
                    "Thursday morning instead, without re-submitting for approval."
                ),
                "category": "Network",
                "cmdb_ci": ci["vpn_gw"],
                "assignment_group": grp_network,
                # Approved window: the Saturday slot.
                "start_date": _fmt(t0 - timedelta(days=2, hours=4)),
                "end_date": _fmt(t0 - timedelta(days=2)),
                # Actual execution: a Thursday morning, four days later and
                # entirely outside the approved window.
                "work_start": _fmt(t0 + timedelta(days=3, hours=7)),
                "work_end": _fmt(t0 + timedelta(days=3, hours=7, minutes=40)),
            },
        ),
    )
    note(
        "incident",
        "s8:inc-after-acl",
        sn.upsert(
            "incident",
            "s8:inc-after-acl",
            {
                "short_description": (
                    "Site-to-site VPN tunnels dropping to the branch offices"
                ),
                "description": (
                    "The branch office tunnels on vpn-gw-east-01 are flapping. "
                    "Remote-user VPN is unaffected -- this is the site-to-site peer "
                    "set only. Branch staff have lost access to head-office "
                    "applications."
                ),
                "cmdb_ci": ci["vpn_gw"],
                "assignment_group": grp_network,
                "category": "Network",
                "priority": "2",
                "state": "2",
                "opened_at": _fmt(t0 + timedelta(days=3, hours=8, minutes=15)),
            },
        ),
    )
    created["change_c"] = chg_c

    created["anchor"] = _fmt(t0)
    created["ci"] = ci
    created["change_a"] = chg_a
    created["change_b"] = chg_b
    created["problem"] = prb
    created["problem_known_error"] = prb_known
    created["incident_major"] = inc_major
    return created


def teardown(sn: Snow, manifest: dict[str, Any]) -> int:
    deleted = 0
    # Relationships first: a CI with a live relationship will not delete.
    for rel in manifest.get("relationships", []):
        if sn.delete(rel["table"], rel["sys_id"]):
            deleted += 1
    # Tasks before the CIs and changes they reference.
    order = {"incident": 0, "problem": 1, "change_request": 2}
    records = sorted(manifest.get("records", []), key=lambda r: order.get(r["table"], 9))
    for rec in records:
        if sn.delete(rec["table"], rec["sys_id"]):
            deleted += 1
    return deleted


def main() -> int:
    ap = argparse.ArgumentParser(description="ServiceNow roadmap fixtures")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    ap.add_argument("--anchor-days-ago", type=int, default=DEFAULT_ANCHOR_DAYS_AGO)
    ap.add_argument("--instance", default=os.environ.get("SERVICENOW_INSTANCE_URL", ""))
    ap.add_argument("--username", default=os.environ.get("SERVICENOW_USERNAME", ""))
    ap.add_argument("--password", default=os.environ.get("SERVICENOW_PASSWORD", ""))
    args = ap.parse_args()

    if not (args.build or args.teardown):
        ap.error("pass --build or --teardown")
    if not (args.instance and args.username and args.password):
        ap.error(
            "instance/username/password required via flags or "
            "SERVICENOW_INSTANCE_URL / SERVICENOW_USERNAME / SERVICENOW_PASSWORD"
        )

    sn = Snow(args.instance, args.username, args.password)

    if args.teardown:
        if not MANIFEST.exists():
            print("no manifest; nothing to tear down")
            return 0
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        n = teardown(sn, manifest)
        MANIFEST.unlink()
        print(f"deleted {n} records")
        return 0

    now = datetime.now(UTC)
    anchor = (now - timedelta(days=args.anchor_days_ago)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )
    # A future-dated incident is not a scenario, it is a data defect: it falls
    # outside every "recent activity" window and reads as a clock problem to
    # anyone who looks. Fail loudly rather than author it.
    latest = anchor + timedelta(days=MAX_FORWARD_DAYS)
    if latest > now:
        ap.error(
            f"--anchor-days-ago {args.anchor_days_ago} puts the latest scenario "
            f"record at {_fmt(latest)}, which is in the future. Use at least "
            f"{MAX_FORWARD_DAYS + 1}."
        )
    created = build(sn, anchor)
    MANIFEST.write_text(json.dumps(created, indent=2), encoding="utf-8")
    print(f"anchor t0 = {created['anchor']}")
    print(f"records   = {len(created['records'])}")
    print(f"relations = {len(created['relationships'])}")
    print(f"manifest  -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
