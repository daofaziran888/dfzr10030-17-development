"""数据血缘追踪 DAG 图服务：生成节点、边示例结构与流转规则。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "lineage"
REQUIRED_FIELDS = ["关系编号", "上游节点", "下游节点"]
STATUS_ORDER = ["草稿", "已记录", "已验证", "已归档"]
ACTION_RULES = {"记录关系": "已记录", "验证关系": "已验证", "归档关系": "已归档"}
NEGATIVE_ACTIONS = []


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


class LineageService:
    def get_graph(self) -> dict[str, Any]:
        rows = store.rows(MODULE)
        nodes: list[dict] = []
        edges: list[dict] = []
        for i, row in enumerate(rows):
            nodes.append({"id": int(row.get("id", i+1)), "name": str(row.get("关系编号") or f"LINE-{i+1:04d}"), "status": str(row.get("status", STATUS_ORDER[0])),
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
        # 字段级血缘视图需要来源字段、目标字段、采集批次和关系版本；
        # 登记时带上这些字段，新关系才会进入字段路径解析。
        for field in ("来源字段", "目标字段", "转换说明", "采集批次", "关系版本"):
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"血缘关系 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据血缘追踪可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"血缘关系已{action}"

    def raw_data(self) -> tuple[list[dict], list[dict]]:
        g = self.get_graph()
        return g["nodes"], g["edges"]
