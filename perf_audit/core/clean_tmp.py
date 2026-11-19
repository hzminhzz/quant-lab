"""Remove the scratch roots this proof left under perf_audit/ (tmp_proof_*)."""
import shutil
from pathlib import Path

for d in sorted(Path(__file__).resolve().parents[1].glob("tmp_proof_*")):
    shutil.rmtree(d, ignore_errors=True)
    print("removed", d)
