"""字段级血缘视图与待复核台账。

这个模块只依赖内存仓库，负责四件事：

1. 把「血缘关系记录」按 ``上游节点 + 来源字段`` 归组，再沿
   ``目标字段`` 串成字段路径；
2. 把关系记录里的 ``关系版本`` 与 ``采集批次`` 版本登记合并成一个稳定的
   快照（snapshot），字段路径必须绑定同一个快照里的版本和批次；
3. 在同一个来源字段存在多条关系时，按明确的前置规则挑选**唯一**的当前
   目标：``verified``（验证状态优先）或 ``latest``（最新记录优先）。旧关系
   不删除，作为历史差异保留；
4. 给流转编排服务提供同一份视图快照，并把编排侧写回的核对结果合并回路径。

快照只由关系数据和批次版本决定，复核结果不参与快照计算。这样一次复核
写回后不会让同批次的其它台账项一起失效，同时又能通过台账历史保留旧结果。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import defaultdict
from typing import Any

from app.store import store

LINEAGE_MODULE = "lineage"

PRECEDENCE_VERIFIED = "verified"
PRECEDENCE_LATEST = "latest"
PRECEDENCE_LABELS = {
    PRECEDENCE_VERIFIED: "验证优先",
    PRECEDENCE_LATEST: "最新优先",
}

STATUS_DRAFT = "草稿"
STATUS_RECORDED = "已记录"
STATUS_VERIFIED = "已验证"
STATUS_ARCHIVED = "已归档"
STATUS_RANK = {
    STATUS_VERIFIED: 3,
    STATUS_RECORDED: 2,
    STATUS_DRAFT: 1,
    STATUS_ARCHIVED: 0,
}

# 采集批次版本登记：字段级视图的第二个输入。真实项目里来自批次表。
BATCH_VERSIONS: list[dict[str, Any]] = [
    {
        "采集批次": "BATCH-2026-09A",
        "批次版本": "v1",
        "批次状态": "已确认",
        "采集时间": "2026-09-12",
        "来源系统": "订单采集通道",
    },
    {
        "采集批次": "BATCH-2026-09B",
        "批次版本": "v2",
        "批次状态": "已校验",
        "采集时间": "2026-09-18",
        "来源系统": "订单采集通道",
    },
    {
        "采集批次": "BATCH-2026-09C",
        "批次版本": "v1",
        "批次状态": "已确认",
        "采集时间": "2026-09-25",
        "来源系统": "报表采集通道",
    },
]
BATCH_ORDER = {item["采集批次"]: index for index, item in enumerate(BATCH_VERSIONS)}
BATCH_META = {item["采集批次"]: item for item in BATCH_VERSIONS}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _version_number(value: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", value or ""))
    return parts or (0,)


def _status_rank(value: str) -> int:
    return STATUS_RANK.get(value, 0)


def _batch_rank(value: str) -> int:
    return BATCH_ORDER.get(value, -1)


def _copy(value: Any) -> Any:
    """仅复制 JSON 友好的字典/列表，避免台账被调用方原位修改。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def normalize_precedence(value: str | None) -> str:
    """把前端/接口传入的多种写法归一成两种业务边界。"""
    text = _text(value).lower()
    if text in {"latest", "newest", "record", "record_first", "latest_first", "最新", "最新优先", "记录优先"}:
        return PRECEDENCE_LATEST
    return PRECEDENCE_VERIFIED


def normalize_result(value: str | None, action: str | None = None) -> str:
    text = _text(value) or _text(action)
    lower = text.lower()
    if lower in {"pass", "passed", "agree", "ok", "一致", "复核通过", "通过", "确认一致"}:
        return "一致"
    if lower in {"diff", "different", "reject", "存在差异", "标记差异", "驳回"}:
        return "存在差异"
    return text or "一致"


