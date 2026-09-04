// Stage 12: Recharts view for precision/recall, ₹ FP-cost sweep, and the graph-vs-baseline delta.
// Reads from GET /api/metrics and GET /api/benign — both already render-ready (PLAN.md B3),
// no reshaping happens here beyond what Recharts needs for its own props.

import { useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ComposedChart } from 'recharts'
import { ApiError, getBenign, getMetrics, type BenignPayload, type MetricsPayload } from '../api/client'

const COLOR_GRAPH = '#ef4444' // danger — the graph-based detector
const COLOR_BASELINE = '#f59e0b' // amber — the structure-blind baseline
const COLOR_NET = '#22c55e' // safe — net ₹ saved
const COLOR_SAVED = '#3b82f6' // blue — ₹ saved
const COLOR_LOST = '#ef4444' // danger — ₹ lost
const COLOR_PRECISION = '#3b82f6'
const COLOR_RECALL = '#22c55e'

function formatRupees(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(amount)
}

function formatRupeesCompact(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(amount)
}

export default function MetricsView() {
  const [metrics, setMetrics] = useState<MetricsPayload | null>(null)
  const [benign, setBenign] = useState<BenignPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([getMetrics(), getBenign()])
      .then(([m, b]) => {
        if (cancelled) return
        setMetrics(m)
        setBenign(b)
      })
      .catch((err) => {
        if (cancelled) return
        const message = err instanceof ApiError ? err.message : 'Failed to load metrics.'
        setError(message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-neutral-700 border-t-danger" />
          <p className="text-sm text-neutral-500">Loading metrics…</p>
        </div>
      </div>
    )
  }

  if (error || !metrics || !benign) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-danger-bright">{error ?? 'No metrics available — run detection first.'}</p>
      </div>
    )
  }

  return (
    <div className="grid h-full grid-cols-2 gap-4 overflow-y-auto p-6">
      <PrecisionRecallCard metrics={metrics} />
      <RupeeSweepCard metrics={metrics} />
      <RingRecallBarCard metrics={metrics} />
      <BenignResultsCard benign={benign} />
    </div>
  )
}

