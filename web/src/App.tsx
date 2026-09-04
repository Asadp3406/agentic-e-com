import { useCallback, useMemo, useState } from 'react'
import { ApiError, getGraph, getRings, runDetection, type GraphPayload, type RingSummary } from './api/client'
import CaseFilePanel from './components/CaseFilePanel'
import NetworkGraph from './components/NetworkGraph'
import RingList from './components/RingList'
import SummaryCards from './components/SummaryCards'
import MetricsView from './views/MetricsView'

type RunPhase = 'idle' | 'running' | 'loading-results' | 'ready' | 'error'
type Tab = 'network' | 'metrics'

export default function App() {
  const [phase, setPhase] = useState<RunPhase>('idle')
  const [error, setError] = useState<string | null>(null)
  const [graph, setGraph] = useState<GraphPayload | null>(null)
  const [rings, setRings] = useState<RingSummary[]>([])
  const [selectedClusterId, setSelectedClusterId] = useState<number | null>(null)
  const [ringsOnly, setRingsOnly] = useState(false)
  const [tab, setTab] = useState<Tab>('network')

  const handleRun = useCallback(async () => {
    setError(null)
    setSelectedClusterId(null)
    setPhase('running')
    try {
      await runDetection()
      setPhase('loading-results')
      const [graphData, ringsData] = await Promise.all([getGraph(), getRings()])
      setGraph(graphData)
      setRings(ringsData.rings)
      setPhase('ready')
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Detection run failed — is the API running on :8000?'
      setError(message)
      setPhase('error')
    }
  }, [])

  const summary = useMemo(() => {
    const suspicious = rings.filter((r) => r.status === 'suspicious')
    const nAccountsFlagged = suspicious.reduce((sum, r) => sum + r.size, 0)
    const totalRupeeAtRisk = suspicious.reduce((sum, r) => sum + r.rupee_risk, 0)
    // False positives on the benign set: a benign-labeled community the engine still marked
    // suspicious. A precise count also lives in GET /api/benign (used by Stage 12's metrics view).
    const benignTotal = rings.filter((r) => r.ground_truth.startsWith('benign')).length
    const benignFalsePositives = rings.filter(
      (r) => r.ground_truth.startsWith('benign') && r.status === 'suspicious',
    ).length
    return {
      nRings: suspicious.length,
      nAccountsFlagged,
      totalRupeeAtRisk,
      falsePositiveRate: benignTotal > 0 ? benignFalsePositives / benignTotal : null,
    }
  }, [rings])

  const isBusy = phase === 'running' || phase === 'loading-results'

  return (
    <div className="flex h-screen flex-col bg-neutral-950 text-neutral-100">
      <header className="flex items-center justify-between border-b border-neutral-800 px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Abuse Ring Sentinel</h1>
          <p className="text-xs text-neutral-500">Graph-based fraud ring detection — engine backed by FastAPI</p>
        </div>
        <div className="flex items-center gap-3">
          {phase === 'ready' && (
            <div className="flex items-center gap-1 rounded-lg border border-neutral-800 bg-neutral-900 p-1 text-xs">
              <button
                onClick={() => setTab('network')}
                className={
                  'rounded-md px-3 py-1.5 font-medium transition-colors ' +
                  (tab === 'network' ? 'bg-neutral-700 text-neutral-100' : 'text-neutral-400 hover:text-neutral-200')
                }
              >
                Network
              </button>
              <button
                onClick={() => setTab('metrics')}
                className={
                  'rounded-md px-3 py-1.5 font-medium transition-colors ' +
                  (tab === 'metrics' ? 'bg-neutral-700 text-neutral-100' : 'text-neutral-400 hover:text-neutral-200')
                }
              >
                Metrics
              </button>
            </div>
          )}
          {phase === 'ready' && tab === 'network' && (
            <label className="flex items-center gap-2 text-xs text-neutral-400">
              <input
                type="checkbox"
                checked={ringsOnly}
                onChange={(e) => setRingsOnly(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-neutral-600 bg-neutral-800 accent-danger"
              />
              Rings only
            </label>
          )}
          <button
            onClick={handleRun}
            disabled={isBusy}
            className="rounded-lg bg-danger px-5 py-2 text-sm font-semibold text-white shadow transition-colors hover:bg-danger-bright disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
          >
            {phase === 'running' && 'Running detection…'}
            {phase === 'loading-results' && 'Loading results…'}
            {(phase === 'idle' || phase === 'error') && 'Run detection'}
            {phase === 'ready' && 'Re-run detection'}
          </button>
        </div>
      </header>

      {error && (
        <div className="border-b border-danger/30 bg-danger-dim/40 px-6 py-2 text-sm text-danger-bright">
          {error}
        </div>
      )}

      <main className="flex flex-1 flex-col gap-4 overflow-hidden p-6">
        {phase === 'ready' && tab === 'network' && (
          <SummaryCards
            nRings={summary.nRings}
            nAccountsFlagged={summary.nAccountsFlagged}
            totalRupeeAtRisk={summary.totalRupeeAtRisk}
            falsePositiveRate={summary.falsePositiveRate}
          />
        )}

        {tab === 'network' ? (
          <div className="flex flex-1 gap-4 overflow-hidden">
            <div className="flex-1 overflow-hidden rounded-xl border border-neutral-800 bg-black">
              {graph ? (
                <NetworkGraph
                  graph={graph}
                  selectedClusterId={selectedClusterId}
                  onSelectCluster={setSelectedClusterId}
                  ringsOnly={ringsOnly}
                />
              ) : (
                <div className="flex h-full items-center justify-center text-sm text-neutral-600">
                  {isBusy ? 'Building the network…' : 'Press "Run detection" to draw the network.'}
                </div>
              )}
            </div>

            <div className="w-[420px] shrink-0">
              <RingList rings={rings} selectedClusterId={selectedClusterId} onSelectRing={setSelectedClusterId} />
            </div>
          </div>
        ) : (
          <div className="flex-1 overflow-hidden rounded-xl border border-neutral-800 bg-neutral-950">
            <MetricsView />
          </div>
        )}
      </main>

      <CaseFilePanel clusterId={selectedClusterId} onClose={() => setSelectedClusterId(null)} />
    </div>
  )
}
