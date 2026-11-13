"""采集审计日志业务规则：状态流转、字段校验与筛选口径都收在这里。

审计记录清单里有两类记录：
- 配置版本动作自动生成的审计事件（来源模块=采集配置版本），初始为「待核对」，
  详情里的发布结论、版本号与配置版本列表使用同一份版本数据；
- 手工登记的审计记录，从「已记录」开始。

协同口径：版本动作先成功落库再写审计事件；若审计事件里的结论与版本列表冲突，
以成功落库的版本记录为准（读取时按版本记录重新对齐结论）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "audit"
VERSION_MODULE = "version"
REQUIRED_FIELDS = ["记录编号", "操作类型", "采集计划"]
STATUS_ORDER = ["已记录", "待核对", "已复核", "已归档"]
# 动作 -> (允许的当前状态, 目标状态)
ACTION_RULES: dict[str, tuple[set[str], str]] = {
    "标记复核": ({"已记录", "待核对"}, "已复核"),
    "归档记录": ({"已复核"}, "已归档"),
}
NEGATIVE_ACTIONS: list[str] = []


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class AuditService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with store.lock:
            rows = list(store.rows(MODULE))
            for row in rows:
                _sync_version_fields(row)
            if keyword:
                keyword = keyword.strip()
                rows = [
                    row
                    for row in rows
                    if keyword in str(row.get("记录编号", ""))
                    or keyword in str(row.get("采集计划", ""))
                    or keyword in str(row.get("版本编号", ""))
                    or keyword in str(row.get("版本号", ""))
                ]
            if status:
                rows = [row for row in rows if row.get("status") == status]
            total = len(rows)
            start = max(page - 1, 0) * size
            return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None
            _sync_version_fields(entry)
            return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str] | str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with store.lock:
            record_no = str(values.get("记录编号")).strip()
            if any(str(row.get("记录编号", "")) == record_no for row in store.rows(MODULE)):
                return None, f"记录编号「{record_no}」已存在，请换一个记录编号后重试"
            entry: dict[str, Any] = {
                "status": "已记录",
                "pending": True,
                "abnormal": False,
            }
            entry.update(
                {
                    "记录编号": record_no,
                    "操作类型": str(values.get("操作类型")).strip(),
                    "采集计划": str(values.get("采集计划")).strip(),
                }
            )
            for field in ["执行人", "执行时间", "结果摘要", "变更内容", "复核状态"]:
                entry[field] = values.get(field) or "—"
            if entry["执行时间"] == "—":
                entry["执行时间"] = _now()
            entry["复核状态"] = "未复核"
            entry["来源模块"] = "手工登记"
            store.append(MODULE, entry)
            return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"审计记录 {entry_id} 不存在或已归档"
            action = action.strip()
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于采集审计日志可执行范围"
            allowed, target = ACTION_RULES[action]
            current = str(entry.get("status") or "已记录")
            if current not in allowed:
                return None, self.reject_message(action, current)
            entry["status"] = target
            entry["复核状态"] = "已复核" if target == "已复核" else "已归档"
            entry["pending"] = target != "已归档"
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"审计记录 {entry.get('记录编号')} {action}成功，当前状态：{target}"

    def reject_message(self, action: str, current: str) -> str:
        if action == "标记复核":
            if current == "已复核":
                return "该审计记录已复核，不能重复标记复核，本次未生成新的审计事件"
            if current == "已归档":
                return "已归档记录不能标记复核"
        if action == "归档记录":
            if current == "已归档":
                return "该审计记录已归档，不能重复归档"
            if current in {"已记录", "待核对"}:
                return "记录尚未复核，不能直接归档；请先标记复核"
        return f"当前状态「{current}」不允许执行「{action}」"


def _sync_version_fields(row: dict[str, Any]) -> dict[str, Any]:
    """把审计事件和配置版本列表对齐：版本号与当前状态取成功落库后的版本记录。

    「发布结论」保留该次动作成功时的结论（发布/回滚/废弃各自一条事件，便于追溯），
    「版本当前状态」则实时跟随配置版本列表；两者冲突时以版本记录为准。
    """
    if row.get("来源模块") != "采集配置版本":
        return row
    version = None
    version_id = row.get("关联版本")
    if version_id is not None:
        version = store.find(VERSION_MODULE, int(version_id))
    if version is None:
        return row
    row["版本编号"] = version.get("版本编号")
    row["版本号"] = version.get("版本号")
    row["版本当前状态"] = version.get("status")
    if not row.get("发布结论"):
        row["发布结论"] = version.get("status")
    return row
