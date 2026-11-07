"""Optional development runner; production demonstration uses Docker Compose."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {'collector': 8101, 'storage': 8102, 'ml_service': 8103, 'web_master': 8104, 'visualization': 8050}


def main():
    children = []
    logs = []
    try:
        for service, port in SERVICES.items():
            path = ROOT / 'runtime' / service
            path.mkdir(parents=True, exist_ok=True)
            stream = (path / 'console.log').open('a', encoding='utf-8')
            logs.append(stream)
            environment = {**os.environ, 'STATE_DIR': str(path), 'PYTHONIOENCODING': 'utf-8'}
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', f'{service}.app:app',
                                        '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT,
                                       env=environment, stdout=stream, stderr=subprocess.STDOUT)
            children.append(process)
            print(f'{service}: http://127.0.0.1:{port}', flush=True)
        while all(process.poll() is None for process in children):
            time.sleep(1)
        raise RuntimeError('A service stopped. See runtime/<service>/console.log')
    except KeyboardInterrupt:
        pass
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
        for process in children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        for stream in logs:
            stream.close()


if __name__ == '__main__':
    main()
