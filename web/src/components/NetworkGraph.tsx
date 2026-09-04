// Stage 11: interactive force-graph of the customer network, rings in red and benign clusters in green.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods, type NodeObject, type LinkObject } from 'react-force-graph-2d'
import type { GraphEdge, GraphNode, GraphPayload } from '../api/client'

interface NetworkGraphProps {
  graph: GraphPayload
  selectedClusterId: number | null
  onSelectCluster: (clusterId: number | null) => void
  ringsOnly: boolean
}

type FGNode = NodeObject<GraphNode>
type FGLink = LinkObject<GraphNode, GraphEdge>

const ENTITY_COLOR = '#525252' // neutral-600, non-customer entity nodes (device/card/phone/...)
const RING_COLOR = '#ef4444' // danger
const BENIGN_COLOR = '#22c55e' // safe
const DIM_COLOR = 'rgba(115, 115, 115, 0.25)'

function nodeColor(node: FGNode, selectedClusterId: number | null, clusterEntityIds: Set<string>): string {
  if (node.type !== 'customer') {
    if (selectedClusterId !== null) return clusterEntityIds.has(node.id) ? ENTITY_COLOR : DIM_COLOR
    return ENTITY_COLOR
  }
  const isDimmed = selectedClusterId !== null && node.cluster_id !== selectedClusterId
  if (isDimmed) return DIM_COLOR
  return node.suspicious ? RING_COLOR : BENIGN_COLOR
}

function nodeRadius(node: FGNode): number {
  if (node.type !== 'customer') return 2
  return 3 + node.risk * 9
}

function linkEndId(end: string | GraphNode): string {
  return typeof end === 'string' ? end : end.id
}

