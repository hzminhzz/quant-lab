"""Headless Marimo witness. Defaults to synthetic-only with no live observation."""
import argparse
import os

from case_studies.phase2.evidence import governed_view
from case_studies.phase2.p2_a01 import app as a01
from case_studies.phase2.p2_d01 import app as d01
from case_studies.phase2.synthetic import SYNTHETIC


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-appliance", action="store_true")
    args = parser.parse_args()
    before = governed_view() if args.observe_appliance else None
    os.environ.pop("QUANT_LAB_SYNTHETIC_DEMO", None)
    for name, app in (("D01", d01), ("A01", a01)):
        _, definitions = app.run()
        assert "demo" not in definitions
        print(f"{name}: default idle")
    os.environ["QUANT_LAB_SYNTHETIC_DEMO"] = "1"
    for name, app in (("D01", d01), ("A01", a01)):
        outputs, definitions = app.run()
        result = definitions["demo"]
        assert result["scope"] == SYNTHETIC
        assert result["schedule"].height == 4
        print(f"{name}: {result['eligible_rows']} eligible; "
              f"{result['forecast_tape'].height} validation predictions; "
              f"{len(outputs)} rendered cells")
    if before is not None:
        after = governed_view()
        for view in (before, after):
            assert view["status"] == "READ_ONLY_OBSERVATION"
            assert view["scientific_writes"] == view["evaluator_calls"] == 0
            assert view["sealed_access"] is False
        assert before["counts"] == after["counts"]
        print("Appliance observations: scientific ledger counts unchanged")


if __name__ == "__main__":
    main()
