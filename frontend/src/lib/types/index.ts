export interface Tenant {
  id: string;
  name: string;
  slug: string;
  config: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Workspace {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Domain {
  id: string;
  tenant_id: string;
  workspace_id: string | null;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface User {
  id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  status: string;
  sso_provider: string | null;
  created_at: string;
  updated_at: string;
}

export interface RoleBinding {
  id: string;
  user_id: string;
  tenant_id: string;
  role: string;
  scope_type: string;
  scope_id: string | null;
  created_at: string;
}

export interface AuditLog {
  id: string;
  tenant_id: string;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  timestamp: string;
}

export interface Source {
  id: string;
  tenant_id: string;
  workspace_id: string | null;
  domain_ids: string[];
  source_type: string;
  display_name: string;
  owner_user_id: string;
  purpose: string | null;
  auth_type: string;
  auth_status: string;
  discovery_status: string;
  sync_mode: string;
  classification_policy_id?: string | null;
  retention_policy_id?: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface SourceObject {
  id: string;
  source_id: string;
  tenant_id: string;
  object_type: string;
  external_id: string;
  display_name: string;
  object_path: string | null;
  owner_hint: string | null;
  sensitivity_label: string | null;
  approved_for_backfill: boolean;
  approved_for_sync: boolean;
  backfill_window_days: number | null;
  steady_state_sync_enabled: boolean;
  last_checkpoint_at: string | null;
  last_successful_sync_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvidenceItem {
  id: string;
  tenant_id: string;
  source_id: string;
  evidence_type: string;
  title: string | null;
  body_text?: string | null;
  body_summary: string | null;
  relevance_state: string;
  relevance_score: number | null;
  created_at_source: string | null;
  ingested_at: string;
  created_at?: string;
}

export interface EvidenceItemDetail extends EvidenceItem {
  body_text: string | null;
  workspace_id: string | null;
  domain_id: string | null;
  source_object_id: string | null;
  thread_id: string | null;
  sensitivity_label: string | null;
  access_policy_id?: string | null;
  canonical_entity_refs: Record<string, unknown> | null;
}

export interface Episode {
  id: string;
  tenant_id: string;
  domain_id: string | null;
  title: string;
  status: string;
  extraction_confidence: number;
  root_cause_summary: string | null;
  final_outcome: string | null;
  reviewer_state: string;
  created_at: string;
  updated_at: string;
}

export interface EpisodeDetail extends Episode {
  workspace_id: string | null;
  primary_case_ref: string | null;
  reviewer_user_id: string | null;
  evidence_ids: string[] | null;
  entity_refs: Record<string, unknown> | null;
  steps: EpisodeStep[];
}

export interface EpisodeStep {
  id: string;
  episode_id: string;
  step_order: number;
  step_type: string;
  text: string;
  observation: string | null;
  result_state: string;
  failed_flag: boolean;
  successful_flag: boolean;
  extraction_confidence: number;
  evidence_refs: unknown[] | null;
}

export interface Pattern {
  id: string;
  tenant_id: string;
  domain_id: string | null;
  title: string;
  description: string | null;
  pattern_type: string;
  confidence: number;
  episode_count: number;
  active_flag: boolean;
  contradiction_score: number;
  freshness_score: number;
  trigger_conditions?: string[] | null;
  core_entities?: string[] | null;
  observed_errors?: string[] | null;
  root_causes?: string[] | null;
  resolution_steps?: string[] | null;
  evidence_summary?: Record<string, number> | null;
  created_at: string;
  updated_at: string;
}

export interface Playbook {
  id: string;
  tenant_id: string;
  domain_id: string | null;
  stable_key: string;
  title: string;
  description: string | null;
  lifecycle_state: string;
  risk_tier: string;
  automation_mode: string;
  owner_user_id: string;
  reviewer_user_id: string | null;
  approver_user_id: string | null;
  current_version_id: string | null;
  last_validated_at: string | null;
  expiry_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlaybookVersion {
  id: string;
  playbook_id: string;
  semantic_version: string;
  trigger_conditions: Record<string, unknown>;
  branching_logic: Record<string, unknown>;
  inputs: unknown;
  outputs: unknown;
  steps: unknown[];
  rollback_notes: string | null;
  evidence_refs: unknown[] | null;
  playbook_confidence: number;
  execution_confidence_guidance: string | null;
  published_at: string | null;
  published_by: string | null;
  created_at: string;
}

export interface SyncRun {
  id: string;
  source_id: string;
  source_object_id: string | null;
  tenant_id: string;
  run_type: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  items_processed: number;
  errors: Record<string, unknown> | null;
  created_at: string;
}

export interface EvaluationDataset {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  cases: unknown[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationRun {
  id: string;
  tenant_id: string;
  dataset_id: string;
  config: Record<string, unknown>;
  status: string;
  results: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PatternSubgraph {
  nodes: { type: string; id: string }[];
  edges: { source: string; target: string; type: string; weight: number }[];
}

export interface ThreadSummary {
  id: string;
  tenant_id: string;
  source_id: string;
  external_thread_id: string;
  title: string | null;
  participant_count: number;
  message_count: number;
  first_message_at: string | null;
  last_message_at: string | null;
  hydration_status: string;
  relevance_state: string;
  created_at: string;
}

/** GET /policies — grouped tenant policy records. */
export interface TenantPolicyRecord {
  id: string;
  name: string;
  description: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PoliciesOverview {
  retention_policies: TenantPolicyRecord[];
  classification_policies: TenantPolicyRecord[];
  access_policies: TenantPolicyRecord[];
  approval_policies: TenantPolicyRecord[];
}

export type PolicyType = "retention" | "classification" | "access" | "approval";

/** GET /drift/alerts */
export interface DriftAlert {
  playbook_id: string;
  title: string;
  issues: string[];
  severity: string;
}

/** POST /runtime/match */
export interface RuntimeMatchResult {
  playbook_id: string;
  playbook_title: string;
  stable_key: string;
  match_score: number;
  confidence: number;
  retrieval_score?: number | null;
  playbook_confidence?: number | null;
  freshness_status: string;
  evidence_count: number;
  risk_tier: string;
  automation_mode: string;
  scoring_breakdown?: Record<string, unknown> | null;
}

export interface RuntimeMatchResponse {
  match_id: string;
  session_id?: string | null;
  results: RuntimeMatchResult[];
  fallback_guidance: string | null;
  filters_applied: Record<string, unknown>;
}

/** POST /episodes/reconstruct — 202 Accepted */
export interface EpisodeReconstructQueuedResponse {
  status: string;
  evidence_count: number;
  task_id: string;
}

/** GET /runtime/explain/{match_id} */
export interface RuntimeExplainResponse {
  match_id: string;
  query_text: string;
  symptoms: string[];
  entities: string[];
  environment: Record<string, unknown>;
  ranked_results: Record<string, unknown>[];
  fallback_guidance: string | null;
  filters_applied: Record<string, unknown>;
}

/** GET /runtime/playbooks/{stable_key} */
export interface RuntimePlaybookVersion {
  id: string;
  playbook_id: string;
  semantic_version: string;
  trigger_conditions: Record<string, unknown>;
  branching_logic: Record<string, unknown>;
  inputs: unknown;
  outputs: unknown;
  steps: unknown[];
  rollback_notes: string | null;
  evidence_refs: unknown[] | null;
  playbook_confidence: number;
  execution_confidence_guidance: string | null;
  published_at: string | null;
  published_by: string | null;
  created_at: string;
}

// ── New API surface ───────────────────────────────────────────────────────

export interface Notification {
  id: string;
  tenant_id: string;
  user_id: string | null;
  notification_type: string;
  title: string;
  body: string;
  metadata: Record<string, unknown>;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Contradiction {
  id: string;
  tenant_id: string;
  source_a_ref: string;
  source_b_ref: string;
  contradiction_type: string;
  description: string | null;
  resolution_status: string;
  resolved_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface NegativeKnowledgeItem {
  id: string;
  tenant_id: string;
  domain_id: string | null;
  step_text: string;
  failure_reason: string | null;
  status: string;
  evidence_refs: unknown[] | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface IdentityAlias {
  id: string;
  canonical_identity_id: string;
  alias_text: string;
  source_id: string | null;
  confidence: number;
  created_by: string | null;
  created_at: string;
}

export interface CanonicalIdentity {
  id: string;
  tenant_id: string;
  entity_type: string;
  canonical_name: string;
  metadata_extra: Record<string, unknown> | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  aliases: IdentityAlias[];
}

export interface CorrelationEdge {
  id: string;
  tenant_id: string;
  source_evidence_id: string;
  target_evidence_id: string;
  correlation_type: string;
  confidence: number;
  explanation: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface AttachmentArtifact {
  id: string;
  evidence_id: string;
  tenant_id: string;
  file_name: string | null;
  mime_type: string | null;
  extraction_status: string;
  extracted_text: string | null;
  object_store_key: string | null;
  created_at: string;
}

export interface PatternEvidenceLink {
  id: string;
  pattern_id: string;
  episode_id: string | null;
  evidence_id: string | null;
  link_type: string;
  weight: number;
  created_at: string;
}

export interface RetrievalFeedback {
  id: string;
  tenant_id: string;
  match_id: string | null;
  playbook_id: string | null;
  feedback_type: string;
  details: Record<string, unknown> | null;
  submitted_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlaybookVersionDiff {
  playbook_id: string;
  base_version_id: string | null;
  base_semantic_version: string | null;
  target_version_id: string;
  target_semantic_version: string;
  changed_fields: string[];
  unified_diff: string;
}

export interface PendingApproval {
  id: string;
  execution_run_id: string;
  step_run_id: string | null;
  requested_by: string;
  requested_action: string;
  safety_class: string;
  context: Record<string, unknown>;
  status: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_comment: string | null;
  created_at: string;
}
