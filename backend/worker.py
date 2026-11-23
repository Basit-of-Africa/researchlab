"""Standalone worker: `python worker.py` (use when RUN_EMBEDDED_WORKER=false)."""
import logging
import signal

from app.services.jobs import Worker

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
w = Worker()
signal.signal(signal.SIGTERM, lambda *_: w.stop())
w.start()
try:
    w.join()
except KeyboardInterrupt:
    w.stop()
