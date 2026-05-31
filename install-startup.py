"""安装 AgentStatusOverlay 到 Windows 开机自启"""
import os
import sys

def install():
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.py")
    startup_dir = os.path.join(
        os.environ["APPDATA"],
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )

    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    vbs_path = os.path.join(startup_dir, "AgentStatusOverlay.vbs")

    lines = [
        'Set ws = CreateObject("WScript.Shell")',
        f'ws.Run "{pythonw} {target}", 0, False',
    ]

    os.makedirs(startup_dir, exist_ok=True)
    with open(vbs_path, "w", encoding="ascii") as f:
        f.write("\n".join(lines))

    print(f"OK - installed to {vbs_path}")

if __name__ == "__main__":
    install()