class FieldLineageService:
    """字段级血缘视图、关系版本差异和流转复核台账的共同数据源。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (snapshot, path_id) -> 复核结果；字段级视图读取这里的核对结果。
        self._review_results: dict[tuple[str, str], dict[str, Any]] = {}
        # 流转编排服务落下来的待复核台账。
        self._ledger: list[dict[str, Any]] = []
        self._ledger_seq = 0
        # snapshot -> 首次同步时间。用于拒绝同快照的重复/并发写入。
        self._synced_snapshots: dict[str, str] = {}
        self._review_seq = 0

    # ------------------------------------------------------------------
    # 输入解析：血缘关系记录 + 采集批次版本
    # ------------------------------------------------------------------
    def batches(self) -> list[dict[str, Any]]:
        return _copy(BATCH_VERSIONS)

    def _load_relations(self, batch: str | None = None, version: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(LINEAGE_MODULE)
        relations: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            source_field = _text(row.get("来源字段"))
            target_field = _text(row.get("目标字段"))
            upstream = _text(row.get("上游节点"))
            downstream = _text(row.get("下游节点"))
            if not source_field or not target_field or not upstream or not downstream:
                continue
            row_batch = _text(row.get("采集批次"))
            row_version = _text(row.get("关系版本"))
            if batch and row_batch != batch:
                continue
            if version and row_version != version:
                continue
            relations.append({
                **row,
                "关系编号": _text(row.get("关系编号")) or f"LINE-{index + 1:04d}",
                "上游节点": upstream,
                "下游节点": downstream,
                "来源字段": source_field,
                "目标字段": target_field,
                "转换说明": _text(row.get("转换说明")) or "无转换说明",
                "采集批次": row_batch,
                "关系版本": row_version or "v0",
                "关系状态": _text(row.get("status")) or STATUS_DRAFT,
                "_id": int(row.get("id", index + 1)),
                "_version_num": _version_number(row_version),
                "_batch_rank": _batch_rank(row_batch),
                "_status_rank": _status_rank(_text(row.get("status"))),
                "_batch_meta": BATCH_META.get(row_batch, {}),
                "_group_key": (upstream, source_field),
            })
        relations.sort(key=lambda item: item["_id"])
        return relations

    def _snapshot(
        self,
        relations: list[dict[str, Any]],
        batch: str | None = None,
        version: str | None = None,
        precedence: str | None = None,
    ) -> str:
        """快照只由字段级视图的输入决定，复核结果不参与，避免写回后视图漂移。"""
        payload = [
            {
                "关系编号": row["关系编号"],
                "上游节点": row["上游节点"],
                "下游节点": row["下游节点"],
                "来源字段": row["来源字段"],
                "目标字段": row["目标字段"],
                "转换说明": row["转换说明"],
                "采集批次": row["采集批次"],
                "关系版本": row["关系版本"],
                "关系状态": row["关系状态"],
                "id": row["_id"],
            }
            for row in sorted(relations, key=lambda item: item["_id"])
        ]
        payload.append(BATCH_VERSIONS)
        payload.append({"batch": batch or "", "version": version or "", "precedence": precedence or ""})
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return f"SNAP-{digest[:12].upper()}"

    # ------------------------------------------------------------------
    # 当前关系选择：两种业务边界都只产出唯一可执行目标
    # ------------------------------------------------------------------
    def _select_current(self, group: list[dict[str, Any]], precedence: str) -> dict[str, Any] | None:
        live = [row for row in group if row["关系状态"] != STATUS_ARCHIVED]
        if not live:
            return None
        if precedence == PRECEDENCE_LATEST:
            key = lambda row: (row["_version_num"], row["_batch_rank"], row["_id"])
        else:
            # 验证状态前置：已验证关系优先成为当前字段路径，不被处理先后覆盖；
            # 同为已验证时再看关系版本，保证同批次内的选择可复现。
            key = lambda row: (row["_status_rank"], row["_version_num"], row["_batch_rank"], row["_id"])
        return max(live, key=key)

    def _latest_record(self, group: list[dict[str, Any]]) -> dict[str, Any] | None:
        live = [row for row in group if row["关系状态"] != STATUS_ARCHIVED]
        if not live:
            return None
        return max(live, key=lambda row: (row["_version_num"], row["_batch_rank"], row["_id"]))

    def _version_rows(self, group: list[dict[str, Any]], current: dict[str, Any] | None) -> list[dict[str, Any]]:
        rows = sorted(
            group,
            key=lambda row: (row["_version_num"], row["_batch_rank"], row["_id"]),
            reverse=True,
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            output.append({
                "关系编号": row["关系编号"],
                "关系版本": row["关系版本"],
                "采集批次": row["采集批次"],
                "批次版本": row["_batch_meta"].get("批次版本", ""),
                "批次状态": row["_batch_meta"].get("批次状态", ""),
                "转换说明": row["转换说明"],
                "关系状态": row["关系状态"],
                "上游节点": row["上游节点"],
                "下游节点": row["下游节点"],
                "来源字段": row["来源字段"],
                "目标字段": row["目标字段"],
                "是否当前": bool(current and row["_id"] == current["_id"]),
                "是否最新记录": False,
            })
        latest = self._latest_record(group)
        for item in output:
            if latest and item["关系编号"] == latest["关系编号"]:
                item["是否最新记录"] = True
        return output

    def _group_conflict(self, group: list[dict[str, Any]], current: dict[str, Any] | None) -> dict[str, Any] | None:
        live = [row for row in group if row["关系状态"] != STATUS_ARCHIVED]
        if len(live) <= 1:
            return None
        targets = {row["目标字段"] for row in live}
        batches = {row["采集批次"] for row in live}
        statuses = {row["关系状态"] for row in live}
        if len(targets) > 1:
            conflict_type = "目标字段冲突"
        elif len(batches) == 1 and len(statuses) > 1:
            conflict_type = "同批次验证状态冲突"
        elif len(statuses) > 1:
            conflict_type = "跨批次状态冲突"
        else:
            conflict_type = "关系版本差异"
        return {
            "冲突类型": conflict_type,
            "上游节点": live[0]["上游节点"],
            "来源字段": live[0]["来源字段"],
            "候选关系数": len(live),
            "当前关系编号": current["关系编号"] if current else "",
            "候选目标字段": sorted(targets),
            "候选批次": sorted(batches),
        }

    def _step_view(
        self,
        row: dict[str, Any],
        group: list[dict[str, Any]],
        executable: bool,
        blocked_by: str = "",
    ) -> dict[str, Any]:
        latest = self._latest_record(group)
        versions = self._version_rows(group, row)
        stale = bool(latest and row["_id"] != latest["_id"])
        uses_old_transform = bool(stale and latest and row["转换说明"] == latest["转换说明"])
        if stale:
            if uses_old_transform:
                difference = "沿用旧转换说明"
            else:
                difference = "转换说明已更新"
        else:
            difference = "当前最新"
        return {
            "关系编号": row["关系编号"],
            "上游节点": row["上游节点"],
            "下游节点": row["下游节点"],
            "来源字段": row["来源字段"],
            "目标字段": row["目标字段"],
            "字段路径": f"{row['上游节点']}.{row['来源字段']} → {row['下游节点']}.{row['目标字段']}",
            "转换说明": row["转换说明"],
            "采集批次": row["采集批次"],
            "批次版本": row["_batch_meta"].get("批次版本", ""),
            "批次状态": row["_batch_meta"].get("批次状态", ""),
            "关系版本": row["关系版本"],
            "关系状态": row["关系状态"],
            "是否当前": True,
            "是否最新记录": bool(latest and latest["_id"] == row["_id"]),
            "沿用旧转换说明": uses_old_transform,
            "存在版本差异": stale,
            "差异说明": difference,
            "可执行": executable,
            "被占用目标": blocked_by,
            "版本列表": versions,
        }

    # ------------------------------------------------------------------
    # 字段级血缘视图
    # ------------------------------------------------------------------
    def _path_id(self, group_key: tuple[str, str]) -> str:
        digest = hashlib.sha1(f"{group_key[0]}|{group_key[1]}".encode("utf-8")).hexdigest()
        return f"FP-{digest[:8].upper()}"

    def _build_groups(
        self,
        relations: list[dict[str, Any]],
        precedence: str,
    ) -> tuple[
        dict[tuple[str, str], list[dict[str, Any]]],
        dict[tuple[str, str], dict[str, Any] | None],
    ]:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in relations:
            groups[row["_group_key"]].append(row)
        ordered_groups = {key: sorted(groups[key], key=lambda row: row["_id"]) for key in sorted(groups)}
        current = {key: self._select_current(group, precedence) for key, group in ordered_groups.items()}
        return ordered_groups, current

    def _build_paths(
        self,
        groups: dict[tuple[str, str], list[dict[str, Any]]],
        current: dict[tuple[str, str], dict[str, Any] | None],
        precedence: str,
        snapshot: str,
    ) -> list[dict[str, Any]]:
        producer: dict[tuple[str, str], tuple[str, str]] = {}
        for key, row in current.items():
            if row is not None:
                producer[(row["下游节点"], row["目标字段"])] = key

        roots = [key for key in groups if key not in producer]
        # 仅由归档关系组成的组不会形成当前路径，但历史差异仍保留在 groups 中。
        roots = [key for key in roots if current.get(key) is not None]
        roots.sort(key=lambda key: (key[0], key[1]))

        executable_targets: dict[tuple[str, str], str] = {}
        path_items: list[dict[str, Any]] = []
        consumed: set[tuple[str, str]] = set()

        def build_chain(root_key: tuple[str, str]) -> list[tuple[tuple[str, str], dict[str, Any]]]:
            chain: list[tuple[tuple[str, str], dict[str, Any]]] = []
            key: tuple[str, str] | None = root_key
            local_seen: set[tuple[str, str]] = set()
            while key is not None and key in groups and key not in local_seen:
                row = current.get(key)
                if row is None:
                    break
                if key != root_key and key in consumed:
                    break
                local_seen.add(key)
                chain.append((key, row))
                key = (row["下游节点"], row["目标字段"])
            return chain

        def emit(root_key: tuple[str, str]) -> None:
            chain = build_chain(root_key)
            if not chain:
                return
            steps: list[dict[str, Any]] = []
            path_conflicts: list[dict[str, Any]] = []
            for index, (group_key, row) in enumerate(chain):
                target_key = (row["下游节点"], row["目标字段"])
                owner = executable_targets.get(target_key)
                executable = owner is None
                if executable:
                    executable_targets[target_key] = row["关系编号"]
                blocked_by = "" if executable else owner or ""
                step = self._step_view(row, groups[group_key], executable, blocked_by)
                step["步骤序号"] = index + 1
                steps.append(step)
                conflict = self._group_conflict(groups[group_key], row)
                if conflict:
                    path_conflicts.append(conflict)
                consumed.add(group_key)

            root_step = steps[0]
            last_step = steps[-1]
            stale = any(step["存在版本差异"] for step in steps)
            old_transform = any(step["沿用旧转换说明"] for step in steps)
            path_id = self._path_id(root_key)
            review = self._review_for(snapshot, path_id)
            path_items.append({
                "path_id": path_id,
                "路径编号": path_id,
                "字段路径": " → ".join(
                    [f"{root_step['上游节点']}.{root_step['来源字段']}"]
                    + [f"{item['下游节点']}.{item['目标字段']}" for item in steps]
                ),
                "来源字段": root_step["来源字段"],
                "目标字段": last_step["目标字段"],
                "上游节点": root_step["上游节点"],
                "下游节点": last_step["下游节点"],
                "转换说明": root_step["转换说明"],
                "采集批次": root_step["采集批次"],
                "批次版本": root_step["批次版本"],
                "关系版本": root_step["关系版本"],
                "关系状态": root_step["关系状态"],
                "关系编号": root_step["关系编号"],
                "快照": snapshot,
                "快照版本": snapshot,
                "步骤": steps,
                "步骤数": len(steps),
                "节点顺序": [root_step["上游节点"]] + [step["下游节点"] for step in steps],
                "版本列表": self._path_versions(steps),
                "冲突": path_conflicts,
                "冲突数": len(path_conflicts),
                "存在版本差异": stale,
                "沿用旧转换说明": old_transform,
                "可执行": all(step["可执行"] for step in steps),
                "复核状态": review.get("状态", "未入台账") if review else "未入台账",
                "复核结果": review.get("核对结果", "") if review else "",
                "复核快照": review.get("快照", "") if review else "",
                "precedence": precedence,
                "precedence_label": PRECEDENCE_LABELS[precedence],
            })

        for root in roots:
            emit(root)

        # 环或孤立组：兜底为独立字段路径，保证当前视图不丢关系。
        leftovers = sorted(
            [key for key in groups if key not in consumed and current.get(key) is not None],
            key=lambda key: (key[0], key[1]),
        )
        for key in leftovers:
            if key not in consumed:
                emit(key)
        return path_items

    def _path_versions(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        versions: dict[str, dict[str, Any]] = {}
        for step in steps:
            for item in step["版本列表"]:
                version = item["关系版本"]
                bucket = versions.setdefault(version, {
                    "关系版本": version,
                    "采集批次": set(),
                    "关系状态": set(),
                    "是否当前": False,
                    "步骤": [],
                })
                bucket["采集批次"].add(item["采集批次"])
                bucket["关系状态"].add(item["关系状态"])
                bucket["是否当前"] = bucket["是否当前"] or item["是否当前"]
                bucket["步骤"].append({
                    "上游节点": item["上游节点"],
                    "来源字段": item["来源字段"],
                    "目标字段": item["目标字段"],
                    "转换说明": item["转换说明"],
                    "是否当前": item["是否当前"],
                })
        output: list[dict[str, Any]] = []
        for version in sorted(versions, key=_version_number, reverse=True):
            item = versions[version]
            output.append({
                **item,
                "采集批次": sorted(item["采集批次"]),
                "关系状态": sorted(item["关系状态"]),
            })
        return output

    def view(
        self,
        precedence: str = PRECEDENCE_VERIFIED,
        batch: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        precedence = normalize_precedence(precedence)
        relations = self._load_relations(batch=batch, version=version)
        # 快照只由本次视图的输入（关系记录 + 批次版本 + 过滤条件）决定，复核结果不参与。
        snapshot = self._snapshot(relations, batch=batch, version=version, precedence=precedence)
        groups, current = self._build_groups(relations, precedence)
        paths = self._build_paths(groups, current, precedence, snapshot)

        live_relations = [row for row in relations if row["关系状态"] != STATUS_ARCHIVED]
        conflict_groups = [
            conflict
            for key, group in groups.items()
            if (conflict := self._group_conflict(group, current.get(key))) is not None
        ]
        stale_paths = [path for path in paths if path["存在版本差异"]]
        old_transform_paths = [path for path in paths if path["沿用旧转换说明"]]
        review_counts = self._ledger_counts(snapshot)

        metrics = [
            {"key": "paths", "label": "字段路径总数", "value": len(paths)},
            {"key": "relations", "label": "当前关系数", "value": sum(1 for row in current.values() if row)},
            {"key": "conflicts", "label": "冲突字段路径", "value": len(conflict_groups)},
            {"key": "stale", "label": "沿用旧转换说明", "value": len(old_transform_paths)},
            {"key": "pending", "label": "待复核台账", "value": review_counts["待复核"]},
            {"key": "batches", "label": "采集批次", "value": len({row["采集批次"] for row in live_relations if row["采集批次"]})},
        ]
        summary = {item["label"]: item["value"] for item in metrics}
        return {
            "snapshot": snapshot,
            "快照版本": snapshot,
            "precedence": precedence,
            "precedence_label": PRECEDENCE_LABELS[precedence],
            "precedence_options": [
                {"value": PRECEDENCE_VERIFIED, "label": "验证优先"},
                {"value": PRECEDENCE_LATEST, "label": "最新优先"},
            ],
            "generated_from": ["血缘关系记录", "采集批次版本"],
            "batch": batch or "",
            "version": version or "",
            "batch_filter": batch or "",
            "batches": self.batches(),
            "metrics": metrics,
            "summary": summary,
            "paths": paths,
            "conflicts": conflict_groups,
            "stale_paths": [
                {"path_id": path["path_id"], "字段路径": path["字段路径"], "沿用旧转换说明": path["沿用旧转换说明"]}
                for path in stale_paths
            ],
            "field_targets": [
                {
                    "下游节点": key[0],
                    "目标字段": key[1],
                    "可执行关系": owner,
                    "可执行目标数": 1,
                }
                for key, owner in sorted(executable_targets_view(paths).items())
            ],
            "review": review_counts,
            "relation_count": len(relations),
            "archived_relation_count": len(relations) - len(live_relations),
        }

    # ------------------------------------------------------------------
    # 单条字段路径 / 版本切换 / 差异
    # ------------------------------------------------------------------
    def _find_path(self, path_id: str, precedence: str = PRECEDENCE_VERIFIED) -> tuple[dict[str, Any], dict, dict] | None:
        view = self.view(precedence=precedence)
        for path in view["paths"]:
            if path["path_id"] == path_id:
                relations = self._load_relations()
                groups, current = self._build_groups(relations, precedence)
                return path, groups, current
        return None

    def path_detail(
        self,
        path_id: str,
        version: str | None = None,
        precedence: str = PRECEDENCE_VERIFIED,
    ) -> dict[str, Any] | None:
        found = self._find_path(path_id, precedence)
        if found is None:
            return None
        path, groups, current = found
        selected_steps = self._steps_for_version(path, groups, current, version)
        current_steps = path["步骤"]
        diff = self._diff_steps(current_steps, selected_steps)
        result = _copy(path)
        result["选中版本"] = version or ""
        result["版本步骤"] = selected_steps
        result["版本差异"] = diff
        result["差异"] = diff
        return result

    def _steps_for_version(
        self,
        path: dict[str, Any],
        groups: dict[tuple[str, str], list[dict[str, Any]]],
        current: dict[tuple[str, str], dict[str, Any] | None],
        version: str | None,
    ) -> list[dict[str, Any]]:
        if not version:
            return _copy(path["步骤"])
        steps: list[dict[str, Any]] = []
        for step in path["步骤"]:
            key = (step["上游节点"], step["来源字段"])
            group = groups.get(key, [])
            chosen = next((row for row in group if row["关系版本"] == version), None)
            if chosen is None:
                steps.append({**_copy(step), "版本可用": False})
                continue
            item = self._step_view(chosen, group, True)
            item["步骤序号"] = step["步骤序号"]
            item["版本可用"] = True
            steps.append(item)
        return steps

    def _diff_steps(self, current_steps: list[dict[str, Any]], selected_steps: list[dict[str, Any]]) -> dict[str, Any]:
        fields = [
            ("上游节点", "上游节点"),
            ("下游节点", "下游节点"),
            ("来源字段", "来源字段"),
            ("目标字段", "目标字段"),
            ("转换说明", "转换说明"),
            ("采集批次", "采集批次"),
            ("关系版本", "关系版本"),
            ("关系状态", "关系状态"),
        ]
        changes: list[dict[str, Any]] = []
        for index, current_step in enumerate(current_steps):
            selected = selected_steps[index] if index < len(selected_steps) else {}
            if selected.get("版本可用") is False:
                # 该关系版本在此步骤没有记录：不伪造字段差异，只提示版本缺失。
                changes.append({
                    "步骤": index + 1,
                    "字段路径": current_step.get("字段路径", ""),
                    "项目": "关系版本",
                    "当前版本": current_step.get("关系版本", ""),
                    "对比版本": "该版本无此步骤",
                })
                continue
            step_changes: list[dict[str, Any]] = []
            for key, label in fields:
                left = current_step.get(key, "")
                right = selected.get(key, "")
                if _text(left) != _text(right):
                    step_changes.append({
                        "项目": label,
                        "当前版本": left,
                        "对比版本": right,
                    })
            if step_changes:
                changes.extend([
                    {
                        "步骤": index + 1,
                        "字段路径": current_step.get("字段路径", ""),
                        **change,
                    }
                    for change in step_changes
                ])
        return {
            "changed": bool(changes),
            "changes": changes,
            "summary": f"{len(changes)} 处差异" if changes else "版本一致",
        }

    def path_versions(self, path_id: str, precedence: str = PRECEDENCE_VERIFIED) -> list[dict[str, Any]] | None:
        found = self._find_path(path_id, precedence)
        if found is None:
            return None
        path, _, _ = found
        return _copy(path["版本列表"])

    def path_diff(
        self,
        path_id: str,
        left_version: str | None = None,
        right_version: str | None = None,
        precedence: str = PRECEDENCE_VERIFIED,
    ) -> dict[str, Any] | None:
        found = self._find_path(path_id, precedence)
        if found is None:
            return None
        path, groups, current = found
        left_steps = self._steps_for_version(path, groups, current, left_version)
        right_steps = self._steps_for_version(path, groups, current, right_version)
        diff = self._diff_steps(left_steps, right_steps)
        return {
            "path_id": path_id,
            "路径编号": path_id,
            "字段路径": path["字段路径"],
            "snapshot": path["快照"],
            "left_version": left_version or "当前版本",
            "right_version": right_version or "当前版本",
            "left_steps": left_steps,
            "right_steps": right_steps,
            **diff,
        }

    # ------------------------------------------------------------------
    # 流转编排待复核台账：同快照并发写只放行一次
    # ------------------------------------------------------------------
    def _review_for(self, snapshot: str, path_id: str) -> dict[str, Any] | None:
        with self._lock:
            result = self._review_results.get((snapshot, path_id))
            return _copy(result) if result else None

    def _ledger_counts(self, snapshot: str | None = None) -> dict[str, int]:
        with self._lock:
            entries = [
                entry for entry in self._ledger
                if snapshot is None or entry["快照"] == snapshot
            ]
            return {
                "台账总数": len(entries),
                "待复核": sum(1 for entry in entries if entry["复核状态"] == "待复核"),
                "已复核": sum(1 for entry in entries if entry["复核状态"] == "已复核"),
                "存在差异": sum(1 for entry in entries if entry.get("核对结果") == "存在差异"),
            }

    def ledger(
        self,
        status: str | None = None,
        snapshot: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            items = [
                _copy(entry) for entry in self._ledger
                if (not status or entry["复核状态"] == status)
                and (not snapshot or entry["快照"] == snapshot)
            ]
            counts = self._ledger_counts(snapshot)
        items.sort(key=lambda entry: entry["id"])
        return {
            "items": items,
            "total": len(items),
            "page": 1,
            "size": len(items) or 20,
            "summary": counts,
            "snapshot": snapshot or "",
        }

    def sync_ledger(
        self,
        precedence: str = PRECEDENCE_VERIFIED,
        snapshot: str | None = None,
        batch: str | None = None,
        version: str | None = None,
        source: str = "flow",
    ) -> dict[str, Any]:
        """从字段级视图落待复核台账。

        同快照只允许一次成功写入：先到请求创建台账，后到的并发请求（或重复
        同步）会收到 snapshot_conflict，由调用方转成 409。
        """
        view = self.view(precedence=precedence, batch=batch, version=version)
        current_snapshot = view["snapshot"]
        if snapshot and snapshot != current_snapshot:
            return {
                "ok": False,
                "conflict": True,
                "code": "snapshot_conflict",
                "message": "快照已失效：字段级血缘视图已经更新，请重新读取后复核",
                "snapshot": current_snapshot,
                "expected_snapshot": snapshot,
            }
        with self._lock:
            if current_snapshot in self._synced_snapshots:
                return {
                    "ok": False,
                    "conflict": True,
                    "code": "snapshot_conflict",
                    "message": "同快照已同步：并发写入仅一次成功，其余请求已拒绝",
                    "snapshot": current_snapshot,
                    "synced_at": self._synced_snapshots[current_snapshot],
                }
            self._synced_snapshots[current_snapshot] = "已同步"
            created: list[dict[str, Any]] = []
            for path in view["paths"]:
                self._ledger_seq += 1
                conflict_note = "；".join(
                    f"{item['冲突类型']}：{item['来源字段']}" for item in path["冲突"]
                )
                if not conflict_note:
                    conflict_note = "无字段级冲突"
                entry = {
                    "id": self._ledger_seq,
                    "快照": current_snapshot,
                    "snapshot": current_snapshot,
                    "来源服务": source,
                    "precedence": view["precedence"],
                    "precedence_label": view["precedence_label"],
                    "path_id": path["path_id"],
                    "路径编号": path["path_id"],
                    "字段路径": path["字段路径"],
                    "来源字段": path["来源字段"],
                    "目标字段": path["目标字段"],
                    "关系版本": path["关系版本"],
                    "采集批次": path["采集批次"],
                    "关系状态": path["关系状态"],
                    "当前关系编号": path["关系编号"],
                    "差异摘要": conflict_note,
                    "沿用旧转换说明": path["沿用旧转换说明"],
                    "复核状态": "待复核",
                    "核对结果": "",
                    "复核人": "",
                    "复核时间": "",
                    "复核快照": "",
                    "历史": [],
                }
                self._ledger.append(entry)
                created.append(_copy(entry))
        return {
            "ok": True,
            "message": f"字段级血缘视图已落入待复核台账，共 {len(created)} 条",
            "snapshot": current_snapshot,
            "created": len(created),
            "items": created,
        }

    def review_ledger_entry(
        self,
        entry_id: int,
        result: str | None = None,
        snapshot: str | None = None,
        action: str | None = None,
        reviewer: str = "值班管理员",
        remark: str = "",
    ) -> dict[str, Any]:
        """写回核对结果；同台账项同快照并发写只允许一次成功。"""
        normalized_result = normalize_result(result, action)
        with self._lock:
            entry = next((item for item in self._ledger if item["id"] == entry_id), None)
            if entry is None:
                return {"ok": False, "not_found": True, "message": f"待复核台账 {entry_id} 不存在"}
            if snapshot and snapshot != entry["快照"]:
                return {
                    "ok": False,
                    "conflict": True,
                    "code": "snapshot_conflict",
                    "message": "快照不一致：台账项已被新的字段级血缘视图替换，旧结果保留",
                    "snapshot": entry["快照"],
                    "expected_snapshot": snapshot,
                }
            if entry["复核状态"] != "待复核":
                return {
                    "ok": False,
                    "conflict": True,
                    "code": "snapshot_conflict",
                    "message": "同快照并发写仅一次成功：该台账项已复核，其余写入已拒绝",
                    "snapshot": entry["快照"],
                    "entry": _copy(entry),
                }
            self._review_seq += 1
            entry["历史"].append({
                "序号": self._review_seq,
                "前状态": entry["复核状态"],
                "前核对结果": entry["核对结果"],
                "快照": entry["快照"],
                "时间": "2026-11-18",
                "说明": "旧结果保留",
            })
            entry["复核状态"] = "已复核"
            entry["核对结果"] = normalized_result
            entry["复核人"] = reviewer
            entry["复核时间"] = "2026-11-18"
            entry["复核快照"] = entry["快照"]
            entry["备注"] = remark
            review = {
                "状态": "已复核",
                "核对结果": normalized_result,
                "快照": entry["快照"],
                "复核人": reviewer,
                "复核时间": entry["复核时间"],
                "备注": remark,
                "path_id": entry["path_id"],
                "字段路径": entry["字段路径"],
            }
            self._review_results[(entry["快照"], entry["path_id"])] = review
            return {
                "ok": True,
                "message": "核对结果已写入字段级血缘视图",
                "snapshot": entry["快照"],
                "entry": _copy(entry),
                "review": _copy(review),
            }

    # ------------------------------------------------------------------
    # 编排顺序：流转编排服务读取同一份字段级视图
    # ------------------------------------------------------------------
    def node_order(self, precedence: str = PRECEDENCE_VERIFIED, snapshot: str | None = None) -> dict[str, Any]:
        view = self.view(precedence=precedence)
        current_snapshot = view["snapshot"]
        edges: list[dict[str, Any]] = []
        node_fields: dict[str, set[str]] = defaultdict(set)
        first_seen: dict[str, int] = {}
        for path in view["paths"]:
            for step in path["步骤"]:
                source_node = step["上游节点"]
                target_node = step["下游节点"]
                node_fields[source_node].update([f"出:{step['来源字段']}"])
                node_fields[target_node].update([f"入:{step['目标字段']}"])
                for node in (source_node, target_node):
                    if node not in first_seen:
                        first_seen[node] = len(first_seen)
                if not any(
                    edge["from"] == source_node and edge["to"] == target_node
                    for edge in edges
                ):
                    edges.append({
                        "from": source_node,
                        "to": target_node,
                        "from_node": source_node,
                        "to_node": target_node,
                        "来源字段": step["来源字段"],
                        "目标字段": step["目标字段"],
                        "关系版本": step["关系版本"],
                        "采集批次": step["采集批次"],
                        "path_id": path["path_id"],
                        "label": f"{step['来源字段']}→{step['目标字段']}",
                    })

        indegree = {node: 0 for node in first_seen}
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            adjacency[edge["from"]].append(edge["to"])
            indegree[edge["to"]] += 1
        queue = sorted([node for node, degree in indegree.items() if degree == 0], key=lambda node: first_seen[node])
        order: list[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for target in sorted(adjacency.get(node, []), key=lambda item: first_seen[item]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        # 有环时按首次出现顺序补齐，保证编排页仍能给出可执行的节点顺序。
        for node in sorted(first_seen, key=lambda item: first_seen[item]):
            if node not in order:
                order.append(node)

        position = {node: index + 1 for index, node in enumerate(order)}
        ordered_nodes = [
            {
                "position": position[node],
                "node": node,
                "节点": node,
                "字段": sorted(node_fields.get(node, set())),
                "下一节点": sorted(set(adjacency.get(node, []))),
            }
            for node in order
        ]
        return {
            "snapshot": current_snapshot,
            "快照版本": current_snapshot,
            "generated_from": "字段级血缘视图",
            "precedence": view["precedence"],
            "precedence_label": view["precedence_label"],
            "order": ordered_nodes,
            "node_order": [item["node"] for item in ordered_nodes],
            "edges": edges,
            "paths": [
                {
                    "path_id": path["path_id"],
                    "字段路径": path["字段路径"],
                    "节点顺序": path["节点顺序"],
                }
                for path in view["paths"]
            ],
            "conflict_count": len(view["conflicts"]),
        }


def executable_targets_view(paths: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """从当前路径收集可执行目标；每条字段在当前视图中只留一个目标。"""
    targets: dict[tuple[str, str], str] = {}
    for path in paths:
        for step in path["步骤"]:
            if step["可执行"]:
                targets.setdefault((step["下游节点"], step["目标字段"]), step["关系编号"])
    return targets


field_lineage_service = FieldLineageService()
