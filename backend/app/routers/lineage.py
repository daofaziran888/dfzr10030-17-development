"""数据血缘追踪接口：DAG 拓扑 + 字段级血缘视图 + 关系版本差异 + 待复核台账。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.field_lineage import field_lineage_service
from app.services.lineage import LineageService

router = APIRouter(prefix="/api/lineage", tags=["数据血缘追踪"])
service = LineageService()
LIST_FIELDS = ["关系编号", "上游节点", "下游节点", "来源字段", "目标字段", "转换说明", "采集批次", "关系版本"]
STATUSES = ["草稿", "已记录", "已验证", "已归档"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# 字段级血缘视图：必须定义在 /{entry_id} 之前，避免被整数路径参数吞掉
# ---------------------------------------------------------------------------
@router.get("/field-view")
def get_field_view(
    precedence: str | None = Query(default=None, description="verified=验证优先；latest=最新优先"),
    batch: str | None = Query(default=None, description="按采集批次过滤"),
    version: str | None = Query(default=None, description="按关系版本过滤"),
) -> dict[str, Any]:
    """按来源字段、目标字段和采集批次解析关系，归并关系版本后形成字段级视图。"""
    return field_lineage_service.view(precedence=precedence or "verified", batch=batch, version=version)


@router.get("/field-view/summary")
def get_field_view_summary(
    precedence: str | None = Query(default=None),
    batch: str | None = Query(default=None),
    version: str | None = Query(default=None),
) -> dict[str, Any]:
    """概览用汇总：只返回指标、快照和明细入口，不携带完整路径。"""
    view = field_lineage_service.view(precedence=precedence or "verified", batch=batch, version=version)
    return {
        "snapshot": view["snapshot"],
        "快照版本": view["快照版本"],
        "precedence": view["precedence"],
        "precedence_label": view["precedence_label"],
        "metrics": view["metrics"],
        "summary": view["summary"],
        "conflicts": view["conflicts"],
        "review": view["review"],
        "detail_entry": "/lineage/field-view",
    }


@router.get("/field-view/batches")
def get_batches() -> dict[str, Any]:
    """采集批次版本登记：字段级血缘视图的第二个输入。"""
    return {"items": field_lineage_service.batches(), "total": len(field_lineage_service.batches())}


@router.get("/field-view/paths/{path_id}")
def get_field_path(
    path_id: str,
    version: str | None = Query(default=None),
    precedence: str | None = Query(default=None),
) -> dict[str, Any]:
    detail = field_lineage_service.path_detail(path_id, version=version, precedence=precedence or "verified")
    if detail is None:
        raise HTTPException(status_code=404, detail=f"字段路径 {path_id} 不存在")
    return detail


@router.get("/field-view/paths/{path_id}/versions")
def get_path_versions(path_id: str, precedence: str | None = Query(default=None)) -> dict[str, Any]:
    versions = field_lineage_service.path_versions(path_id, precedence=precedence or "verified")
    if versions is None:
        raise HTTPException(status_code=404, detail=f"字段路径 {path_id} 不存在")
    return {"items": versions, "total": len(versions), "path_id": path_id}


@router.get("/field-view/paths/{path_id}/diff")
def get_path_diff(
    path_id: str,
    left: str | None = Query(default=None),
    right: str | None = Query(default=None),
    precedence: str | None = Query(default=None),
) -> dict[str, Any]:
    diff = field_lineage_service.path_diff(
        path_id, left_version=left, right_version=right, precedence=precedence or "verified"
    )
    if diff is None:
        raise HTTPException(status_code=404, detail=f"字段路径 {path_id} 不存在")
    return diff


# ---------------------------------------------------------------------------
# 待复核台账：流转编排服务写入的核对结果
# ---------------------------------------------------------------------------
@router.get("/ledger")
def get_ledger(
    status: str | None = Query(default=None),
    snapshot: str | None = Query(default=None),
) -> dict[str, Any]:
    return field_lineage_service.ledger(status=status, snapshot=snapshot)


@router.post("/ledger/sync")
def sync_ledger(payload: EntryPayload) -> Any:
    """血缘服务侧的同步入口：和流转编排服务读取同一份字段级视图。"""
    values = payload.values or {}
    result = field_lineage_service.sync_ledger(
        precedence=str(values.get("precedence") or "verified"),
        snapshot=values.get("snapshot"),
        batch=values.get("batch"),
        version=values.get("version"),
        source="lineage",
    )
    if not result.get("ok"):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content=result)
    return result


@router.post("/ledger/{entry_id}/review")
def review_ledger_entry(entry_id: int, payload: EntryPayload) -> Any:
    values = payload.values or {}
    result = field_lineage_service.review_ledger_entry(
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
        from fastapi.responses import JSONResponse

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
                             headers={"Content-Disposition": "attachment; filename=lineage.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"血缘关系 {entry_id} 不存在")
    return entry


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="血缘关系已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry_or_none, message = service.run_action(entry_id, payload.action)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)
