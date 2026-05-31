"""claude_status_daemon.py — 轻量守护进程：监控 Claude Code 会话 → 写状态文件。

独立于 PyQt overlay 运行，确保状态始终同步。
用法: python claude_status_daemon.py [project_name]
"""
import sys
import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone

# ── 配置 ──
PROJECT = sys.argv[1] if len(sys.argv) > 1 else "TianqinWu"
STATE_DIR = os.path.expanduser("~/.agent-status")
CLAUDE_PROJECTS = os.path.expanduser(f"~/.claude/projects/C--Users-{PROJECT}")
REASONIX_SESSIONS = os.path.expanduser("~/.reasonix/sessions")
POLL_INTERVAL = 0.5          # 扫描间隔
IDLE_TIMEOUT = 2.0           # 无事件后写 idle
FAST_IDLE_TIMEOUT = 1.5      # model.final 后更快回落
STATUS_TTL = 30.0            # 忽略超过 N 秒的旧事件

TOOL_STATUS = {
    "read": "reading", "read_file": "reading", "search": "reading",
    "grep": "reading", "glob": "reading", "list": "reading",
    "write": "coding", "edit": "coding", "edit_file": "coding",
    "write_file": "coding",
    "bash": "executing", "shell": "executing",
    "task": "executing", "agent": "executing",
}


def write_status(status: str, message: str):
    """原子写入状态文件"""
    payload = {
        "status": status,
        "message": message,
        "timestamp": datetime.now().isoformat(),
    }
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = os.path.join(STATE_DIR, f".{PROJECT}.tmp")
    dst = os.path.join(STATE_DIR, f"{PROJECT}.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, dst)


def is_recent(ts_str: str) -> bool:
    """事件时间戳是否在 STATUS_TTL 秒内"""
    try:
        et = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - et).total_seconds() < STATUS_TTL
    except (ValueError, TypeError):
        return True


def tail_file(filepath: Path, pos_tracker: dict) -> list[dict]:
    """读取文件新增内容，返回事件列表"""
    key = str(filepath)
    try:
        size = filepath.stat().st_size
        if key not in pos_tracker:
            pos_tracker[key] = max(0, size - 4096)
        last_pos = pos_tracker[key]
        if size < last_pos:
            last_pos = 0
        if size <= last_pos:
            return []

        events = []
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_pos)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        pos_tracker[key] = size
        return events
    except OSError:
        return []


def infer_status(events: list[dict]) -> tuple[str, str] | None:
    """从事件列表推断当前状态，返回 (status, message) 或 None"""
    result = None
    for e in events:
        etype = e.get("type", "")

        # Reasonix 事件格式
        if etype == "model.turn.started":
            result = ("thinking", "分析用户输入中...")
        elif etype == "model.final":
            result = ("thinking", "准备下一步...")
        elif etype in ("tool.intent", "tool.dispatched", "tool.preparing"):
            name = e.get("name", "")
            s = TOOL_STATUS.get(name, "executing")
            result = (s, name or s)
        elif etype == "status":
            text = e.get("text", "")
            if "等待" in text:
                result = ("waiting", text[:60])
            elif "错误" in text or "失败" in text:
                result = ("error", text[:60])
            elif "思考" in text:
                result = ("thinking", text[:60])

        # Claude Code session 事件
        elif etype == "user":
            result = ("thinking", "理解用户意图中...")
        elif etype == "last-prompt":
            result = ("thinking", "分析中...")
        elif etype == "assistant":
            msg = e.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "tool")
                        s = TOOL_STATUS.get(name, "executing")
                        result = (s, name)
                        break
                else:
                    result = ("thinking", "生成回复中...")
            elif isinstance(content, str) and content:
                result = ("thinking", "生成回复中...")

    return result


def find_latest_session(project_dir: Path) -> Path | None:
    """找到最近的 Claude Code session JSONL 文件"""
    if not project_dir.exists():
        return None
    files = sorted(project_dir.glob("*.jsonl"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


def find_latest_reasonix() -> Path | None:
    """找到最近的 Reasonix 事件文件"""
    rd = Path(REASONIX_SESSIONS)
    if not rd.exists():
        return None
    files = sorted(rd.glob("code-*.events.jsonl"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


def main():
    print(f"[statusd] 启动: project={PROJECT}, state_dir={STATE_DIR}")
    write_status("idle", "守护进程启动")

    pos_tracker = {}
    project_dir = Path(CLAUDE_PROJECTS)
    last_event_time = time.time()
    fast_idle = False  # model.final 后快速回落

    while True:
        try:
            found_any = False

            # 1. Reasonix 事件
            reasonix_file = find_latest_reasonix()
            if reasonix_file:
                events = tail_file(reasonix_file, pos_tracker)
                recent = [e for e in events if is_recent(e.get("ts", ""))]
                if recent:
                    found_any = True
                    # model.final → 快速回落标记
                    if any(e.get("type") == "model.final" for e in recent):
                        fast_idle = True
                    status = infer_status(recent)
                    if status:
                        write_status(*status)

            # 2. Claude Code session
            session_file = find_latest_session(project_dir)
            if session_file:
                events = tail_file(session_file, pos_tracker)
                recent = [e for e in events if is_recent(e.get("ts", ""))]
                if recent:
                    found_any = True
                    status = infer_status(recent)
                    if status:
                        write_status(*status)

            # 3. Idle 检测（model.final 后更快回落）
            now = time.time()
            if found_any:
                last_event_time = now
            else:
                timeout = FAST_IDLE_TIMEOUT if fast_idle else IDLE_TIMEOUT
                if now - last_event_time > timeout:
                    write_status("idle", "空闲")
                    fast_idle = False

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("[statusd] 退出")
            break
        except Exception as e:
            print(f"[statusd] 错误: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    main()
