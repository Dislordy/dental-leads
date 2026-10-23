"""Runs the pipeline in a background thread and keeps its progress and log for the browser."""
import logging
import threading
import time
from collections import deque

from ..config import load_config
from ..pipeline import run as run_pipeline


class Busy(RuntimeError):
    pass


class Job:
    def __init__(self, cities, limit, refresh):
        self.id = int(time.time() * 1000)
        self.cities, self.limit, self.refresh = cities, limit, refresh
        self.status = "running"          # running | done | failed | stopped
        self.phase, self.done, self.total = "starting", 0, 0
        self.run_id = None
        self.lead_count = None
        self.started_at, self.finished_at = time.time(), None
        self.log = deque(maxlen=1000)
        self.log_offset = 0              # lines dropped from the front of the deque
        self.stop = threading.Event()
        self._lock = threading.Lock()

    def add_log(self, level, message):
        with self._lock:
            if len(self.log) == self.log.maxlen:
                self.log_offset += 1
            self.log.append({"t": time.time(), "level": level, "msg": message})

    def on_progress(self, event):
        self.phase = event.get("phase", self.phase)
        self.done, self.total = event.get("done", self.done), event.get("total", self.total)
        if event.get("run_id"):
            self.run_id = event["run_id"]

    def to_dict(self, after=0):
        with self._lock:
            start = max(0, after - self.log_offset)
            lines = list(self.log)[start:]
            next_index = self.log_offset + len(self.log)
        return {
            "id": self.id, "status": self.status, "phase": self.phase, "done": self.done,
            "total": self.total, "run_id": self.run_id, "lead_count": self.lead_count,
            "cities": self.cities, "limit": self.limit, "started_at": self.started_at,
            "finished_at": self.finished_at, "stopping": self.stop.is_set(),
            "log": lines, "next": next_index,
        }


class _JobLogHandler(logging.Handler):
    def __init__(self, job):
        super().__init__(logging.INFO)
        self.job = job
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.job.add_log(record.levelname, self.format(record))


class JobManager:
    def __init__(self, config_loader=load_config):
        self.current = None
        self._lock = threading.Lock()
        self._load = config_loader

    def start(self, cities, limit=None, refresh=False):
        with self._lock:
            if self.current and self.current.status == "running":
                raise Busy("A search is already running.")
            job = Job(cities, limit, refresh)
            self.current = job
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"job-{job.id}").start()
        return job

    def _run(self, job):
        logger = logging.getLogger("leadgen")
        handler = _JobLogHandler(job)
        logger.addHandler(handler)
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
        try:
            cfg = self._load()
            args = {"city": job.cities, "limit": job.limit, "refresh": job.refresh, "source": "web"}
            leads = run_pipeline(cfg, job.cities, limit=job.limit, yes=True, refresh=job.refresh,
                                 args=args, progress=job.on_progress, stop=job.stop)
            if leads is None:
                job.status = "stopped" if job.stop.is_set() else "failed"
            else:
                job.lead_count = len(leads)
                job.status = "stopped" if job.stop.is_set() else "done"
        except Exception as e:  # surface anything unexpected in the browser log
            logging.getLogger("leadgen").exception("run crashed: %s", e)
            job.status = "failed"
        finally:
            job.finished_at = time.time()
            logger.removeHandler(handler)
