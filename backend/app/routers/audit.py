"""采集审计日志接口：维护审计记录，覆盖标记复核、归档记录等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.audit import ACTION_RULES, STATUS_ORDER, AuditService

router = APIRouter(prefix="/api/audit", tags=["采集审计日志"])

service = AuditService()

LIST_FIELDS = [
    "记录编号",
    "操作类型",
    "采集计划",
    "执行人",
    "执行时间",
    "结果摘要",
    "变更内容",
    "复核状态",
]
STATUSES = STATUS_ORDER


def _action_name(payload: EntryPayload) -> str:
    return str(payload.action or payload.values.get("action") or "").strip()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号、采集计划或版本编号检索"),
    status: str | None = Query(default=None, description="已记录、待核对、已复核、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤采集审计日志列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"状态「{status}」不在允许范围内，可选：{'、'.join(STATUSES)}",
        )
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集审计日志清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "audit", "total": total, "items": items}


@router.get("/summary")
def summary() -> dict[str, Any]:
    """按状态汇总审计记录数量，保证统计卡片和列表使用同一份状态口径。"""
    items, total = service.list_entries(page=1, size=10000)
    counts = {status: 0 for status in STATUSES}
    for item in items:
        status = str(item.get("status"))
        counts[status] = counts.get(status, 0) + 1
    return {"total": total, "counts": counts}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条审计记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"审计记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条审计记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if entry is None:
        if isinstance(missing, list):
            return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
        return ActionResult(ok=False, message=str(missing))
    return ActionResult(ok=True, message="审计记录已登记，当前状态：已记录", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条审计记录执行标记复核、归档记录；不允许的动作会被拦下并说明原因。"""
    action = _action_name(payload)
    if not action:
        return ActionResult(ok=False, message="缺少动作名称，可选：标记复核、归档记录")
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/action-hints", response_model=dict)
def action_hints(entry_id: int) -> dict:
    """给出该审计记录当前可执行动作的判定口径，供详情页展示被拦截的原因。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"审计记录 {entry_id} 不存在或已归档")
    status = str(entry.get("status"))
    allowed = {action: status in rule[0] for action, rule in ACTION_RULES.items()}
    return {
        "status": status,
        "version_no": entry.get("版本号"),
        "allowed": allowed,
        "reason": {name: "" if ok else service.reject_message(name, status) for name, ok in allowed.items()},
    }
