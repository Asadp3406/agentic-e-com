// Stage 11: summary cards for #rings found, #accounts flagged, total ₹ at risk, and benign false-positive rate.

interface SummaryCardsProps {
  nRings: number
  nAccountsFlagged: number
  totalRupeeAtRisk: number
  falsePositiveRate: number | null
  totalAccounts: number | null
}

function formatRupees(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(amount)
}

function Card({
  label,
  value,
  accent,
  subtext,
}: {
  label: string
  value: string
  accent?: 'danger' | 'safe'
  subtext?: string
}) {
  return (
    <div className="flex-1 min-w-[180px] rounded-xl border border-neutral-800 bg-neutral-900 px-6 py-5 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</p>
      <p
        className={
          'mt-2 text-3xl font-semibold ' +
          (accent === 'danger' ? 'text-danger-bright' : accent === 'safe' ? 'text-safe-bright' : 'text-neutral-100')
        }
      >
        {value}
      </p>
      {subtext && <p className="mt-1 text-xs text-neutral-500">{subtext}</p>}
    </div>
  )
}

export default function SummaryCards({
  nRings,
  nAccountsFlagged,
  totalRupeeAtRisk,
  falsePositiveRate,
  totalAccounts,
}: SummaryCardsProps) {
  return (
    <div className="flex flex-wrap gap-4">
      <Card
        label="Rings found"
        value={String(nRings)}
        accent="danger"
        subtext="at the money-optimal ₹ threshold"
      />
      <Card
        label="Accounts flagged"
        value={String(nAccountsFlagged)}
        accent="danger"
        subtext={totalAccounts !== null ? `vs ${totalAccounts.toLocaleString('en-IN')} total accounts` : undefined}
      />
      <Card
        label="₹ at risk"
        value={formatRupees(totalRupeeAtRisk)}
        accent="danger"
        subtext="at current run's threshold"
      />
      <Card
        label="False-positive rate (benign set)"
        value={falsePositiveRate === null ? '—' : `${(falsePositiveRate * 100).toFixed(1)}%`}
        accent="safe"
        subtext="across all benign look-alike clusters"
      />
    </div>
  )
}
