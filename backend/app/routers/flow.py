"""数据流转编排 DAG 拓扑接口：返回节点、连线图结构，以及同一份字段级血缘视图
生成的节点顺序和待复核台账。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.flow import FlowService

router = APIRouter(prefix="/api/flow", tags=["数据流转编排"])
service = FlowService()
LIST_FIELDS = ["管道编号", "管道名称", "起始节点", "终止节点", "中间节点", "流转条件", "并行数量", "管道状态"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# 字段级血缘视图在编排侧的第二条链路：同一快照、节点顺序、待复核台账
# ---------------------------------------------------------------------------
@router.get("/field-view")
def get_field_view(precedence: str | None = Query(default=None)) -> dict[str, Any]:
    """编排页读取与血缘页相同的字段级血缘视图。"""
    return service.field_view(precedence=precedence)


@router.get("/field-view/order")
def get_node_order(
    precedence: str | None = Query(default=None),
    snapshot: str | None = Query(default=None),
) -> dict[str, Any]:
    """按字段级视图的上下游关系编排下一节点。"""
    return service.node_order(precedence=precedence, snapshot=snapshot)


@router.get("/field-view/ledger")
def get_ledger(
    status: str | None = Query(default=None),
    snapshot: str | None = Query(default=None),
) -> dict[str, Any]:
    return service.ledger(status=status, snapshot=snapshot)


@router.post("/field-view/ledger/sync")
def sync_ledger(payload: EntryPayload) -> Any:
    """把字段级血缘视图落到待复核台账；同快照并发写只一次成功，其余 409。"""
    values = payload.values or {}
    result = service.sync_ledger(
        precedence=values.get("precedence"),
        snapshot=values.get("snapshot"),
        batch=values.get("batch"),
        version=values.get("version"),
    )
    if not result.get("ok"):
        return JSONResponse(status_code=409, content=result)
    return result


@router.post("/field-view/ledger/{entry_id}/review")
def review_ledger_entry(entry_id: int, payload: EntryPayload) -> Any:
    values = payload.values or {}
    result = service.review_ledger_entry(
        entry_id,
        result=values.get("result"),
        snapshot=values.get("snapshot"),
        action=values.get("action"),
        reviewer=str(values.get("reviewer") or "值班管理员"),
        remark=str(values.get("remark") or ""),
    )
    if result.get("not_found"):
        raise HTTPException(status_code=404, detail=result["message"])
    if result.get("conflict"):
        return JSONResponse(status_code=409, content=result)
    return result


@router.get("")
def get_graph() -> dict[str, Any]:
    """返回节点与边组成的 DAG 图数据。"""
    return service.get_graph()


@router.get("/export")
def export_entries() -> StreamingResponse:
    nodes, edges = service.raw_data()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(["type", "id", "name", "from", "to", "label", "status"])
    for node in nodes:
        writer.writerow(["node", node.get("id",""), csv_cell(node.get("name","")), "", "", "", node.get("status","")])
    for edge in edges:
        writer.writerow(["edge", "", "", edge.get("from",""), edge.get("to",""), edge.get("label",""), ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=flow.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"流转管道 {entry_id} 不存在")
    return entry


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="流转管道已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry_or_none, message = service.run_action(entry_id, payload.action)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)
