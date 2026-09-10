"""
ACT-XXIX — Module 3: Data Lineage, Provenance & Schema Versioning
==================================================================

Tracks every datum's lineage through the pipeline: where it came from,
what transformed it, where it went. Supports prediction ancestry queries
("why did we make this prediction?") and decision provenance graphs.

Public surface
--------------
- ``LineageNode``              — a node in the lineage DAG
- ``LineageEdge``              — typed edge (DERIVED_FROM, TRANSFORMED_BY, etc.)
- ``LineageGraph``             — the DAG itself
- ``PredictionAncestry``       — convenience builder + query for predictions
- ``DecisionProvenanceGraph``  — decision → event chain graph
- ``SchemaVersion``            — frozen dataclass for a schema version
- ``SchemaVersioner``          — registry + migration paths
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Edge types
# ---------------------------------------------------------------------------

class EdgeType(str, Enum):
    DERIVED_FROM = "DERIVED_FROM"           # B derived from A
    TRANSFORMED_BY = "TRANSFORMED_BY"        # B transformed by operation A
    PRODUCED_BY = "PRODUCED_BY"              # B produced by component A
    CONSUMED_BY = "CONSUMED_BY"              # A consumed by component B
    VALIDATED_BY = "VALIDATED_BY"            # A validated by check B
    PERSISTED_TO = "PERSISTED_TO"            # A persisted to store B
    READ_FROM = "READ_FROM"                  # A read from source B
    AGGREGATES = "AGGREGATES"                # A aggregates several B's
    TRIGGERS = "TRIGGERS"                    # A triggers event B
    DEPENDS_ON = "DEPENDS_ON"                # soft dependency


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Node + Edge
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LineageNode:
    node_id: str
    kind: str          # e.g. "prediction", "feature", "signal", "market_data"
    name: str          # human-readable
    payload: dict
    ts: str
    hash: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def create(cls, kind: str, name: str, payload: dict,
               node_id: str | None = None,
               ts: str | None = None) -> "LineageNode":
        ts = ts or _now_iso()
        node_id = node_id or f"{kind}_{int(time.time()*1000)}_{hash(name) & 0xFFFFFF:06x}"
        h = _hash_payload({"kind": kind, "name": name, "payload": payload, "ts": ts})
        return cls(node_id=node_id, kind=kind, name=name,
                   payload=payload, ts=ts, hash=h)


@dataclass(frozen=True)
class LineageEdge:
    src_id: str
    dst_id: str
    edge_type: EdgeType
    metadata: dict
    ts: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["edge_type"] = self.edge_type.value
        return d


# ---------------------------------------------------------------------------
# LineageGraph
# ---------------------------------------------------------------------------

class LineageGraph:
    """In-memory lineage DAG with parent/child traversal.

    Supports:
      - add_node / add_edge
      - ancestors(node_id, max_depth) — walk up
      - descendants(node_id, max_depth) — walk down
      - path(from_id, to_id) — BFS path
      - subgraph(root_id, max_depth) — extract subtree
      - verify_integrity() — every edge points to known nodes
    """

    def __init__(self, name: str = "lineage"):
        self.name = name
        self._nodes: dict[str, LineageNode] = {}
        self._out_edges: dict[str, list[LineageEdge]] = defaultdict(list)
        self._in_edges: dict[str, list[LineageEdge]] = defaultdict(list)
        self._lock = threading.RLock()

    def add_node(self, node: LineageNode) -> LineageNode:
        with self._lock:
            self._nodes[node.node_id] = node
        return node

    def add_edge(self, src_id: str, dst_id: str,
                 edge_type: EdgeType = EdgeType.DERIVED_FROM,
                 metadata: dict | None = None) -> LineageEdge:
        with self._lock:
            if src_id not in self._nodes:
                raise KeyError(f"unknown src node: {src_id}")
            if dst_id not in self._nodes:
                raise KeyError(f"unknown dst node: {dst_id}")
            edge = LineageEdge(
                src_id=src_id, dst_id=dst_id,
                edge_type=edge_type,
                metadata=metadata or {},
                ts=_now_iso(),
            )
            self._out_edges[src_id].append(edge)
            self._in_edges[dst_id].append(edge)
            return edge

    def get_node(self, node_id: str) -> LineageNode | None:
        with self._lock:
            return self._nodes.get(node_id)

    def ancestors(self, node_id: str,
                  max_depth: int = 10) -> list[tuple[LineageNode, int]]:
        """Return [(ancestor_node, depth_from_node)] walking up the DAG."""
        with self._lock:
            if node_id not in self._nodes:
                raise KeyError(f"unknown node: {node_id}")
            visited: dict[str, int] = {node_id: 0}
            queue: deque[tuple[str, int]] = deque([(node_id, 0)])
            out: list[tuple[LineageNode, int]] = []
            while queue:
                nid, depth = queue.popleft()
                if depth >= max_depth:
                    continue
                for edge in self._in_edges.get(nid, []):
                    src_id = edge.src_id
                    if src_id not in visited:
                        visited[src_id] = depth + 1
                        out.append((self._nodes[src_id], depth + 1))
                        queue.append((src_id, depth + 1))
            return out

    def descendants(self, node_id: str,
                    max_depth: int = 10) -> list[tuple[LineageNode, int]]:
        """Return [(descendant_node, depth_from_node)] walking down."""
        with self._lock:
            if node_id not in self._nodes:
                raise KeyError(f"unknown node: {node_id}")
            visited: dict[str, int] = {node_id: 0}
            queue: deque[tuple[str, int]] = deque([(node_id, 0)])
            out: list[tuple[LineageNode, int]] = []
            while queue:
                nid, depth = queue.popleft()
                if depth >= max_depth:
                    continue
                for edge in self._out_edges.get(nid, []):
                    dst_id = edge.dst_id
                    if dst_id not in visited:
                        visited[dst_id] = depth + 1
                        out.append((self._nodes[dst_id], depth + 1))
                        queue.append((dst_id, depth + 1))
            return out

    def path(self, from_id: str, to_id: str) -> list[str] | None:
        """BFS shortest path. Returns list of node_ids or None."""
        with self._lock:
            if from_id not in self._nodes or to_id not in self._nodes:
                return None
            if from_id == to_id:
                return [from_id]
            visited = {from_id}
            queue: deque[list[str]] = deque([[from_id]])
            while queue:
                path = queue.popleft()
                last = path[-1]
                for edge in self._out_edges.get(last, []):
                    nxt = edge.dst_id
                    if nxt in visited:
                        continue
                    new_path = path + [nxt]
                    if nxt == to_id:
                        return new_path
                    visited.add(nxt)
                    queue.append(new_path)
            return None

    def subgraph(self, root_id: str, max_depth: int = 5) -> "LineageGraph":
        """Extract a sub-DAG rooted at ``root_id`` containing descendants."""
        with self._lock:
            sg = LineageGraph(name=f"{self.name}_sub_{root_id}")
            if root_id not in self._nodes:
                return sg
            sg.add_node(self._nodes[root_id])
            seen: set[str] = {root_id}
            queue: deque[tuple[str, int]] = deque([(root_id, 0)])
            while queue:
                nid, depth = queue.popleft()
                if depth >= max_depth:
                    continue
                for edge in self._out_edges.get(nid, []):
                    dst_id = edge.dst_id
                    if dst_id not in seen:
                        seen.add(dst_id)
                        sg.add_node(self._nodes[dst_id])
                        queue.append((dst_id, depth + 1))
                    # Re-add edge
                    sg.add_edge(edge.src_id, edge.dst_id,
                                edge.edge_type, dict(edge.metadata))
            return sg

    def verify_integrity(self) -> tuple[bool, list[str]]:
        """Check every edge points to known nodes."""
        with self._lock:
            errs: list[str] = []
            for src_id, edges in self._out_edges.items():
                if src_id not in self._nodes:
                    errs.append(f"edge src missing: {src_id}")
                for e in edges:
                    if e.dst_id not in self._nodes:
                        errs.append(f"edge dst missing: {e.dst_id}")
            return (len(errs) == 0, errs)

    def node_count(self) -> int:
        with self._lock:
            return len(self._nodes)

    def edge_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._out_edges.values())

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "nodes": [n.to_dict() for n in self._nodes.values()],
                "edges": [e.to_dict() for edges in self._out_edges.values()
                          for e in edges],
            }


# ---------------------------------------------------------------------------
# PredictionAncestry — high-level helper
# ---------------------------------------------------------------------------

class PredictionAncestry:
    """Builds a lineage graph for the prediction pipeline.

    Standard pipeline stages (each becomes a node):
      market_data -> features -> signal -> prediction -> routing_decision
                                          -> verification -> outcome
    """

    def __init__(self, graph: LineageGraph | None = None):
        self.graph = graph or LineageGraph(name="prediction_ancestry")

    def record_market_data(self, symbol: str, timeframe: str,
                           raw_payload: dict) -> LineageNode:
        node = LineageNode.create(
            kind="market_data",
            name=f"{symbol}_{timeframe}",
            payload={"symbol": symbol, "timeframe": timeframe, **raw_payload},
        )
        self.graph.add_node(node)
        return node

    def record_features(self, parent: LineageNode, features: dict,
                        transformer: str = "feature_engine") -> LineageNode:
        node = LineageNode.create(
            kind="features",
            name=f"features_for_{parent.name}",
            payload={"features": features, "transformer": transformer},
        )
        self.graph.add_node(node)
        self.graph.add_edge(parent.node_id, node.node_id,
                            EdgeType.TRANSFORMED_BY,
                            {"transformer": transformer})
        return node

    def record_signal(self, feature_node: LineageNode,
                      direction: str, confidence: float,
                      meta: dict | None = None) -> LineageNode:
        node = LineageNode.create(
            kind="signal",
            name=f"signal_{direction}_{feature_node.name}",
            payload={"direction": direction, "confidence": confidence,
                     "meta": meta or {}},
        )
        self.graph.add_node(node)
        self.graph.add_edge(feature_node.node_id, node.node_id,
                            EdgeType.DERIVED_FROM)
        return node

    def record_prediction(self, signal_node: LineageNode,
                          prediction_id: str,
                          final_confidence: float,
                          meta: dict | None = None) -> LineageNode:
        node = LineageNode.create(
            kind="prediction",
            name=f"pred_{prediction_id}",
            node_id=f"prediction_{prediction_id}",
            payload={"prediction_id": prediction_id,
                     "confidence": final_confidence,
                     "meta": meta or {}},
        )
        self.graph.add_node(node)
        self.graph.add_edge(signal_node.node_id, node.node_id,
                            EdgeType.DERIVED_FROM)
        return node

    def record_outcome(self, pred_node: LineageNode,
                       outcome: str, realized_return: float | None) -> LineageNode:
        node = LineageNode.create(
            kind="outcome",
            name=f"outcome_{pred_node.name}",
            payload={"outcome": outcome,
                     "realized_return": realized_return},
        )
        self.graph.add_node(node)
        self.graph.add_edge(pred_node.node_id, node.node_id,
                            EdgeType.PRODUCED_BY,
                            {"producer": "verifier"})
        return node

    def ancestry_of(self, prediction_id: str,
                    max_depth: int = 10) -> list[tuple[LineageNode, int]]:
        """Walk up the DAG from a prediction node."""
        return self.graph.ancestors(f"prediction_{prediction_id}", max_depth)

    def descendants_of(self, prediction_id: str,
                       max_depth: int = 10) -> list[tuple[LineageNode, int]]:
        """Walk down the DAG from a prediction node."""
        return self.graph.descendants(f"prediction_{prediction_id}", max_depth)

    def explain(self, prediction_id: str) -> dict:
        """Produce a human-readable provenance report."""
        pred = self.graph.get_node(f"prediction_{prediction_id}")
        if pred is None:
            return {"error": "prediction not found"}
        anc = self.ancestry_of(prediction_id)
        desc = self.descendants_of(prediction_id)
        return {
            "prediction": pred.to_dict(),
            "ancestors": [
                {"node": n.to_dict(), "depth": d} for n, d in anc
            ],
            "descendants": [
                {"node": n.to_dict(), "depth": d} for n, d in desc
            ],
            "depth_reached_up": max((d for _, d in anc), default=0),
            "depth_reached_down": max((d for _, d in desc), default=0),
            "graph_size": {
                "nodes": self.graph.node_count(),
                "edges": self.graph.edge_count(),
            },
        }


# ---------------------------------------------------------------------------
# DecisionProvenanceGraph — decision → event chain
# ---------------------------------------------------------------------------

class DecisionProvenanceGraph:
    """High-level wrapper that records decisions as lineage nodes and
    links them to the inputs/outputs they produced.
    """

    def __init__(self, graph: LineageGraph | None = None):
        self.graph = graph or LineageGraph(name="decision_provenance")

    def record_decision(self, decision_id: str, actor: str,
                        inputs: list[LineageNode], outputs: list[LineageNode],
                        rationale: str = "") -> LineageNode:
        node = LineageNode.create(
            kind="decision",
            name=f"decision_{decision_id}",
            node_id=f"decision_{decision_id}",
            payload={"actor": actor, "rationale": rationale,
                     "input_ids": [n.node_id for n in inputs],
                     "output_ids": [n.node_id for n in outputs]},
        )
        self.graph.add_node(node)
        for inp in inputs:
            self.graph.add_edge(inp.node_id, node.node_id,
                                EdgeType.CONSUMED_BY)
        for outp in outputs:
            self.graph.add_edge(node.node_id, outp.node_id,
                                EdgeType.PRODUCED_BY)
        return node

    def trace_decision(self, decision_id: str) -> dict:
        node = self.graph.get_node(f"decision_{decision_id}")
        if node is None:
            return {"error": "decision not found"}
        return {
            "decision": node.to_dict(),
            "inputs": [n.to_dict() for n, _ in
                       self.graph.ancestors(f"decision_{decision_id}", 1)],
            "outputs": [n.to_dict() for n, _ in
                        self.graph.descendants(f"decision_{decision_id}", 1)],
        }


# ---------------------------------------------------------------------------
# SchemaVersion + SchemaVersioner
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SchemaVersion:
    """A versioned schema definition.

    ``migrate_from`` is a callable that takes an old-version payload and
    returns a new-version payload. ``None`` means no migration possible.
    """
    kind: str             # e.g. "prediction_record"
    version: int          # 1, 2, 3, ...
    schema: dict          # JSON-schema-like spec
    introduced_at: str    # ISO timestamp
    deprecated: bool = False
    migrate_from: int | None = None  # prior version this can migrate from

    def to_dict(self) -> dict:
        return asdict(self)


class SchemaVersioner:
    """Registry of schema versions + migration paths.

    Usage:
        sv = SchemaVersioner()
        sv.register(SchemaVersion("prediction", 1, {...}))
        sv.register(SchemaVersion("prediction", 2, {...}, migrate_from=1))
        latest = sv.latest_version("prediction")
        migrated, ok = sv.migrate("prediction", 1, 2, old_payload)
    """

    def __init__(self):
        self._registry: dict[str, dict[int, SchemaVersion]] = defaultdict(dict)
        self._migrators: dict[tuple[str, int, int], Callable[[dict], dict]] = {}
        self._lock = threading.Lock()

    def register(self, ver: SchemaVersion,
                 migrator: Callable[[dict], dict] | None = None) -> None:
        with self._lock:
            self._registry[ver.kind][ver.version] = ver
            if migrator is not None and ver.migrate_from is not None:
                self._migrators[(ver.kind, ver.migrate_from, ver.version)] = migrator

    def latest_version(self, kind: str) -> int | None:
        with self._lock:
            versions = [v for v, sv in self._registry.get(kind, {}).items()
                        if not sv.deprecated]
            return max(versions) if versions else None

    def get_schema(self, kind: str, version: int) -> SchemaVersion | None:
        with self._lock:
            return self._registry.get(kind, {}).get(version)

    def migration_path(self, kind: str, from_v: int,
                       to_v: int) -> list[int] | None:
        """Compute the chain of intermediate versions to migrate from→to."""
        with self._lock:
            if from_v == to_v:
                return [from_v]
            # Greedy: try to migrate forward step by step
            path = [from_v]
            cur = from_v
            while cur < to_v:
                # Find a migrator from cur to something higher
                next_v = None
                for (k, f, t) in self._migrators.keys():
                    if k == kind and f == cur and t <= to_v:
                        if next_v is None or t > next_v:
                            next_v = t
                if next_v is None:
                    return None
                path.append(next_v)
                cur = next_v
            return path

    def migrate(self, kind: str, from_v: int, to_v: int,
                payload: dict) -> tuple[dict, bool]:
        """Migrate ``payload`` from ``from_v`` to ``to_v``.

        Returns (migrated_payload, ok). On failure returns (payload, False).
        """
        path = self.migration_path(kind, from_v, to_v)
        if path is None:
            return payload, False
        cur_payload = dict(payload)
        cur_v = from_v
        for nxt_v in path[1:]:
            migrator = self._migrators.get((kind, cur_v, nxt_v))
            if migrator is None:
                return payload, False
            try:
                cur_payload = migrator(cur_payload)
            except Exception:
                return payload, False
            cur_v = nxt_v
        return cur_payload, True

    def list_versions(self, kind: str) -> list[int]:
        with self._lock:
            return sorted(self._registry.get(kind, {}).keys())


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "EdgeType",
    "LineageNode",
    "LineageEdge",
    "LineageGraph",
    "PredictionAncestry",
    "DecisionProvenanceGraph",
    "SchemaVersion",
    "SchemaVersioner",
]
