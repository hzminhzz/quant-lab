# ruff: noqa: PLR1711
# Keep Marimo's generated cell-return convention.
import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import os

    import marimo as mo
    import matplotlib.pyplot as plt

    from case_studies.phase2.evidence import governed_view
    from case_studies.phase2.synthetic import run_a01

    return governed_view, mo, os, plt, run_a01


@app.cell
def _(mo):
    mo.md(r"""
    # P2-A01 · Conditional closing-session momentum

    **Status: research draft. No market evaluation is admitted.**

    ## 1. Economic question

    Does intraday volatility add predictive information to rest-of-day
    return before the closing window? Baltussen, Da, Lammers and Martens
    (2021) document futures hedging-demand momentum. Gao et al. (2018)
    examine a related but different intraday momentum mechanism.

    This is a proposed **ES index-proxy adaptation**, not a replication on
    exchange futures or proof of dealer gamma. No position or fund flow
    information is inferred from OHLCV.

    ## 2. Session clock and data contract

    Use the XNYS cash-session calendar, including DST and early closes.
    For scheduled close $C$, decide at $t=C-30\mathrm{min}$ using completed,
    available bars only. Proposed entry is the open at $t+1$; exit is the
    open at $C$ **only when that price is observable and executable**.
    An index print is not an executable futures fill.

    $$r_t=\log(P_{\mathrm{last\ completed}}/P_{\mathrm{prior\ close}})$$

    $$s_t=\sqrt{\sum_{u\ \mathrm{in\ session,\ completed\ by}\ t}
    (\Delta\log P_u)^2}$$

    At the session open the return is open-to-first-close. Subsequent
    returns are close-to-close. Missing minutes or an unavailable prior
    close invalidate the affected observation.

    ## 3. Prespecified comparison

    Reference: sign of $r_t$ (direction only). Benchmark: Ridge on $r_t$.
    Candidate: identical Ridge on $(r_t,s_t,r_t s_t)$.

    Label: log return from the execution entry to exit. Identical session
    samples; native chronological ML4T folds; fold-local standardization;
    no tuning. The sign reference is not scored as a calibrated return forecast.
    """)
    return


@app.cell
def _(mo):
    synthetic_button = mo.ui.run_button(label="Run bounded synthetic demonstration")
    evidence_button = mo.ui.run_button(label="Read appliance observation")
    hypothesis_id = mo.ui.text(label="Registered hypothesis ID (optional)", value="")
    mo.vstack([synthetic_button, mo.hstack([hypothesis_id, evidence_button])])
    return evidence_button, hypothesis_id, synthetic_button


@app.cell
def _(mo, os, run_a01, synthetic_button):
    mo.stop(
        not synthetic_button.value and os.environ.get("QUANT_LAB_SYNTHETIC_DEMO") != "1",
        mo.md("Synthetic demonstration not run. No market observations were read."),
    )
    demo = run_a01()
    mo.md("**SYNTHETIC ENGINEERING DEMONSTRATION — NOT MARKET EVIDENCE**")
    return (demo,)


@app.cell
def _(demo, mo, plt):
    tape = demo["forecast_tape"]
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), layout="constrained")
    for column in ("realized", "benchmark", "candidate"):
        axes[0].plot(
            tape["decision_timestamp"].to_list(), tape[column].to_numpy(),
            label=column, alpha=0.8,
        )
    axes[0].set_ylabel("Log return")
    axes[0].legend()
    features = demo["features"]
    axes[1].scatter(
        features["rod"].to_numpy(), features["session_vol"].to_numpy(),
        s=12, alpha=0.65,
    )
    axes[1].set(xlabel="Rest-of-day log return", ylabel="Completed-session volatility")
    mo.vstack([
        mo.md("## 4. Synthetic predictive diagnostics"),
        mo.md("Paired squared-error difference and five-session block uncertainty."),
        mo.ui.table(demo["metrics"]), mo.ui.table(demo["schedule"]),
        mo.ui.table([demo["paired"]]), fig,
    ])
    return


@app.cell
def _(evidence_button, governed_view, hypothesis_id, mo):
    mo.stop(not evidence_button.value, mo.md("No governed evidence requested."))
    evidence = governed_view(hypothesis_id.value)
    mo.json(evidence)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 5. Portfolio and execution economics

    Both return models must use the same frozen signal-to-position map,
    capital and leverage limits, order clock and executable exits.
    Include spread, fees, slippage, financing and latency under Warden.

    An improvement in prediction loss is neither executable PnL nor
    qualification. Reject the incremental forecast claim when evidence
    does not support it; reject the economic claim separately if cost
    stress, matched-risk performance or other applicable gates fail.

    Native single-instrument return target, session sampling, data
    admission and benchmark approval are unresolved. See INTENTS.md
    and DECISION.md.
    """)
    return


if __name__ == "__main__":
    app.run()
