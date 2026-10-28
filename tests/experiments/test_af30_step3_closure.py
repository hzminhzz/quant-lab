"""The Step-3 ledger's tables must match the registry they claim to describe."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_script = Path("scripts/build_af30_step3_closure.py")


def _load():
    spec = importlib.util.spec_from_file_location("build_af30_step3_closure", _script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ledger_tables_match_registry():
    mod = _load()
    assert mod.main(["--check"]) == 0, "regenerate with scripts/build_af30_step3_closure.py"


def test_generated_block_has_every_strategy_and_follows_the_stage3_rows():
    mod = _load()
    block = mod.build()
    for hyp in mod.STRATEGIES:
        assert hyp in block, f"{hyp} missing from the generated tables"
    assert "Counts: 3 CANDIDATE, 3 REVISE" in block
    assert "1.5x" not in block, "the unmeasured 1.5x stress must not appear as a column"
