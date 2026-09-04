// Stage 11: typed fetch client for the FastAPI backend (api/main.py).
// Every function here maps 1:1 to one endpoint and one response shape from that module —
// no reshaping happens on the frontend, per PLAN.md B3 ("payloads render-ready ... frontend
// stays dumb").

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, options)
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new ApiError(res.status, detail.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// POST /api/run
// ---------------------------------------------------------------------------

export interface RunResult {
  run_id: string
  graph_nodes: number
  graph_edges: number
  n_communities: number
  n_reportable_communities: number
  method: string
  giant_flags: unknown[]
}

export function runDetection(): Promise<RunResult> {
  return request<RunResult>('/api/run', { method: 'POST' })
}

// ---------------------------------------------------------------------------
// GET /api/graph
// ---------------------------------------------------------------------------

export interface GraphNode {
  id: string
  type: 'customer' | 'device' | 'card' | 'phone' | 'address' | 'ip_subnet' | 'pincode'
  cluster_id: number | null
  suspicious: boolean
  risk: number
  ring_id?: string | null
  cluster_tag?: string | null
  entity_id?: string | null
}

export interface GraphEdge {
  source: string
  target: string
  type: string
  weight: number
}

export interface GraphPayload {
  run_id: string
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export function getGraph(): Promise<GraphPayload> {
  return request<GraphPayload>('/api/graph')
}

// ---------------------------------------------------------------------------
// GET /api/rings
// ---------------------------------------------------------------------------

export interface RingSummary {
  id: number
  size: number
  score: number
  rupee_risk: number
  status: 'suspicious' | 'cleared' | 'unlabeled'
  top_feature: string
  ground_truth: string
}

export interface RingsPayload {
  run_id: string
  rings: RingSummary[]
}

export function getRings(): Promise<RingsPayload> {
  return request<RingsPayload>('/api/rings')
}

// ---------------------------------------------------------------------------
// GET /api/rings/{id}
// ---------------------------------------------------------------------------

export interface RingCaseFile {
  run_id: string
  community_id: number
  size: number
  score: number | null
  status: string
  ground_truth: string
  members: unknown
  shared_entities: Array<{ entity: string; type: string; weight: number; member_count: number }>
  verdict: {
    is_ring: boolean
    confidence: number
    evidence: string[]
    benign_explanations_considered: string[]
    reasoning: string
  }
  degraded: boolean
  degraded_reason: string | null
  policy_decision: {
    community_id: number
    size: number
    action: string
    agent_recommended_action: string
    was_downgraded: boolean
    downgrade_reason: string | null
    estimated_ring_risk_inr: number
    estimated_false_block_cost_inr: number
    rationale: string
  }
  tool_call_trail: unknown[]
  evidence_subgraph: GraphPayload
}

export function getRing(id: number): Promise<RingCaseFile> {
  return request<RingCaseFile>(`/api/rings/${id}`)
}

// ---------------------------------------------------------------------------
// GET /api/metrics
// ---------------------------------------------------------------------------

export interface MetricsPayload {
  run_id: string
  money_optimal_threshold: number
  cost_sweep: Array<{
    threshold: number
    n_flagged: number
    ring_accounts_caught: number
    legit_accounts_blocked: number
    rupees_saved: number
    rupees_lost: number
    net_rupees: number
  }>
  money_optimal_point: {
    threshold: number
    net_rupees: number
    rupees_saved: number
    rupees_lost: number
    ring_accounts_caught: number
    legit_accounts_blocked: number
  }
  ring_level_metrics: Record<string, number>
  account_level_metrics: Record<string, number>
  head_to_head: Array<{
    budget: number
    n_flagged: number
    graph_recall: number
    graph_precision: number
    baseline_recall: number
    baseline_precision: number
  }>
}

export function getMetrics(): Promise<MetricsPayload> {
  return request<MetricsPayload>('/api/metrics')
}

// ---------------------------------------------------------------------------
// GET /api/benign
// ---------------------------------------------------------------------------

export interface BenignPayload {
  run_id: string
  threshold: number
  n_benign_clusters: number
  n_false_positives: number
  benign_clusters: Array<{
    id: number
    cluster_tag: string
    size: number
    score: number
    flagged: boolean
    top_feature: string
    sub_scores: Record<string, number>
  }>
}

export function getBenign(): Promise<BenignPayload> {
  return request<BenignPayload>('/api/benign')
}
