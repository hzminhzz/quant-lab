"""Canonical additive operational job tables; scientific state remains in domain receipts."""
JOB_DDL = """
CREATE TABLE IF NOT EXISTS control_resource_policy (
    policy_id TEXT PRIMARY KEY CHECK(policy_id='host'), policy_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS control_jobs (
    job_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
    objective TEXT NOT NULL, resources_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS control_job_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES control_jobs(job_id),
    state TEXT NOT NULL, fence TEXT, details_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS control_job_event_lookup ON control_job_events(job_id,sequence);
""" + "\n".join(
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
    "BEGIN SELECT RAISE(ABORT,'append-only job evidence'); END;"
    for table in ("control_resource_policy", "control_jobs", "control_job_events")
    for op in ("UPDATE", "DELETE")
)
