"""启动入口：python run.py"""
import sys
import os

# 将 src 加入 path，确保绝对导入可用
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.main import main

if __name__ == "__main__":
    main()
