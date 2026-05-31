"""启动入口：python run.py"""
import sys
import os

# ── 最早阶段：创建运行标记（防重复启动）──
_MARKER_DIR = os.path.join(os.path.expanduser("~"), ".agent-status")
_MARKER_FILE = os.path.join(_MARKER_DIR, ".overlay.running")
os.makedirs(_MARKER_DIR, exist_ok=True)
with open(_MARKER_FILE, "w") as _f:
    _f.write(str(os.getpid()))

# 将 src 加入 path，确保绝对导入可用
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.main import main

if __name__ == "__main__":
    main()
