"""字段级血缘视图后端单元测试（标准库 unittest，无额外依赖）。

运行：cd backend && .venv/bin/python -m unittest tests.test_field_lineage -v
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.field_lineage import (  # noqa: E402
    PRECEDENCE_LATEST,
    PRECEDENCE_VERIFIED,
    FieldLineageService,
)
from app.store import store  # noqa: E402


def fresh_service() -> FieldLineageService:
    return FieldLineageService()


class FieldLineageViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = fresh_service()

    def test_view_binds_version_and_batch(self) -> None:
        view = self.service.view()
        self.assertTrue(view["snapshot"].startswith("SNAP-"))
        self.assertEqual(view["precedence_label"], "验证优先")
        self.assertGreaterEqual(len(view["paths"]), 5)
        for path in view["paths"]:
            self.assertTrue(path["关系版本"])
            self.assertTrue(path["采集批次"])
            self.assertEqual(path["快照"], view["snapshot"])
            for step in path["步骤"]:
                self.assertTrue(step["关系版本"])
                self.assertTrue(step["采集批次"])
                self.assertTrue(step["版本列表"])

    def test_each_target_has_single_executable_relation(self) -> None:
        for precedence in (PRECEDENCE_VERIFIED, PRECEDENCE_LATEST):
            view = self.service.view(precedence=precedence)
            seen: dict[tuple[str, str], str] = {}
            for path in view["paths"]:
                for step in path["步骤"]:
                    if not step["可执行"]:
                        self.assertTrue(step["被占用目标"])
                        continue
                    key = (step["下游节点"], step["目标字段"])
                    self.assertNotIn(key, seen, f"{precedence} 下目标 {key} 出现两个可执行关系")
                    seen[key] = step["关系编号"]
            for target in view["field_targets"]:
                self.assertEqual(target["可执行目标数"], 1)

    def test_verified_precedence_keeps_verified_relation(self) -> None:
        view = self.service.view(precedence=PRECEDENCE_VERIFIED)
        path = next(p for p in view["paths"] if p["来源字段"] == "订单号" and p["上游节点"] == "订单采集通道")
        self.assertEqual(path["关系版本"], "v1")
        self.assertEqual(path["关系状态"], "已验证")
        self.assertTrue(path["沿用旧转换说明"])
        self.assertTrue(path["存在版本差异"])

    def test_latest_precedence_uses_new_record(self) -> None:
        view = self.service.view(precedence=PRECEDENCE_LATEST)
        path = next(p for p in view["paths"] if p["来源字段"] == "订单号" and p["上游节点"] == "订单采集通道")
        self.assertEqual(path["关系版本"], "v2")
        self.assertEqual(path["关系状态"], "已记录")
        self.assertFalse(path["沿用旧转换说明"])

    def test_same_batch_status_conflict_reported(self) -> None:
        view = self.service.view()
        types = {conflict["冲突类型"] for conflict in view["conflicts"]}
        self.assertIn("同批次验证状态冲突", types)
        self.assertIn("跨批次状态冲突", types)

    def test_archived_relation_not_current(self) -> None:
        view = self.service.view()
        relations = []
        for path in view["paths"]:
            relations.extend(step["关系编号"] for step in path["步骤"])
        self.assertNotIn("LINE-1011", relations)
        versions = []
        for path in view["paths"]:
            versions.extend(item["关系版本"] for item in path["版本列表"])
        self.assertIn("v2", versions)

    def test_path_diff_reports_version_and_status(self) -> None:
        view = self.service.view()
        path = next(p for p in view["paths"] if p["来源字段"] == "订单号" and p["上游节点"] == "订单采集通道")
        diff = self.service.path_diff(path["path_id"], left_version="v1", right_version="v2")
        self.assertTrue(diff["changed"])
        fields = {change["项目"] for change in diff["changes"]}
        self.assertIn("关系版本", fields)
        self.assertIn("关系状态", fields)

    def test_path_detail_selected_version_keeps_current(self) -> None:
        view = self.service.view()
        path = next(p for p in view["paths"] if p["来源字段"] == "订单号" and p["上游节点"] == "订单采集通道")
        detail = self.service.path_detail(path["path_id"], version="v2")
        self.assertEqual(detail["选中版本"], "v2")
        self.assertEqual(detail["步骤"][0]["关系版本"], "v1")
        self.assertTrue(detail["版本差异"]["changed"])

    def test_batch_filter_restricts_view(self) -> None:
        view = self.service.view(batch="BATCH-2026-09C")
        self.assertEqual(view["batch"], "BATCH-2026-09C")
        for path in view["paths"]:
            for step in path["步骤"]:
                self.assertEqual(step["采集批次"], "BATCH-2026-09C")


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = fresh_service()

    def test_sync_then_same_snapshot_rejected(self) -> None:
        view = self.service.view()
        first = self.service.sync_ledger(snapshot=view["snapshot"])
        self.assertTrue(first["ok"])
        self.assertEqual(first["created"], len(view["paths"]))
        second = self.service.sync_ledger(snapshot=view["snapshot"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["code"], "snapshot_conflict")

    def test_stale_snapshot_rejected(self) -> None:
        result = self.service.sync_ledger(snapshot="SNAP-OLD")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "snapshot_conflict")

    def test_concurrent_review_only_one_succeeds(self) -> None:
        view = self.service.view()
        self.service.sync_ledger(snapshot=view["snapshot"])
        entry = self.service.ledger()["items"][0]
        results: list[dict] = []
        lock = threading.Lock()

        def worker(value: str) -> None:
            outcome = self.service.review_ledger_entry(
                entry["id"], result=value, snapshot=view["snapshot"]
            )
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker, args=(value,)) for value in ("一致", "存在差异", "一致")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(1 for item in results if item["ok"]), 1)
        self.assertEqual(sum(1 for item in results if item.get("code") == "snapshot_conflict"), 2)
        # 后面的写入不能覆盖第一次的核对结果
        stored = self.service.ledger()["items"][0]
        successes = [item for item in results if item["ok"]]
        self.assertEqual(stored["核对结果"], successes[0]["entry"]["核对结果"])
        self.assertEqual(len(stored["历史"]), 1)

    def test_review_writes_back_to_view(self) -> None:
        view = self.service.view()
        self.service.sync_ledger(snapshot=view["snapshot"])
        entry = self.service.ledger()["items"][0]
        result = self.service.review_ledger_entry(
            entry["id"], result="存在差异", snapshot=view["snapshot"]
        )
        self.assertTrue(result["ok"])
        refreshed = self.service.view()
        matched = next(p for p in refreshed["paths"] if p["path_id"] == entry["path_id"])
        self.assertEqual(matched["复核状态"], "已复核")
        self.assertEqual(matched["复核结果"], "存在差异")
        # 复核写回不改变快照
        self.assertEqual(refreshed["snapshot"], view["snapshot"])

    def test_node_order_follows_field_paths(self) -> None:
        order = self.service.node_order()
        self.assertEqual(order["generated_from"], "字段级血缘视图")
        self.assertEqual(order["snapshot"], self.service.view()["snapshot"])
        nodes = order["node_order"]
        self.assertLess(nodes.index("订单采集通道"), nodes.index("订单解析器"))
        self.assertLess(nodes.index("订单解析器"), nodes.index("订单入库"))

    def test_flow_service_reads_same_snapshot(self) -> None:
        from app.services.flow import FlowService

        flow = FlowService()
        self.assertEqual(flow.field_view()["snapshot"], self.service.view()["snapshot"])
        self.assertEqual(flow.node_order()["snapshot"], self.service.view()["snapshot"])


if __name__ == "__main__":
    unittest.main()
