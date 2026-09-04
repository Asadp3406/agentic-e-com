// Stage 12: slide-over panel showing the agent's evidence subgraph, reasoning, action, and confidence.
// Opens when a ring/cluster is selected (App.tsx's `selectedClusterId`). Fetches the full case
// file from GET /api/rings/{id} — slow the first time a community is opened (a real LLM call),
// instant after (served from the case-file JSON on disk).

import { useEffect, useRef, useState, type ReactNode } from 'react'
import ForceGraph2D, { type ForceGraphMethods, type NodeObject, type LinkObject } from 'react-force-graph-2d'
import { ApiError, getRing, type GraphEdge, type GraphNode, type RingCaseFile } from '../api/client'

type FGNode = NodeObject<GraphNode>
type FGLink = LinkObject<GraphNode, GraphEdge>

interface CaseFilePanelProps {
  clusterId: number | null
  onClose: () => void
}

const ENTITY_TYPE_LABEL: Record<string, string> = {
  device: 'Device',
  card: 'Card',
  phone: 'Phone',
  address: 'Address',
  ip_subnet: 'IP subnet',
  pincode: 'Pincode',
}

// Same weak/strong split Stage 5's scoring uses: device/card require deliberate reuse,
// phone/address are moderate, ip_subnet/pincode are weak and common among unrelated people.
const STRONG_ENTITY_TYPES = new Set(['device', 'card'])

function formatRupees(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(amount)
}

function extractBaselineStats(caseFile: RingCaseFile): Record<string, unknown> | null {
  const call = (caseFile.tool_call_trail as Array<{ tool_name: string; result: Record<string, unknown> }>).find(
    (tc) => tc.tool_name === 'compare_to_baseline',
  )
  return call ? call.result : null
}

function extractEventStats(caseFile: RingCaseFile): Record<string, unknown> | null {
  const call = (caseFile.tool_call_trail as Array<{ tool_name: string; result: Record<string, unknown> }>).find(
    (tc) => tc.tool_name === 'get_events',
  )
  return call ? call.result : null
}

const ACTION_LABEL: Record<string, string> = {
  monitor: 'Monitor',
  hold: 'Hold orders',
  manual_review: 'Manual review',
  block: 'Block',
}

const ACTION_STYLE: Record<string, string> = {
  monitor: 'bg-neutral-800 text-neutral-300 border-neutral-700',
  hold: 'bg-amber-950/60 text-amber-300 border-amber-700/40',
  manual_review: 'bg-amber-950/60 text-amber-300 border-amber-700/40',
  block: 'bg-danger-dim text-danger-bright border-danger/40',
}

