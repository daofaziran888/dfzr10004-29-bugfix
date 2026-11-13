"""后端业务规则回归校验：版本状态机 + 审计事件 + 并发发布。

运行：cd backend && .venv/bin/python -I scripts/check_version_audit.py
脚本只走 HTTP 接口，不直接读写 app 内部对象。
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("CHECK_BASE", "http://127.0.0.1:8013")


def call(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def action(entry_id: int, name: str) -> tuple[int, dict[str, Any]]:
    return call("POST", f"/api/version/{entry_id}/actions", {"action": name})


def audit_count(version_code: str) -> int:
    _, payload = call("GET", f"/api/audit?keyword={version_code}&size=200")
    return int(payload["total"])


def scenario_sequence() -> None:
    _, created = call(
        "POST",
        "/api/version",
        {"values": {"版本编号": "VERS-T1", "配置名称": "回归配置", "版本号": "v-t1"}},
    )
    assert created["ok"], created
    vid = created["entry"]["id"]

    assert action(vid, "回滚版本")[1]["ok"] is False, "未发布稿不能回滚"
    assert action(vid, "废弃版本")[1]["ok"] is False, "未发布稿不能废弃"
    assert action(vid, "发布版本")[1]["ok"] is True
    assert action(vid, "发布版本")[1]["ok"] is False, "重复发布必须被拦下"
    assert action(vid, "废弃版本")[1]["ok"] is False, "已发布版本不能直接废弃"
    assert action(vid, "回滚版本")[1]["ok"] is True
    assert action(vid, "回滚版本")[1]["ok"] is False, "回滚只能一次"
    assert action(vid, "发布版本")[1]["ok"] is False, "已回滚版本不能重新发布"
    assert action(vid, "废弃版本")[1]["ok"] is True
    assert action(vid, "发布版本")[1]["ok"] is False, "已废弃版本不能重新发布"
    assert action(vid, "废弃版本")[1]["ok"] is False, "不能重复废弃"

    _, detail = call("GET", f"/api/version/{vid}")
    assert detail["status"] == "已废弃", detail
    assert detail["发布状态"] == "已废弃" and detail["回滚标识"] == "是"
    assert audit_count("VERS-T1") == 3, "三次成功动作应各生成一条审计事件"

    _, audit_rows = call("GET", "/api/audit?keyword=VERS-T1&size=200")
    conclusions = {row["操作类型"]: row["发布结论"] for row in audit_rows["items"]}
    assert conclusions == {"发布版本": "已发布", "回滚版本": "已回滚", "废弃版本": "已废弃"}
    for row in audit_rows["items"]:
        assert row["版本号"] == "v-t1", row
        assert row["版本当前状态"] == "已废弃", row

    _, rebuilt = call("POST", f"/api/version/{vid}/rebuild", {"values": {}})
    assert rebuilt["ok"] and rebuilt["entry"]["status"] == "草稿", rebuilt
    assert rebuilt["entry"]["版本号"] != "v-t1", "重建必须使用新版本号"
    print("sequence ok", rebuilt["message"])


def scenario_concurrency() -> None:
    _, created = call(
        "POST",
        "/api/version",
        {"values": {"版本编号": "VERS-T2", "配置名称": "并发配置", "版本号": "v-t2"}},
    )
    vid = created["entry"]["id"]
    results: list[dict[str, Any]] = []
    lock = threading.Lock()

    def worker() -> None:
        _, payload = action(vid, "发布版本")
        with lock:
            results.append(payload)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [payload for payload in results if payload["ok"]]
    assert len(winners) == 1, f"并发发布应只有一个成功，实际 {len(winners)}"
    assert audit_count("VERS-T2") == 1, "并发发布只允许生成一条审计事件"
    _, detail = call("GET", f"/api/version/{vid}")
    assert detail["status"] == "已发布"
    print("concurrency ok", winners[0]["message"])


def scenario_audit_flow() -> None:
    _, audit_rows = call("GET", "/api/audit?keyword=VERS-T1&size=200")
    event_id = audit_rows["items"][0]["id"]
    assert call("POST", f"/api/audit/{event_id}/actions", {"action": "标记复核"})[1]["ok"] is True
    assert call("POST", f"/api/audit/{event_id}/actions", {"action": "标记复核"})[1]["ok"] is False
    assert call("POST", f"/api/audit/{event_id}/actions", {"action": "归档记录"})[1]["ok"] is True
    assert call("POST", f"/api/audit/{event_id}/actions", {"action": "归档记录"})[1]["ok"] is False
    _, detail = call("GET", f"/api/audit/{event_id}")
    assert detail["status"] == "已归档" and detail["复核状态"] == "已归档"
    assert audit_count("VERS-T1") == 3, "审计动作不能追加新事件"
    print("audit flow ok")


if __name__ == "__main__":
    scenario_sequence()
    scenario_concurrency()
    scenario_audit_flow()
    print("all checks passed")