export default function NetworkGraph({ graph, selectedClusterId, onSelectCluster, ringsOnly }: NetworkGraphProps) {
  const fgRef = useRef<ForceGraphMethods<FGNode, FGLink> | undefined>(undefined)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })
  const [hoverNode, setHoverNode] = useState<FGNode | null>(null)

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

  const filteredData = useMemo(() => {
    if (!ringsOnly) {
      return { nodes: graph.nodes as FGNode[], links: graph.edges as unknown as FGLink[] }
    }
    const ringClusterIds = new Set(
      graph.nodes.filter((n) => n.type === 'customer' && n.suspicious).map((n) => n.cluster_id),
    )
    const keepIds = new Set(
      graph.nodes
        .filter((n) => n.type === 'customer' && ringClusterIds.has(n.cluster_id))
        .map((n) => n.id),
    )
    // Keep entity nodes that connect to a kept customer, so shared-entity edges still render.
    const entityIds = new Set(
      graph.edges
        .filter((e) => keepIds.has(e.source) || keepIds.has(e.target))
        .flatMap((e) => [e.source, e.target]),
    )
    const nodes = graph.nodes.filter((n) => keepIds.has(n.id) || entityIds.has(n.id)) as FGNode[]
    const nodeIdSet = new Set(nodes.map((n) => n.id))
    const links = graph.edges.filter(
      (e) => nodeIdSet.has(e.source) && nodeIdSet.has(e.target),
    ) as unknown as FGLink[]
    return { nodes, links }
  }, [graph, ringsOnly])

  // Entity nodes (device/card/...) directly shared by the selected cluster's customers —
  // these have cluster_id: null themselves, but should stay lit up (not dimmed) alongside
  // the customer nodes they connect, and should count toward the focus zoom's bounding box.
  const selectedClusterEntityIds = useMemo(() => {
    if (selectedClusterId === null) return new Set<string>()
    const memberIds = new Set(
      graph.nodes.filter((n) => n.type === 'customer' && n.cluster_id === selectedClusterId).map((n) => n.id),
    )
    return new Set(
      graph.edges
        .filter((e) => memberIds.has(e.source) || memberIds.has(e.target))
        .flatMap((e) => [e.source, e.target])
        .filter((id) => !memberIds.has(id)),
    )
  }, [graph, selectedClusterId])

  useEffect(() => {
    // Let the sim settle then gently zoom to fit — the "network forming" effect.
    const fg = fgRef.current
    if (!fg) return
    const t = setTimeout(() => {
      fg.zoomToFit(600, 40)
    }, 900)
    return () => clearTimeout(t)
  }, [filteredData])

  useEffect(() => {
    // Focusing a cluster: zoom in on just its nodes (+ its shared entities) instead of the
    // whole network.
    const fg = fgRef.current
    if (!fg || selectedClusterId === null) return
    const t = setTimeout(() => {
      fg.zoomToFit(
        500,
        120,
        (n) => (n as FGNode).cluster_id === selectedClusterId || selectedClusterEntityIds.has((n as FGNode).id),
      )
    }, 50)
    return () => clearTimeout(t)
  }, [selectedClusterId, selectedClusterEntityIds])

  const handleNodeClick = useCallback(
    (node: FGNode) => {
      if (node.type !== 'customer' || node.cluster_id === null) return
      onSelectCluster(selectedClusterId === node.cluster_id ? null : node.cluster_id)
    },
    [onSelectCluster, selectedClusterId],
  )

  const handleBackgroundClick = useCallback(() => {
    onSelectCluster(null)
  }, [onSelectCluster])

  return (
    <div className="relative h-full w-full" ref={containerRef}>
      <ForceGraph2D<GraphNode, GraphEdge>
        ref={fgRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={filteredData}
        backgroundColor="#0a0a0a"
        nodeId="id"
        nodeLabel={() => ''}
        nodeColor={(n) => nodeColor(n as FGNode, selectedClusterId, selectedClusterEntityIds)}
        nodeVal={(n) => nodeRadius(n as FGNode) ** 2}
        nodeRelSize={1}
        linkColor={(l) => {
          const link = l as unknown as GraphEdge & { source: string | GraphNode; target: string | GraphNode }
          const srcId = linkEndId(link.source)
          const tgtId = linkEndId(link.target)
          if (selectedClusterId === null) return `rgba(163,163,163,${0.15 + link.weight * 0.4})`
          const srcNode = graph.nodes.find((n) => n.id === srcId)
          const tgtNode = graph.nodes.find((n) => n.id === tgtId)
          const inCluster = srcNode?.cluster_id === selectedClusterId || tgtNode?.cluster_id === selectedClusterId
          return inCluster ? `rgba(248,113,113,${0.3 + link.weight * 0.5})` : 'rgba(82,82,82,0.08)'
        }}
        linkWidth={(l) => 0.5 + (l as unknown as GraphEdge).weight * 2}
        onNodeClick={(n) => handleNodeClick(n as FGNode)}
        onNodeHover={(n) => setHoverNode(n as FGNode | null)}
        onBackgroundClick={handleBackgroundClick}
        cooldownTicks={100}
        onEngineStop={() => fgRef.current?.zoomToFit(400, 40)}
      />

      {hoverNode && (
        <div
          className="pointer-events-none absolute left-3 top-3 max-w-xs rounded-lg border border-neutral-700 bg-neutral-900/95 px-3 py-2 text-xs shadow-lg"
        >
          <p className="font-semibold text-neutral-100">{hoverNode.id}</p>
          <p className="text-neutral-400">type: {hoverNode.type}</p>
          {hoverNode.type === 'customer' && (
            <>
              <p className={hoverNode.suspicious ? 'text-danger-bright' : 'text-safe-bright'}>
                {hoverNode.suspicious ? 'ring member' : 'benign / unflagged'}
              </p>
              <p className="text-neutral-400">risk score: {hoverNode.risk.toFixed(3)}</p>
              {hoverNode.cluster_id !== null && (
                <p className="text-neutral-400">cluster #{hoverNode.cluster_id}</p>
              )}
              {(hoverNode.ring_id || hoverNode.cluster_tag) && (
                <p className="text-neutral-500">{hoverNode.ring_id || hoverNode.cluster_tag}</p>
              )}
            </>
          )}
          {hoverNode.type !== 'customer' && hoverNode.entity_id && (
            <p className="text-neutral-400 break-all">{hoverNode.entity_id}</p>
          )}
        </div>
      )}

      <Legend />
    </div>
  )
}

function Legend() {
  return (
    <div className="absolute bottom-3 left-3 flex flex-col gap-1.5 rounded-lg border border-neutral-700 bg-neutral-900/95 px-3 py-2.5 text-xs shadow-lg">
      <LegendRow color={RING_COLOR} label="Ring member (suspicious)" />
      <LegendRow color={BENIGN_COLOR} label="Benign / unflagged" />
      <LegendRow color={ENTITY_COLOR} label="Shared entity (device, card, address, ...)" />
      <p className="mt-1 text-[10px] text-neutral-500">Node size = risk score. Line thickness = edge weight.</p>
    </div>
  )
}

function LegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
      <span className="text-neutral-300">{label}</span>
    </div>
  )
}
