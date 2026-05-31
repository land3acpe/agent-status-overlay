"""Claude Code hooks 自动注入/还原 — 改编自 claude-code-traffic-light"""
import json
import os
import shutil
from pathlib import Path

# ── 配置 ──
CLAUDE_CONFIG = os.path.expanduser("~/.claude/settings.json")
STATE_DIR = os.path.expanduser("~/.agent-status")
BACKUP_PATH = os.path.join(STATE_DIR, "settings_backup.json")
HOOK_MARKER = "agent_status_overlay"


def backup_config():
    """备份 Claude Code 原始配置"""
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
    if Path(CLAUDE_CONFIG).exists():
        try:
            shutil.copy2(CLAUDE_CONFIG, BACKUP_PATH)
        except OSError:
            pass


def restore_config():
    """还原 Claude Code 配置并清理"""
    if Path(BACKUP_PATH).exists():
        try:
            shutil.copy2(BACKUP_PATH, CLAUDE_CONFIG)
            Path(BACKUP_PATH).unlink()
        except OSError:
            pass
    # 不删除状态目录（用户可能想保留状态）


def _is_our_hook(entry: dict) -> bool:
    """判断 hook 条目是否属于本程序"""
    for h in entry.get("hooks", []):
        if HOOK_MARKER in h.get("command", ""):
            return True
    return False


def _write_state_json(state: str, message: str = "") -> str:
    """生成写入状态 JSON 的 shell 命令"""
    import time
    ts = "${TIMESTAMP:-" + str(int(time.time() * 1000)) + "}"
    json_str = (
        '{"status":"' + state + '",'
        '"message":"' + message + '",'
        '"timestamp":"' + ts + '"}'
    )
    marker = f"# {HOOK_MARKER}"
    return (
        f'project=$(basename "${{CLAUDE_PROJECT_DIR:-$PWD}}") && '
        f'mkdir -p {STATE_DIR} && '
        f'echo \'{json_str}\' > {STATE_DIR}/"$project".json '
        f'{marker}'
    )


def _detect_encoding(filepath: str) -> str:
    """检测 JSON 文件编码：优先 UTF-8，失败回退 GBK"""
    for enc in ("utf-8", "gbk", "utf-8-sig", "gb2312", "latin-1"):
        try:
            with open(filepath, "r", encoding=enc) as f:
                json.load(f)
            return enc
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return "utf-8"  # 最终兜底


def configure_hooks():
    """向 ~/.claude/settings.json 注入状态上报 hooks"""
    Path(CLAUDE_CONFIG).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)

    backup_config()

    config = {}
    if Path(CLAUDE_CONFIG).exists():
        enc = _detect_encoding(CLAUDE_CONFIG)
        try:
            with open(CLAUDE_CONFIG, "r", encoding=enc) as f:
                config = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            config = {}

    hooks = config.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    # 需要权限确认的工具类型
    permission_tools = "Bash|Write|Edit|NotebookEdit|WebFetch"
    # 读取类工具
    read_tools = "Read|Search|Glob|Grep|List"

    # ── 自动启动 overlay daemon（Claude Code 启动时拉起）──
    _overlay_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _overlay_root_fwd = _overlay_root.replace("\\", "/")
    _marker_path_fwd = os.path.join(STATE_DIR, ".overlay.running").replace("\\", "/")
    _auto_start_cmd = (
        f'if [ ! -f {_marker_path_fwd} ]; then '
        f'pythonw {_overlay_root_fwd}/run.py & fi '
        f'# {HOOK_MARKER}'
    )

    # Hook 事件 → 状态映射
    desired = {
        "SessionStart": [
            {"matcher": "",
             "hooks": [{"type": "command",
                        "command": _write_state_json("idle", "")}]},
            {"matcher": "",
             "hooks": [{"type": "command",
                        "command": _auto_start_cmd}]},
        ],
        "UserPromptSubmit": [
            {"matcher": "",
             "hooks": [{"type": "command",
                        "command": _write_state_json("thinking", "分析用户需求...")}]}
        ],
        "PermissionRequest": [
            {"matcher": "",
             "hooks": [{"type": "command",
                        "command": _write_state_json("waiting", "等待用户确认...")}]}
        ],
        "PreToolUse": [
            {"matcher": permission_tools,
             "hooks": [{"type": "command",
                        "command": _write_state_json("executing", "执行工具调用...")}]},
            {"matcher": read_tools,
             "hooks": [{"type": "command",
                        "command": _write_state_json("reading", "读取文件/搜索代码...")}]},
        ],
        "PostToolUse": [
            {"matcher": "",
             "hooks": [{"type": "command",
                        "command": _write_state_json("thinking", "")}]}
        ],
        "Stop": [
            {"matcher": "",
             "hooks": [{"type": "command",
                        "command": _write_state_json("idle", "会话结束")}]}
        ],
        "SessionEnd": [
            {"matcher": "",
             "hooks": [{"type": "command",
                        "command": _write_state_json("idle", "会话结束")}]}
        ],
    }

    for hook_name, new_entries in desired.items():
        existing = hooks.get(hook_name, [])
        if not isinstance(existing, list):
            existing = []
        cleaned = [e for e in existing if not _is_our_hook(e)]
        cleaned.extend(new_entries)
        hooks[hook_name] = cleaned

    config["hooks"] = hooks
    with open(CLAUDE_CONFIG, "w", encoding=enc) as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    return True


def write_status_manually(status: str, message: str = "",
                          project: str = "default"):
    """手动写入状态（供其他 Agent 或脚本调用）"""
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
    import json as _json
    from datetime import datetime
    payload = {
        "status": status,
        "message": message,
        "timestamp": datetime.now().isoformat(),
    }
    state_file = Path(STATE_DIR) / f"{project}.json"
    state_file.write_text(_json.dumps(payload, ensure_ascii=False),
                          encoding="utf-8")
