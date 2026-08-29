from dataclasses import dataclass

class IllegalJobStateError(Exception): ...

@dataclass
class Job:
    id: str
    state: str  # "queued" | "running" | "completed" | "failed"

def start_job(job: Job) -> Job:
    if job.state != "queued":
        raise IllegalJobStateError(f"Job {job.id} cannot be started from state {job.state}")
    job.state = "running"
    return job
