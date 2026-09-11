import threading
import time
from datetime import datetime
from croniter import croniter


class CronScheduler:
    """A minimal cron-based scheduler using croniter."""

    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()
        self._running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def add_job(self, func, cron_expr, args=None, job_id=None):
        """Add or replace a job with given cron expression."""
        if args is None:
            args = []
        itr = croniter(cron_expr, datetime.now())
        with self.lock:
            self.jobs[job_id] = {
                "func": func,
                "args": args,
                "cron": cron_expr,
                "iter": itr,
                "next_run": itr.get_next(datetime),
                "name": func.__name__,
            }

    def remove_job(self, job_id):
        with self.lock:
            self.jobs.pop(job_id, None)

    def get_jobs(self):
        with self.lock:
            result = []
            for jid, job in self.jobs.items():
                result.append(
                    {
                        "id": jid,
                        "name": job["name"],
                        "func": str(job["func"]),
                        "trigger": job["cron"],
                        "next_run": str(job["next_run"]),
                    }
                )
            return result

    def shutdown(self):
        self._running = False
        self.thread.join()

    def _run(self):
        while self._running:
            now = datetime.now()
            to_run = []
            with self.lock:
                for jid, job in self.jobs.items():
                    if job["next_run"] <= now:
                        to_run.append((jid, job))
            for jid, job in to_run:
                threading.Thread(target=job["func"], args=job["args"], daemon=True).start()
                with self.lock:
                    job["next_run"] = job["iter"].get_next(datetime)
            time.sleep(1)
