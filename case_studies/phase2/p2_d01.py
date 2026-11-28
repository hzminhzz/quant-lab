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
    from case_studies.phase2.synthetic import run_d01

    return governed_view, mo, os, plt, run_d01


@app.cell
def _(mo):
    mo.md(r"""
    # P2-D01 · Calendar-conditioned cryptocurrency volatility

    **Status: research draft. No market evaluation is admitted.**

    ## 1. Economic question

    Does the known UTC quarter-hour clock improve forward realized variance
    beyond historical volatility and hour-of-day effects? Hansen, Kim and
    Kimbrough document intraday crypto periodicity; Andersen–Bollerslev and
    Corsi motivate seasonal controls and multiple volatility horizons. The
    directional candle-boundary anomaly is not assumed profitable.

    ## 2. Data contract and causal clock

    Proposed BTCUSDT perpetual minute OHLCV, one decision every five minutes.
    Source bars have minute-open timestamps and explicit availability.
    A completed bar at minute $u$ is admissible only when its information is
    available, no earlier than $u+1$. Missing minutes invalidate whole windows.
    The target covers five open-to-open returns from $t+1$ to $t+6$.

    $$RV_{t} = \sum_{i=1}^{5}\log^2(O_{t+i+1}/O_{t+i})$$

    The boundary count uses the half-open execution interval $[t+1,t+6)$.
    Its value varies on the five-minute clock. Actual dataset admission,
    timestamp evidence and evaluator support remain unresolved.

    ## 3. Frozen comparison draft

    Persistence → Ridge(RV5, RV60, RV1440, UTC hour indicators) →
    identical Ridge plus boundary count. Train-only preprocessing; native
    ML4T walk-forward split; purge through label availability plus embargo.
    Fit log variance, use training-residual smearing for the arithmetic mean,
    and floor variance at $10^{-12}$. No hyperparameter search.
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
def _(mo, os, run_d01, synthetic_button):
    mo.stop(
        not synthetic_button.value and os.environ.get("QUANT_LAB_SYNTHETIC_DEMO") != "1",
        mo.md("Synthetic demonstration not run. No files or market observations were read."),
    )
    demo = run_d01()
    mo.md("**SYNTHETIC ENGINEERING DEMONSTRATION — NOT MARKET EVIDENCE**")
    return (demo,)


@app.cell
def _(demo, mo, plt):
    tape = demo["forecast_tape"].head(288)
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), layout="constrained")
    for column in ("realized", "benchmark", "candidate"):
        axes[0].plot(tape["decision_timestamp"].to_list(), tape[column].to_numpy(),
                     label=column, alpha=0.8)
    axes[0].set_ylabel("Variance (return squared)")
    axes[0].legend()
    features = demo["features"].group_by("boundary_count").len().sort("boundary_count")
    axes[1].bar(features["boundary_count"].to_numpy(), features["len"].to_numpy())
    axes[1].set(xlabel="Upcoming quarter-hour starts", ylabel="Synthetic decision count")
    mo.vstack([
        mo.md("## 4. Synthetic predictive diagnostics"),
        mo.md(r"$QLIKE=\log(\widehat v)+RV/\widehat v$. Lower is better."),
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
    ## 5. Economic decision and falsification

    A later approved overlay sizes the **same preselected base strategy** with
    a common risk budget and leverage cap using predicted volatility. Lower
    QLIKE does not establish better net returns. Warden must own fees, spread,
    slippage, funding, latency, turnover and paired strategy evaluation.

    No base strategy, admitted dataset, forecast materiality or candidate
    approval has been established. Stop on a failed incremental claim;
    never change the horizon, estimator or feature set to reset search debt.
    See INTENTS.md and DECISION.md for the research and approval boundaries.
    """)
    return


if __name__ == "__main__":
    app.run()
