"""Composable MAF plugin bundle for provider-only, tool-only, or combined use."""

from __future__ import annotations

from typing import Any

from contextedge.integrations.maf.client import (
    ChangeRiskClient,
    CmdbTopologyClient,
    CohortClient,
    ContextGraphClient,
    EdgeProposalClient,
    FixApplicabilityClient,
)
from contextedge.integrations.maf.provider import ContextGraphProvider
from contextedge.integrations.maf.tools import (
    ChangeRiskTools,
    CmdbTopologyTools,
    CohortTools,
    ContextGraphTools,
    DiagnosisTools,
    EdgeProposalTools,
    FixApplicabilityTools,
)


class ContextGraphMAFPlugin:
    def __init__(
        self,
        client: ContextGraphClient,
        *,
        enable_provider: bool = True,
        enable_tool: bool = True,
        cmdb_client: CmdbTopologyClient | None = None,
        change_risk_client: ChangeRiskClient | None = None,
        fix_applicability_client: FixApplicabilityClient | None = None,
        cohort_client: CohortClient | None = None,
        edge_proposal_client: EdgeProposalClient | None = None,
        diagnosis_client: Any | None = None,
        writeback: Any | None = None,
    ):
        # F1 write-back reaches the provider through the bundle — before
        # this, the flywheel was constructible only by bypassing the
        # plugin and building ContextGraphProvider by hand.
        self.provider = (
            ContextGraphProvider(client, writeback=writeback)
            if enable_provider
            else None
        )
        self.toolset = ContextGraphTools(client) if enable_tool else None
        self.cmdb_toolset = (
            CmdbTopologyTools(cmdb_client) if cmdb_client is not None else None
        )
        self.change_risk_toolset = (
            ChangeRiskTools(change_risk_client)
            if change_risk_client is not None
            else None
        )
        self.fix_applicability_toolset = (
            FixApplicabilityTools(fix_applicability_client)
            if fix_applicability_client is not None
            else None
        )
        self.cohort_toolset = (
            CohortTools(cohort_client) if cohort_client is not None else None
        )
        self.edge_proposal_toolset = (
            EdgeProposalTools(edge_proposal_client)
            if edge_proposal_client is not None
            else None
        )
        # F1. Two tools, and the ORDER they are registered in is the hint:
        # prior_hypotheses before record_diagnosis, because inheriting what was
        # already ruled out is the half that saves work, and the half an agent
        # will skip if it reads the write tool first.
        self.diagnosis_toolset = (
            DiagnosisTools(diagnosis_client) if diagnosis_client is not None else None
        )
        self.context_providers = [self.provider] if self.provider is not None else []
        self.tools = (
            [self.toolset.query_context_graph] if self.toolset is not None else []
        )
        if self.diagnosis_toolset is not None:
            self.tools.append(self.diagnosis_toolset.prior_hypotheses)
            self.tools.append(self.diagnosis_toolset.record_diagnosis)
        if self.cmdb_toolset is not None:
            self.tools.append(self.cmdb_toolset.cmdb_topology)
        if self.change_risk_toolset is not None:
            self.tools.append(self.change_risk_toolset.assess_change_risk)
        if self.fix_applicability_toolset is not None:
            self.tools.append(
                self.fix_applicability_toolset.assess_fix_applicability
            )
        if self.cohort_toolset is not None:
            self.tools.append(self.cohort_toolset.get_cohort_shared_attributes)
        if self.edge_proposal_toolset is not None:
            self.tools.append(self.edge_proposal_toolset.propose_dependency)
