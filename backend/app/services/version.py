"""采集配置版本业务规则：状态流转、字段校验与筛选口径都收在这里。

状态机：

    草稿 --发布版本--> 已发布 --回滚版本--> 已回滚 --废弃版本--> 已废弃

约束：
- 未发布稿（草稿）不能回滚，只有已发布版本可以回滚，且只能回滚一次；
- 回滚完成后才允许标记废弃；
- 已回滚、已废弃的版本都不能重新发布；
- 已废弃版本不做原地恢复（避免"废弃又复活"的歧义），需要重建时新建一条版本记录。

每次成功动作都在采集审计记录清单里落一条"待核对"审计事件，事件里的版本号与版本状态
直接取自本次成功落库后的版本记录，保证审计结论与配置版本列表一致；重复动作会被状态
机拦下，不会追加第二条事件。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "version"
AUDIT_MODULE = "audit"
REQUIRED_FIELDS = ["版本编号", "配置名称", "版本号"]

DRAFT = "草稿"
PUBLISHED = "已发布"
ROLLED_BACK = "已回滚"
DISCARDED = "已废弃"
STATUS_ORDER = [DRAFT, PUBLISHED, ROLLED_BACK, DISCARDED]

# 动作 -> (允许的当前状态, 目标状态)
ACTION_RULES: dict[str, tuple[set[str], str]] = {
    "发布版本": ({DRAFT}, PUBLISHED),
    "回滚版本": ({PUBLISHED}, ROLLED_BACK),
    "废弃版本": ({ROLLED_BACK}, DISCARDED),
}
NEGATIVE_ACTIONS = ["回滚版本", "废弃版本"]

ACTION_HINTS = {
    "发布版本": "仅草稿状态可发布；已回滚或已废弃版本不能重新发布",
    "回滚版本": "仅已发布版本可回滚，且只能回滚一次；未发布稿不能回滚",
    "废弃版本": "仅已回滚版本可标记废弃",
}

# 状态 -> (发布状态, 回滚标识) 展示字段，读取时按状态派生，避免旧结论残留。
DISPLAY_RULES: dict[str, tuple[str, str]] = {
    DRAFT: ("未发布", "否"),
    PUBLISHED: ("已发布", "否"),
    ROLLED_BACK: ("已回滚", "是"),
    DISCARDED: ("已废弃", "是"),
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sync_display_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """用权威的 status 派生展示字段，防止列表里残留旧的发布状态/回滚标识。"""
    status = str(entry.get("status") or DRAFT)
    if status not in DISPLAY_RULES:
        status = DRAFT
        entry["status"] = status
    publish_label, rollback_label = DISPLAY_RULES[status]
    entry["发布状态"] = publish_label
    entry["回滚标识"] = rollback_label
    entry.setdefault("发布时间", "—")
    return entry


class VersionService:
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
                _sync_display_fields(row)
            if keyword:
                keyword = keyword.strip()
                rows = [
                    row
                    for row in rows
                    if keyword in str(row.get("版本编号", ""))
                    or keyword in str(row.get("配置名称", ""))
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
            return _sync_display_fields(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str] | str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with store.lock:
            version_code = str(values.get("版本编号")).strip()
            if any(str(row.get("版本编号", "")) == version_code for row in store.rows(MODULE)):
                return None, f"版本编号「{version_code}」已存在，请换一个版本编号后重试"
            entry: dict[str, Any] = {
                "status": DRAFT,
                "pending": True,
                "abnormal": False,
            }
            entry.update({field: str(values.get(field)).strip() for field in REQUIRED_FIELDS})
            for field in ["变更说明", "变更内容"]:
                entry[field] = values.get(field) or "—"
            entry["发布状态"] = "未发布"
            entry["回滚标识"] = "否"
            entry["发布时间"] = "—"
            entry["创建时间"] = _now()
            store.append(MODULE, entry)
            return entry, []

    def run_action(
        self, entry_id: int, action: str, operator: str = "值班管理员"
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """执行版本动作；成功时同步写入一条待核对审计事件并返回它。"""
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"配置版本 {entry_id} 不存在或已归档", None
            action = action.strip()
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于采集配置版本可执行范围", None

            from_status = str(entry.get("status") or DRAFT)
            allowed, target = ACTION_RULES[action]
            if from_status not in allowed:
                return None, self.reject_message(action, from_status), None

            now = _now()
            entry["status"] = target
            entry["pending"] = target != DISCARDED
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            entry["发布状态"], entry["回滚标识"] = DISPLAY_RULES[target]
            if action == "发布版本" or str(entry.get("发布时间") or "").strip() in {"", "—"}:
                entry["发布时间"] = now
            entry["最后操作时间"] = now
            entry["最后操作"] = action

            audit_event = self._append_audit_event(entry, action, from_status, target, operator)
            return entry, f"配置版本 {entry.get('版本编号')} {action}成功，当前状态：{target}", audit_event

    def reject_message(self, action: str, current: str) -> str:
        if action == "发布版本":
            if current == DISCARDED:
                return "已废弃版本不能重新发布；如需恢复该配置，请新建一个配置版本（新版本从草稿开始）"
            if current == ROLLED_BACK:
                return "已回滚版本不能重新发布；该版本仅可查看，如需变更请新建配置版本"
            if current == PUBLISHED:
                return "该版本已发布，不能重复发布，本次未生成审计事件"
        if action == "回滚版本":
            if current == DRAFT:
                return "未发布稿不能回滚，请先发布版本"
            if current == ROLLED_BACK:
                return "该版本已回滚，回滚动作只能执行一次，不能重复回滚"
            if current == DISCARDED:
                return "已废弃版本不能回滚"
        if action == "废弃版本":
            if current == DRAFT:
                return "未发布稿不能标记废弃，请先发布并回滚后再废弃"
            if current == PUBLISHED:
                return "已发布版本不能直接标记废弃，请先回滚版本"
            if current == DISCARDED:
                return "该版本已废弃，不能重复废弃"
        return f"当前状态「{current}」不允许执行「{action}」"

    def _append_audit_event(
        self,
        entry: dict[str, Any],
        action: str,
        from_status: str,
        to_status: str,
        operator: str,
    ) -> dict[str, Any]:
        """在同一次加锁内写审计事件，确保成功动作与审计记录一一对应。"""
        rows = store.rows(AUDIT_MODULE)
        version_id = int(entry.get("id", 0))
        # 幂等保护：同一版本、同一动作、同一落库结果只允许一条事件。
        for row in rows:
            if (
                row.get("来源模块") == "采集配置版本"
                and row.get("关联版本") == version_id
                and row.get("操作类型") == action
                and row.get("版本状态") == to_status
            ):
                return row

        seq = 1
        for row in rows:
            match = re.search(r"AUDI-(\d+)", str(row.get("记录编号", "")))
            if match:
                seq = max(seq, int(match.group(1)) + 1)
        record_no = f"AUDI-{seq:04d}"

        event: dict[str, Any] = {
            "记录编号": record_no,
            "操作类型": action,
            "采集计划": str(entry.get("配置名称") or entry.get("版本编号") or "采集配置版本"),
            "执行人": operator,
            "执行时间": _now(),
            "结果摘要": (
                f"配置版本 {entry.get('版本编号')}（版本号 {entry.get('版本号')}）"
                f"{action}成功，当前状态：{to_status}"
            ),
            "变更内容": f"{entry.get('版本编号')}：{from_status} → {to_status}",
            "复核状态": "待核对",
            "status": "待核对",
            "pending": True,
            "abnormal": False,
            "来源模块": "采集配置版本",
            "关联版本": version_id,
            "版本编号": entry.get("版本编号"),
            "版本号": entry.get("版本号"),
            "版本状态": to_status,
            "版本前状态": from_status,
            "发布结论": to_status,
        }
        store.append(AUDIT_MODULE, event)
        return event


def bump_version_number(version_no: str) -> str:
    """为"基于已废弃版本重建"生成一个新的版本号，避免沿用旧版本号。"""
    match = re.match(r"^(.*?)(\d+)$", version_no or "")
    if match:
        prefix, tail = match.groups()
        return f"{prefix}{int(tail) + 1}"
    return f"{version_no}-new" if version_no else "new"
