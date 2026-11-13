"""采集配置版本接口：维护配置版本，覆盖发布版本、回滚版本、废弃版本等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.version import ACTION_HINTS, STATUS_ORDER, VersionService, bump_version_number

router = APIRouter(prefix="/api/version", tags=["采集配置版本"])

service = VersionService()

LIST_FIELDS = ["版本编号", "配置名称", "版本号", "变更说明", "变更内容", "回滚标识", "发布状态", "发布时间"]
STATUSES = STATUS_ORDER


def _action_name(payload: EntryPayload) -> str:
    return str(payload.action or payload.values.get("action") or "").strip()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按版本编号、配置名称或版本号检索"),
    status: str | None = Query(default=None, description="草稿、已发布、已回滚、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按版本编号与状态过滤采集配置版本列表；没有数据时返回空页，不报错。"""
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
    """导出采集配置版本清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "version", "total": total, "items": items}


@router.get("/summary")
def summary() -> dict[str, Any]:
    """按状态汇总配置版本数量，保证统计卡片和列表使用同一份状态口径。"""
    items, total = service.list_entries(page=1, size=10000)
    counts = {status: 0 for status in STATUSES}
    for item in items:
        status = str(item.get("status"))
        counts[status] = counts.get(status, 0) + 1
    return {"total": total, "counts": counts}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条配置版本明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"配置版本 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条配置版本，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if entry is None:
        if isinstance(missing, list):
            return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
        return ActionResult(ok=False, message=str(missing))
    return ActionResult(ok=True, message="配置版本已登记，当前状态：草稿", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条配置版本执行发布版本、回滚版本、废弃版本；不允许的动作会被拦下并说明原因。"""
    action = _action_name(payload)
    if not action:
        return ActionResult(ok=False, message="缺少动作名称，可选：发布版本、回滚版本、废弃版本")
    entry, message, _audit_event = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/rebuild", response_model=ActionResult)
def rebuild_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """从已废弃版本重建一个新版本（新记录、新版本号，从草稿开始）。

    选择"重建"而不是"原地恢复"：废弃记录保持可追溯，恢复动作只产生新版本。
    """
    source = service.get_entry(entry_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"配置版本 {entry_id} 不存在或已归档")
    if source.get("status") != "已废弃":
        return ActionResult(ok=False, message="只有已废弃版本需要重建；其他状态请直接执行对应动作")
    rows = service.list_entries(page=1, size=10000)[0]
    version_no = bump_version_number(str(source.get("版本号") or ""))
    existing_numbers = {str(row.get("版本号")) for row in rows}
    if payload.values.get("版本号"):
        version_no = str(payload.values.get("版本号"))
    while version_no in existing_numbers:
        version_no = bump_version_number(version_no)
    new_code = f"{source.get('版本编号')}-R"
    suffix = 1
    existing_codes = {str(row.get("版本编号")) for row in rows}
    candidate = new_code
    while candidate in existing_codes:
        suffix += 1
        candidate = f"{new_code}{suffix}"

    values = {
        "版本编号": str(payload.values.get("版本编号") or candidate),
        "配置名称": source.get("配置名称"),
        "版本号": str(payload.values.get("版本号") or version_no),
        "变更说明": payload.values.get("变更说明")
        or f"基于已废弃版本 {source.get('版本编号')}（版本号 {source.get('版本号')}）重建",
        "变更内容": payload.values.get("变更内容") or source.get("变更内容"),
    }
    entry, missing = service.create_entry(values)
    if entry is None:
        if isinstance(missing, list):
            return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
        return ActionResult(ok=False, message=str(missing))
    return ActionResult(
        ok=True,
        message=(
            f"已基于废弃版本 {source.get('版本编号')} 重建新版本 {entry.get('版本编号')}"
            f"（版本号 {entry.get('版本号')}），新版本从草稿开始"
        ),
        entry=entry,
    )


@router.get("/{entry_id}/action-hints", response_model=dict)
def action_hints(entry_id: int) -> dict:
    """给出该版本当前可执行动作的判定口径，供详情页展示被拦截的原因。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"配置版本 {entry_id} 不存在或已归档")
    checks = {
        "发布版本": entry.get("status") == "草稿",
        "回滚版本": entry.get("status") == "已发布",
        "废弃版本": entry.get("status") == "已回滚",
        "重建新版本": entry.get("status") == "已废弃",
    }
    current = str(entry.get("status"))
    return {
        "status": current,
        "version_no": entry.get("版本号"),
        "allowed": checks,
        "hints": ACTION_HINTS,
        "reason": {
            name: "" if allowed else service.reject_message(name, current)
            for name, allowed in checks.items()
        },
    }
