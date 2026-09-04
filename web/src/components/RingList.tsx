// Stage 11: ranked table of suspicious communities, wired to focus the graph and open the case-file panel.

import type { RingSummary } from '../api/client'

interface RingListProps {
  rings: RingSummary[]
  selectedClusterId: number | null
  onSelectRing: (id: number) => void
}

function formatRupees(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(amount)
}

const STATUS_STYLES: Record<RingSummary['status'], string> = {
  suspicious: 'bg-danger-dim text-danger-bright border-danger/40',
  cleared: 'bg-safe-dim text-safe-bright border-safe/40',
  unlabeled: 'bg-neutral-800 text-neutral-400 border-neutral-700',
}

const ROW_ACCENT_STYLES: Record<RingSummary['status'], string> = {
  suspicious: 'border-l-4 border-l-danger',
  cleared: 'border-l-4 border-l-safe',
  unlabeled: 'border-l-4 border-l-neutral-700',
}

const TOP_FEATURE_LABELS: Record<string, string> = {
  event_rate_ratio: 'elevated events',
  high_weight_edge_share: 'device/card reuse',
  timing_burst_score: 'signup burst',
  fresh_account_ratio: 'fresh accounts',
  promo_concentration: 'promo farming',
}

function topFeatureLabel(topFeature: string | undefined): string | null {
  if (!topFeature) return null
  return TOP_FEATURE_LABELS[topFeature] ?? topFeature.replace(/_/g, ' ')
}

export default function RingList({ rings, selectedClusterId, onSelectRing }: RingListProps) {
  const sorted = [...rings].sort((a, b) => b.score - a.score)

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-neutral-800 bg-neutral-900">
      <div className="border-b border-neutral-800 px-4 py-3">
        <h2 className="text-sm font-semibold text-neutral-200">Ranked communities</h2>
        <p className="text-xs text-neutral-500">Click a row to focus it in the graph</p>
      </div>
      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 bg-neutral-900 text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="px-4 py-2 font-medium">#</th>
              <th className="px-4 py-2 font-medium">Size</th>
              <th className="px-4 py-2 font-medium">Score</th>
              <th className="px-4 py-2 font-medium">₹ risk</th>
              <th className="px-4 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((ring) => {
              const isSelected = selectedClusterId === ring.id
              return (
                <tr
                  key={ring.id}
                  onClick={() => onSelectRing(ring.id)}
                  title={ring.ground_truth}
                  className={
                    'cursor-pointer border-b border-neutral-800/60 transition-colors hover:bg-neutral-800/60 ' +
                    ROW_ACCENT_STYLES[ring.status] + ' ' +
                    (isSelected ? 'bg-neutral-800' : '')
                  }
                >
                  <td className="px-4 py-2 text-neutral-400">{ring.id}</td>
                  <td className="px-4 py-2 text-neutral-300">{ring.size}</td>
                  <td className="px-4 py-2">
                    <span className="font-mono text-neutral-200">{ring.score.toFixed(3)}</span>
                    {topFeatureLabel(ring.top_feature) && (
                      <span className="ml-2 font-mono text-[10px] text-neutral-500">
                        {topFeatureLabel(ring.top_feature)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-neutral-300">{formatRupees(ring.rupee_risk)}</td>
                  <td className="px-4 py-2">
                    <span
                      className={
                        'inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ' +
                        STATUS_STYLES[ring.status]
                      }
                    >
                      {ring.status}
                    </span>
                  </td>
                </tr>
              )
            })}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-neutral-500">
                  No communities yet — run detection first.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
