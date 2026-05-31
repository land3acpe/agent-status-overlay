# AgentStatusOverlay

桌面灵动岛风格的 CLI Agent 状态悬浮窗。独立于 CLI 窗口，最小化也能看到 Agent 在做什么。

![screenshot](https://img.shields.io/badge/platform-Windows-blue)

## 效果

屏幕顶部居中的胶囊悬浮窗，实时显示 Agent 状态：

```
┌──────────────────────────────────────┐
│  🟡 🧠 Thinking · 分析需求中...  3s │
└──────────────────────────────────────┘
```

支持 8 种状态，每种有独立颜色和指示灯动画：

| 状态 | 颜色 | 含义 |
|------|------|------|
| 🧠 Thinking | 黄色 | 分析推理中 |
| 💻 Coding | 绿色 | 编写/修改代码 |
| 📋 Planning | 蓝色 | 制定计划 |
| 📖 Reading | 青色 | 读取文件/搜索 |
| ⚡ Executing | 紫色 | 执行命令/工具调用 |
| ⏳ Waiting | 粉色 | 等待用户确认 |
| ❌ Error | 红色 | 出错 |
| 💤 Idle | 灰色 | 空闲 |

Hover 展开显示详细信息，全屏应用下自动降低透明度，空闲 5 分钟后自动隐藏。

## 快速开始

```bash
pip install -r requirements.txt
python run.py
```

悬浮窗出现在屏幕顶部居中。右键系统托盘图标可切换项目或手动测试状态。

### 开机自启

```bash
python install-startup.py
```

## 支持的 Agent

| Agent | 集成方式 | 自动同步 |
|-------|---------|---------|
| **Reasonix Code** | 监控 `.reasonix/sessions/*.events.jsonl` | ✅ |
| **Claude Code** | hooks 自动注入 `~/.claude/settings.json` | ✅ |
| **其他 CLI Agent** | 写入 `~/.agent-status/{project}.json` 或 TCP Socket | 🔧 |

### 通用协议

**文件方式：** 写入 JSON 到 `~/.agent-status/{project}.json`：

```json
{"status": "coding", "message": "编写 src/main.py..."}
```

**Socket 方式：** 发送 JSON 到 `localhost:15555`：

```bash
echo '{"status":"thinking","message":"分析中..."}' | nc 127.0.0.1 15555
```

## 项目结构

```
src/
├── main.py              # 入口：托盘 + 启动所有模块
├── overlay.py           # 灵动岛悬浮窗 (PyQt6)
├── monitor.py           # 文件监听 (watchdog + 轮询)
├── reasonix_monitor.py  # Reasonix 事件监控
├── protocol.py          # 状态枚举 + JSON schema
├── hooks.py             # Claude Code hooks 注入/还原
└── socket_server.py     # TCP :15555 实时推送
```

## 打包发布

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name AgentStatusOverlay run.py
```

输出 `dist/AgentStatusOverlay.exe`，单文件约 30MB，无需 Python 环境。

## 致谢

灵感来自苹果 Dynamic Island。Claude Code hooks 机制参考了 [claude-code-traffic-light](https://github.com/DemoJj/claude-code-traffic-light)。

## License

MIT
