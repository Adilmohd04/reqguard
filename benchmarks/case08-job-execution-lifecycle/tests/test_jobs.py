from jobs.scheduler import Job, start_job

def test_start_queued_job():
    job = Job(id="j1", state="queued")
    started = start_job(job)
    assert started.state == "running"

def test_start_job_returns_job():
    job = Job(id="j2", state="queued")
    res = start_job(job)
    assert res.id == "j2"
# NOTE: No test attempts starting a job in running/completed states.
