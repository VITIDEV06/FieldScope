"""QA server with graceful shutdown over stdin; no shutdown endpoint in the app."""
import sys
import threading
from pathlib import Path
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
server = uvicorn.Server(uvicorn.Config("main:app", host="127.0.0.1", port=8001))


def stop_on_stdin():
    sys.stdin.readline()
    server.should_exit = True


threading.Thread(target=stop_on_stdin, daemon=True).start()
server.run()