export default function CaseFilePanel({ clusterId, onClose }: CaseFilePanelProps) {
  const [caseFile, setCaseFile] = useState<RingCaseFile | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (clusterId === null) {
      setCaseFile(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    setCaseFile(null)
    getRing(clusterId)
      .then((data) => {
        if (!cancelled) setCaseFile(data)
      })
      .catch((err) => {
        if (!cancelled) {
          const message = err instanceof ApiError ? err.message : 'Failed to load case file.'
          setError(message)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [clusterId])

  const isOpen = clusterId !== null

  return (
    <>
      <div
        className={
          'fixed inset-0 z-30 bg-black/60 transition-opacity ' +
          (isOpen ? 'opacity-100' : 'pointer-events-none opacity-0')
        }
        onClick={onClose}
      />
      <div
        className={
          'fixed right-0 top-0 z-40 h-full w-full max-w-xl transform overflow-y-auto border-l border-neutral-800 bg-neutral-950 shadow-2xl transition-transform duration-300 ' +
          (isOpen ? 'translate-x-0' : 'translate-x-full')
        }
      >
        {clusterId !== null && (
          <PanelContent
            clusterId={clusterId}
            caseFile={caseFile}
            loading={loading}
            error={error}
            onClose={onClose}
          />
        )}
      </div>
    </>
  )
}

function PanelContent({
  clusterId,
  caseFile,
  loading,
  error,
  onClose,
}: {
  clusterId: number
  caseFile: RingCaseFile | null
  loading: boolean
  error: string | null
  onClose: () => void
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-neutral-500">Case file</p>
          <h2 className="text-lg font-semibold text-neutral-100">Community #{clusterId}</h2>
        </div>
        <button
          onClick={onClose}
          className="rounded-lg border border-neutral-700 px-3 py-1.5 text-xs text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200"
        >
          Close
        </button>
      </div>

      {loading && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-neutral-700 border-t-danger" />
          <p className="text-sm text-neutral-400">
            Investigating community #{clusterId}…
            <br />
            <span className="text-xs text-neutral-600">First open runs a live agent call — this can take a few seconds.</span>
          </p>
        </div>
      )}

      {error && !loading && (
        <div className="m-5 rounded-lg border border-danger/30 bg-danger-dim/30 px-4 py-3 text-sm text-danger-bright">
          {error}
        </div>
      )}

      {caseFile && !loading && <CaseFileBody caseFile={caseFile} />}
    </div>
  )
}

function CaseFileBody({ caseFile }: { caseFile: RingCaseFile }) {
  const { verdict, policy_decision: policy } = caseFile
  const cleared = !verdict.is_ring
  const baseline = extractBaselineStats(caseFile)
  const events = extractEventStats(caseFile)

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto px-5 py-5">
      {/* Verdict banner */}
      <div
        className={
          'flex items-center justify-between rounded-xl border px-4 py-3 ' +
          (cleared ? 'border-safe/40 bg-safe-dim/30' : 'border-danger/40 bg-danger-dim/30')
        }
      >
        <div>
          <p className={'text-sm font-semibold ' + (cleared ? 'text-safe-bright' : 'text-danger-bright')}>
            {cleared ? 'CLEARED — benign' : 'FLAGGED — likely fraud ring'}
          </p>
          <p className="text-xs text-neutral-400">
            {caseFile.size} members · ground truth: {caseFile.ground_truth}
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs text-neutral-500">Agent confidence</p>
          <p className={'text-xl font-bold ' + (cleared ? 'text-safe-bright' : 'text-danger-bright')}>
            {(verdict.confidence * 100).toFixed(0)}%
          </p>
        </div>
      </div>

      {caseFile.degraded && (
        <div className="rounded-lg border border-amber-700/40 bg-amber-950/40 px-4 py-2 text-xs text-amber-300">
          Degraded investigation — the agent call failed and this verdict falls back to raw scoring
          features.{caseFile.degraded_reason ? ` (${caseFile.degraded_reason})` : ''}
        </div>
      )}

      {/* Recommended action */}
      <Section title="Recommended action">
        <div className="flex items-center gap-3">
          <span
            className={
              'inline-block rounded-full border px-3 py-1 text-xs font-semibold ' +
              (ACTION_STYLE[policy.action] ?? ACTION_STYLE.monitor)
            }
          >
            {ACTION_LABEL[policy.action] ?? policy.action}
          </span>
          {policy.was_downgraded && (
            <span className="text-xs text-neutral-500">
              (agent proposed {ACTION_LABEL[policy.agent_recommended_action] ?? policy.agent_recommended_action},
              policy downgraded — bounded action, never upgraded)
            </span>
          )}
        </div>
        <p className="mt-2 text-xs text-neutral-400">{policy.rationale}</p>
        <div className="mt-2 flex gap-4 text-xs text-neutral-500">
          <span>Estimated ring risk: {formatRupees(policy.estimated_ring_risk_inr)}</span>
          <span>Est. false-block cost: {formatRupees(policy.estimated_false_block_cost_inr)}</span>
        </div>
      </Section>

      {/* Evidence subgraph */}
      <Section title="Evidence subgraph">
        <div className="h-64 overflow-hidden rounded-lg border border-neutral-800 bg-black">
          <EvidenceSubgraph subgraph={caseFile.evidence_subgraph} />
        </div>
      </Section>

      {/* Shared entities */}
      <Section title="Shared entities">
        {caseFile.shared_entities.length === 0 ? (
          <p className="text-sm text-neutral-500">No shared entities within this community.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {caseFile.shared_entities.map((e) => (
              <span
                key={e.entity}
                className={
                  'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs ' +
                  (STRONG_ENTITY_TYPES.has(e.type)
                    ? 'border-danger/40 bg-danger-dim/30 text-danger-bright'
                    : 'border-neutral-700 bg-neutral-800 text-neutral-300')
                }
                title={e.entity}
              >
                <span className="font-medium">{ENTITY_TYPE_LABEL[e.type] ?? e.type}</span>
                <span className="text-neutral-500">·</span>
                <span>{e.member_count} members</span>
                <span className="text-neutral-500">·</span>
                <span>w={e.weight.toFixed(2)}</span>
              </span>
            ))}
          </div>
        )}
      </Section>

      {/* Behavioral evidence, as readable stats */}
      <Section title="Behavioral evidence">
        <div className="grid grid-cols-2 gap-3">
          {baseline && (
            <>
              <Stat
                label="Event rate vs. baseline"
                value={`${Number(baseline.event_rate_ratio).toFixed(2)}×`}
                hint={`${(Number(baseline.community_event_rate) * 100).toFixed(1)}% vs. ${(
                  Number(baseline.global_baseline_event_rate) * 100
                ).toFixed(1)}% platform-wide`}
                alarming={Number(baseline.event_rate_ratio) >= 2}
              />
              <Stat
                label="Account-creation burst"
                value={Number(baseline.timing_burst_score).toFixed(2)}
                hint="0 = spread out like ordinary signups, 1 = tight coordinated burst"
                alarming={Number(baseline.timing_burst_score) >= 0.5}
              />
              <Stat
                label="High-weight entity share"
                value={`${(Number(baseline.high_weight_edge_share) * 100).toFixed(0)}%`}
                hint="share of shared-entity evidence from device/card (deliberate) vs. weaker signals"
                alarming={Number(baseline.high_weight_edge_share) >= 0.5}
              />
              <Stat
                label="Fresh account ratio"
                value={`${(Number(baseline.fresh_account_ratio) * 100).toFixed(0)}%`}
                hint="fraction of members created within the same 14-day window"
                alarming={Number(baseline.fresh_account_ratio) >= 0.5}
              />
            </>
          )}
          {events && (
            <Stat
              label="Chargeback / return / COD-refusal burst"
              value={`${events.n_bad_events} of ${events.n_orders} orders`}
              hint={`spread over ${Math.round(Number(events.bad_events_span_days))} days`}
              alarming={Number(baseline?.event_rate_ratio ?? 0) >= 2}
            />
          )}
        </div>
      </Section>

      {/* Reasoning narrative */}
      <Section title="Agent reasoning">
        <Collapsible text={verdict.reasoning} />
        {verdict.evidence.length > 0 && (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-neutral-400">
            {verdict.evidence.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        )}
        {verdict.benign_explanations_considered.length > 0 && (
          <div className="mt-4">
            <p className="text-xs font-medium uppercase tracking-wide text-neutral-500">
              Benign explanations considered
            </p>
            <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-neutral-400">
              {verdict.benign_explanations_considered.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </div>
        )}
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">{title}</h3>
      {children}
    </section>
  )
}

// Long-winded model responses (some real case files run past 800 characters) get collapsed
// to a readable preview rather than pushing the rest of the panel far down.
const COLLAPSE_THRESHOLD = 420

function Collapsible({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  if (!text) return <p className="text-sm text-neutral-500">No reasoning provided.</p>
  const needsCollapse = text.length > COLLAPSE_THRESHOLD
  const shown = needsCollapse && !expanded ? text.slice(0, COLLAPSE_THRESHOLD).trimEnd() + '…' : text
  return (
    <div>
      <p className="text-sm leading-relaxed text-neutral-300">{shown}</p>
      {needsCollapse && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1.5 text-xs font-medium text-neutral-500 underline decoration-dotted hover:text-neutral-300"
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  hint,
  alarming,
}: {
  label: string
  value: string
  hint: string
  alarming: boolean
}) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wide text-neutral-500">{label}</p>
      <p className={'mt-1 text-lg font-semibold ' + (alarming ? 'text-danger-bright' : 'text-neutral-200')}>
        {value}
      </p>
      <p className="mt-0.5 text-[11px] text-neutral-600">{hint}</p>
    </div>
  )
}

function EvidenceSubgraph({ subgraph }: { subgraph: RingCaseFile['evidence_subgraph'] }) {
  const RING_COLOR = '#ef4444'
  const BENIGN_COLOR = '#22c55e'
  const ENTITY_COLOR = '#525252'

  const containerRef = useRef<HTMLDivElement>(null)
  const fgRef = useRef<ForceGraphMethods<FGNode, FGLink> | undefined>(undefined)
  const [dimensions, setDimensions] = useState({ width: 400, height: 256 })

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      setDimensions({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const t = setTimeout(() => fgRef.current?.zoomToFit(400, 30), 700)
    return () => clearTimeout(t)
  }, [subgraph, dimensions])

  const nodeColor = (n: GraphNode) => {
    if (n.type !== 'customer') return ENTITY_COLOR
    return n.suspicious ? RING_COLOR : BENIGN_COLOR
  }

  return (
    <div ref={containerRef} className="h-full w-full">
      <ForceGraph2D<GraphNode, GraphEdge>
        ref={fgRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={{ nodes: subgraph.nodes, links: subgraph.edges }}
        backgroundColor="#0a0a0a"
        nodeId="id"
        nodeLabel={(n) => `${(n as GraphNode).id} (${(n as GraphNode).type})`}
        nodeColor={(n) => nodeColor(n as GraphNode)}
        nodeVal={(n) => (((n as GraphNode).type === 'customer' ? 3 + (n as GraphNode).risk * 6 : 2) ** 2)}
        nodeRelSize={1}
        linkColor={() => 'rgba(163,163,163,0.35)'}
        linkWidth={(l) => 0.5 + (l as unknown as GraphEdge).weight * 2}
        cooldownTicks={80}
        enableZoomInteraction={true}
        enablePanInteraction={true}
      />
    </div>
  )
}