function ChartCard({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-[340px] flex-col rounded-xl border border-neutral-800 bg-neutral-900 p-5">
      <h3 className="text-sm font-semibold text-neutral-200">{title}</h3>
      <p className="mb-3 text-xs text-neutral-500">{subtitle}</p>
      <div className="flex-1">{children}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Precision / recall — ring level and account level, side by side
// ---------------------------------------------------------------------------

function PrecisionRecallCard({ metrics }: { metrics: MetricsPayload }) {
  const data = [
    {
      level: 'Ring',
      precision: Number(metrics.ring_level_metrics.precision ?? 0),
      recall: Number(metrics.ring_level_metrics.recall ?? 0),
      f1: Number(metrics.ring_level_metrics.f1 ?? 0),
    },
    {
      level: 'Account',
      precision: Number(metrics.account_level_metrics.precision ?? 0),
      recall: Number(metrics.account_level_metrics.recall ?? 0),
      f1: Number(metrics.account_level_metrics.f1 ?? 0),
    },
  ]

  return (
    <ChartCard
      title="Precision / recall"
      subtitle={`At the money-optimal score threshold (${metrics.money_optimal_threshold.toFixed(3)}), ring-level and account-level.`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
          <XAxis dataKey="level" stroke="#a3a3a3" fontSize={12} />
          <YAxis stroke="#a3a3a3" fontSize={12} domain={[0, 1]} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
          <Tooltip
            contentStyle={{ background: '#171717', border: '1px solid #404040', borderRadius: 8, fontSize: 12 }}
            formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="precision" name="Precision" fill={COLOR_PRECISION} radius={[4, 4, 0, 0]} />
          <Bar dataKey="recall" name="Recall" fill={COLOR_RECALL} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

// ---------------------------------------------------------------------------
// ₹ false-positive-cost threshold sweep, money-optimal point marked
// ---------------------------------------------------------------------------

function RupeeSweepCard({ metrics }: { metrics: MetricsPayload }) {
  const data = metrics.cost_sweep.map((p) => ({
    threshold: p.threshold,
    net: p.net_rupees,
    saved: p.rupees_saved,
    lost: p.rupees_lost,
  }))
  const optimal = metrics.money_optimal_point

  return (
    <ChartCard
      title="₹ false-positive-cost sweep"
      subtitle={`Net ₹ (saved − lost) across every score threshold. Money-optimal point at ${optimal.threshold.toFixed(3)}: ${formatRupees(optimal.net_rupees)} net.`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
          <XAxis
            dataKey="threshold"
            stroke="#a3a3a3"
            fontSize={12}
            tickFormatter={(v) => Number(v).toFixed(2)}
            type="number"
            domain={['dataMin', 'dataMax']}
          />
          <YAxis stroke="#a3a3a3" fontSize={12} tickFormatter={formatRupeesCompact} />
          <Tooltip
            contentStyle={{ background: '#171717', border: '1px solid #404040', borderRadius: 8, fontSize: 12 }}
            formatter={(v) => formatRupees(Number(v))}
            labelFormatter={(v) => `threshold ${Number(v).toFixed(3)}`}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="net" name="Net ₹" stroke={COLOR_NET} dot={false} strokeWidth={2} />
          <Line
            type="monotone"
            dataKey="saved"
            name="₹ saved"
            stroke={COLOR_SAVED}
            dot={false}
            strokeWidth={1}
            strokeDasharray="4 3"
          />
          <Line
            type="monotone"
            dataKey="lost"
            name="₹ lost"
            stroke={COLOR_LOST}
            dot={false}
            strokeWidth={1}
            strokeDasharray="4 3"
          />
          <ReferenceDot
            x={optimal.threshold}
            y={optimal.net_rupees}
            r={6}
            fill={COLOR_NET}
            stroke="#fff"
            strokeWidth={1.5}
            label={{ value: 'money-optimal', position: 'top', fill: COLOR_NET, fontSize: 11 }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

// ---------------------------------------------------------------------------
// Graph-vs-baseline ring recall bar — the punchline
// ---------------------------------------------------------------------------

function RingRecallBarCard({ metrics }: { metrics: MetricsPayload }) {
  const data = metrics.head_to_head.map((h) => ({
    budget: `Top ${(h.budget * 100).toFixed(0)}%`,
    graph: h.graph_recall,
    baseline: h.baseline_recall,
  }))

  return (
    <ChartCard
      title="Ring recall: graph vs. baseline"
      subtitle="At matched review-budget operating points — the punchline: how much more the graph-based detector catches vs. a structure-blind transaction classifier reviewing the same number of accounts."
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
          <XAxis dataKey="budget" stroke="#a3a3a3" fontSize={12} />
          <YAxis stroke="#a3a3a3" fontSize={12} domain={[0, 1]} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
          <Tooltip
            contentStyle={{ background: '#171717', border: '1px solid #404040', borderRadius: 8, fontSize: 12 }}
            formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="graph" name="Graph-based" fill={COLOR_GRAPH} radius={[4, 4, 0, 0]} />
          <Bar dataKey="baseline" name="Baseline (txn-only)" fill={COLOR_BASELINE} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

// ---------------------------------------------------------------------------
// Benign-cluster results — owned false positives
// ---------------------------------------------------------------------------

function BenignResultsCard({ benign }: { benign: BenignPayload }) {
  const data = [...benign.benign_clusters]
    .sort((a, b) => b.score - a.score)
    .map((c) => ({
      label: `#${c.id} ${c.cluster_tag}`,
      score: c.score,
      flagged: c.flagged,
    }))

  return (
    <ChartCard
      title="Benign-cluster results"
      subtitle={`${benign.n_false_positives} of ${benign.n_benign_clusters} benign look-alike clusters (family/office/hostel/couple/independent) false-flagged at threshold ${benign.threshold.toFixed(3)} — owned, not hidden.`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 24, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
          <XAxis type="number" domain={[0, 1]} stroke="#a3a3a3" fontSize={12} />
          <YAxis type="category" dataKey="label" stroke="#a3a3a3" fontSize={10} width={110} />
          <Tooltip
            contentStyle={{ background: '#171717', border: '1px solid #404040', borderRadius: 8, fontSize: 12 }}
            formatter={(v, _name, props) => [
              Number(v).toFixed(3),
              (props.payload as { flagged: boolean }).flagged ? 'score (false positive)' : 'score (correctly cleared)',
            ]}
          />
          <Bar dataKey="score" radius={[0, 4, 4, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.flagged ? COLOR_LOST : COLOR_RECALL} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}
