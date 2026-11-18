"""数据流转编排 DAG 图服务：生成节点、边示例结构与流转规则。

编排服务不重复实现字段路径解析：它通过 ``FieldLineageService`` 这个链路读取
与血缘页相同的字段级血缘视图快照，据此给出节点顺序，并把视图落到待复核台账；
复核结果再写回字段级血缘视图。
"""
from __future__ import annotations

from typing import Any

from app.services.field_lineage import PRECEDENCE_LATEST, PRECEDENCE_VERIFIED, field_lineage_service
from app.store import store

MODULE = "flow"
REQUIRED_FIELDS = ["管道编号", "管道名称", "起始节点"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用管道": "已启用", "停用管道": "已停用"}
NEGATIVE_ACTIONS = ["停用管道"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


class FlowService:
    def get_graph(self) -> dict[str, Any]:
        rows = store.rows(MODULE)
        nodes: list[dict] = []
        edges: list[dict] = []
        for i, row in enumerate(rows):
            nodes.append({"id": int(row.get("id", i+1)), "name": str(row.get(list(row.keys())[1] if len(row)>1 else "name", "")), "status": str(row.get("status", STATUS_ORDER[0])),
                          **{k: row[k] for k in row if k not in ("id","status","pending","abnormal")}})
        for i in range(len(nodes)-1):
            edges.append({"from": nodes[i]["id"], "to": nodes[i+1]["id"], "label": "依赖"})
        return {"nodes": nodes, "edges": edges}

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id",0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"流转管道 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据流转编排可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"流转管道已{action}"

    def raw_data(self) -> tuple[list[dict], list[dict]]:
        g = self.get_graph()
        return g["nodes"], g["edges"]

    # ------------------------------------------------------------------
    # 字段级血缘视图的第二个消费方：编排顺序 + 待复核台账 + 核对结果写回
    # ------------------------------------------------------------------
    def field_view(self, precedence: str | None = None) -> dict[str, Any]:
        """和血缘页读取同一份字段级视图，保证两个页面看到同一快照。"""
        return field_lineage_service.view(precedence=precedence or PRECEDENCE_VERIFIED)

    def node_order(
        self,
        precedence: str | None = None,
        snapshot: str | None = None,
    ) -> dict[str, Any]:
        order = field_lineage_service.node_order(
            precedence=precedence or PRECEDENCE_VERIFIED
        )
        order["source"] = "flow"
        if snapshot and snapshot != order["snapshot"]:
            order["stale_snapshot"] = {
                "requested": snapshot,
                "current": order["snapshot"],
                "message": "请求的字段级血缘快照已更新，编排顺序按当前快照给出",
            }
        return order

    def sync_ledger(
        self,
        precedence: str | None = None,
        snapshot: str | None = None,
        batch: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        return field_lineage_service.sync_ledger(
            precedence=precedence or PRECEDENCE_VERIFIED,
            snapshot=snapshot,
            batch=batch,
            version=version,
            source="flow",
        )

    def ledger(self, status: str | None = None, snapshot: str | None = None) -> dict[str, Any]:
        return field_lineage_service.ledger(status=status, snapshot=snapshot)

    def review_ledger_entry(self, entry_id: int, **kwargs: Any) -> dict[str, Any]:
        return field_lineage_service.review_ledger_entry(entry_id, **kwargs)
