"""SocketServer: 本地 TCP 服务端，接收实时状态推送"""
import json
import socket
import threading
from pathlib import Path
import os

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 15555
DEFAULT_STATE_DIR = os.path.expanduser("~/.agent-status")


class SocketServer:
    """轻量 TCP 服务端：接收 JSON 状态消息 → 写入状态文件"""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 state_dir: str = DEFAULT_STATE_DIR):
        self.host = host
        self.port = port
        self.state_dir = Path(state_dir)
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        """在后台线程启动 Socket 服务"""
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        # 发送一个空连接来唤醒 accept
        try:
            with socket.create_connection((self.host, self.port), timeout=1):
                pass
        except OSError:
            pass

    def _serve(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)

        try:
            sock.bind((self.host, self.port))
            sock.listen(5)
        except OSError:
            return  # 端口被占用，静默退出

        while self._running:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                conn.settimeout(2.0)
                data = conn.recv(4096)
                if data:
                    self._handle(data, conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

        try:
            sock.close()
        except OSError:
            pass

    def _handle(self, data: bytes, conn: socket.socket):
        """解析 JSON → 写入状态文件"""
        try:
            payload = json.loads(data.decode("utf-8"))
            status = payload.get("status", "idle")
            message = payload.get("message", "")
            project = payload.get("project", "default")

            from datetime import datetime
            full = {
                "status": status,
                "message": message,
                "timestamp": datetime.now().isoformat(),
            }
            if "metadata" in payload:
                full["metadata"] = payload["metadata"]

            self.state_dir.mkdir(parents=True, exist_ok=True)
            state_file = self.state_dir / f"{project}.json"
            state_file.write_text(json.dumps(full, ensure_ascii=False),
                                  encoding="utf-8")

            conn.sendall(b'{"ok":true}\n')
        except (json.JSONDecodeError, UnicodeDecodeError):
            try:
                conn.sendall(b'{"ok":false,"error":"invalid json"}\n')
            except OSError:
                pass
