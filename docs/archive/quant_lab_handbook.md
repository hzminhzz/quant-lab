# Quant-Lab Operator's Handbook (`QUANT_LAB_HANDBOOK.md`)

**Document ID:** HANDBOOK-2026-Q4-V1  
**Repository:** `https://github.com/hzminhzz/quant-lab`  
**Target Operator:** Solo Quantitative Trader & Principal Architect  
**Supported Coding Agents:** Oh My Pi (`omp`), OpenCode (`opencode`), Claude Code (`claude`), Codex CLI (`codex`), plus Herdr (`herdr`)  

---

## 1. The Core Mental Model: Is `quant-lab` Autonomous or Built for a Human?

The most common confusion with AI-native quantitative systems is thinking they must be either:
- **A manual Python library** where *you* sit in Jupyter notebooks writing Polars code all day, OR
- **A "push-button money printer"** where an LLM runs 1,000 backtests overnight with no human oversight and trades your account.

**`quant-lab` is neither. It is a "Principal Investigator + Autonomous Lab Assistant" system:**

1. **You (The Human Trader) are the Chief Investment Officer (CIO) & Lab Director.**
   - You **never** need to write boilerplate Polars feature code, walk-forward cross-validation loops, SQLite queries, or `ml4t-backtest` plumbing yourself.
   - Instead, you feed the agent research ideas (a Substack article, an SSRN paper, a market observation, or a batch research theme), define the **rules of the game** (`contracts/*.yaml`: universe, fee/slippage floors, date splits), and make the **final promotion decision** (`warden qualify --unseal-holdout`).
2. **Your Coding Agent (`omp`, `opencode`, `claude`, or `codex`) is the Autonomous Quant Researcher.**
   - Once you give it an article, paper, or hypothesis prompt, the agent works **autonomously** through the entire 5-stage scientific loop: translating the idea into a pre-registered hypothesis, writing the feature pipeline, running the prefix-invariance lookahead prover, screening the signal, running the walk-forward backtest + 2x-cost stress tests, and writing an adversarial audit report.
3. **`warden` (`src/warden/`) is the Non-Bypassable Referee (Deterministic Software).**
   - The AI agent is **not allowed to grade its own homework**. To test any idea, the agent *must* call `uv run warden <subcommand>`.
   - `warden` enforces that the agent cannot peek at your holdout data (`/data/holdout/`), cannot set transaction costs to zero, cannot leak future bars (`shift(-1)`), cannot try more than 3 variations per hypothesis (`trials_used <= 3`), and cannot delete failed runs from `registry/registry.db`.

---

## 2. Role & Responsibility Matrix: Who Does What?

| Activity / Layer | Human CIO (You) | Coding Agent (`omp` / `opencode` / `claude`) | Deterministic `warden` Engine (`src/warden/`) |
| :--- | :--- | :--- | :--- |
| **1. Idea & Source Intake** (Substack post, SSRN paper, market thesis) | Drops the URL / pasted text or research direction into the agent prompt. | Extracts the economic mechanism, counterparty friction, holding horizon, and exact mathematical rules into `.workspace/literature/` and `.workspace/hypotheses/HYP-XXXX.md`. | Validates that the hypothesis frontmatter is complete (`warden register-hypothesis`). |
| **2. Market & Cost Rules** (`contracts/*.yaml`) | **Owns 100%.** Sets fee/slippage floors, bar frequency (`5m`, `15m`, `1h`), and holdout dates. | **Read-Only.** Cannot edit `contracts/*.yaml`. | Verifies SHA-256 integrity (`.warden_manifest.sha256`) before every run; aborts with exit code `2` if tampered. |
| **3. Feature & Strategy Code** (`experiments/active/`, `strategies/candidates/`) | Reviews only when a candidate passes all statistical gates. | **Owns 100% (Autonomous).** Writes Polars `FeaturePipelineProtocol` and `ml4t.backtest.Strategy` classes (`< 250 LOC`). | Runs **Prefix-Invariance Prover** (`warden verify`): shocks future prices $+50\%$ after $t_0$ to prove $0.0$ lookahead leakage. |
| **4. Signal Screening & Walk-Forward Backtesting** | Watches or runs unattended in background (`Herdr`). | Calls `uv run warden screen` and `uv run warden backtest` (up to 3 trials max per hypothesis). | Strips validation labels, runs Purged Walk-Forward CV, simulates next-bar fills, runs `2x` cost and `+1 bar` delay stress tests, and logs every trial to `registry.db`. |
| **5. Selection-Bias Accounting** ($N$ and $K_{\text{eff}}$ DSR) | Inspects summary table. | Cannot edit or delete rows in `registry/registry.db`. | Automatically loads all prior return streams on that contract, computes eigenvalue effective rank $K_{\text{eff}}$, and calculates Deflated Sharpe Ratio ($\text{DSR}$). |
| **6. Adversarial Audit** | Reads `.workspace/hypotheses/AUDIT_<RUN_ID>.md`. | Runs `uv run warden audit-check --run-id <ID>` (and optional `@adversarial-auditor` subagent). | Evaluates all 6 ML4T leakage, outlier, and cost-survival checks deterministically. |
| **7. Holdout Unsealing & Live Deployment** | **Owns 100%.** Runs `warden qualify --run-id <ID> --human-confirm-unseal` once, then deploys to `ml4t-live` shadow/paper mode. | **Strictly Blocked** (POSIX `0640 root:trader` + CLI guard). | Permanently locks the hypothesis lineage after 1 holdout test (`HoldoutAlreadyUnsealedError` on any retry). |

---

## 3. End-to-End Walkthrough: How `quant-lab` Handles a Research Post (e.g., `https://substack.com/inbox/post/218001957`)

Suppose you are reading a Substack research post in your inbox—such as `https://substack.com/inbox/post/218001957`.

> **Important Note on Substack `/inbox/post/<id>` Links:**  
> Links starting with `https://substack.com/inbox/post/...` are authenticated personal reader URLs (tied to your logged-in Substack session), and many quant Substack posts are subscriber-only. If an unauthenticated web tool cannot fetch your `/inbox/post/218001957` URL directly, either **(a)** use the public canonical slug (`https://<publication>.substack.com/p/<slug>`) or **(b)** copy-paste the article text into `.workspace/literature/post_218001957.md` (takes 5 seconds) and tell the agent to read that file.

Here is the exact step-by-step lifecycle of what happens when you hand that research post to `quant-lab`, what the agent does autonomously, what `warden` enforces, and what you do at the end.

### Step 1: Your Input (Takes You 30 Seconds)

You save the article text (or URL) to `.workspace/literature/inbox_218001957.md` (or paste it directly) and send **one prompt** to your coding agent (`omp`, `opencode`, `claude`, or `codex`):

```bash
omp "Read .workspace/literature/inbox_218001957.md and AGENTS.md.
Extract the core trading hypothesis from this research post, map it to our CRYPTO_PERPS_1H_V1 (or FX_MAJORS_15M_V1) contract, and execute the full Quant-Lab research loop autonomously (register-hypothesis -> verify -> screen -> backtest -> audit-check). Stop after producing the verdict and updating .workspace/memory/LESSONS.md."
```

### Step 2: Autonomous Agent Phase 1 — Literature Deconstruction & Pre-Registration (~1 Minute)

Without any further input from you, the coding agent:
1. **Checks Existing Memory First:** Queries `registry/registry.db` (`uv run warden status --json`) and reads `.workspace/memory/LESSONS.md` to verify whether this exact idea (or something collinear with it) was already tested and rejected.
2. **Translates Prose into Falsifiable Economics:** Many Substack articles describe a trading rule loosely (e.g., *"buy when funding is negative and open interest rises"*) without specifying exact lookbacks, holding horizons, or transaction costs. The agent extracts:
   - **Strategy Family:** Is it a single-asset/small-basket directional rule (`rules_ts`), a multi-asset ranking factor (`cross_sectional_ml`), a carry/basis trade (`carry_rv`), or a risk/turnover filter (`meta_overlay`)?
   - **Economic Mechanism & Counterparty:** *Who is losing money on the other side of this trade and why?*
   - **Falsification Criteria:** What exact Net Sharpe, Profit Factor, or Rank IC $t$-stat will prove the Substack article's claim is false after realistic fees?
3. **Registers the Hypothesis in SQLite:** The agent writes `.workspace/hypotheses/HYP-2026-003.md` and runs:
   ```bash
   uv run warden register-hypothesis .workspace/hypotheses/HYP-2026-003.md --json
   ```
   - **What `warden` does here:** Checks **Stage 0 Feasibility** (`< 1 ms`). If the Substack idea trades every 5 minutes for a 3 bps gross expected move on an instrument with 6 bps round-trip costs, `warden` rejects it *immediately* before a single line of strategy code is written! Otherwise, `warden` logs `HYP-2026-003` in `registry.db` with a hard budget of **3 trials maximum** (`trials_used = 0/3`).

### Step 3: Autonomous Agent Phase 2 — Feature Engineering & Prefix-Invariance Proof (~1–2 Minutes)

Next, the agent translates the Substack article's indicators/signals into a clean Polars pipeline (`FeaturePipelineProtocol`) and `Strategy` class inside `strategies/candidates/` and `experiments/active/hyp_2026_003.yaml`, guided by the 61 [`ml4t/skills`](https://github.com/ml4t/skills) in `.agents/skills/`.

Then the agent runs:
```bash
uv run warden verify experiments/active/hyp_2026_003.yaml --json
```
- **What `warden` does here (`src/warden/leakage.py`):**
  - Most Substack backtests look great on paper because the author accidentally used same-bar close prices (`execution_delay_bars = 0`), centered rolling windows, or full-sample z-scores.
  - `warden verify` runs the agent's feature code on bars $1..t_0$, then shocks all future bars after $t_0$ by **+50% price, 3x volume, and +100 bps funding**, and re-runs the pipeline.
  - If even a single floating-point number at $t \le t_0$ changes by more than $10^{-10}$, `warden` raises `LookaheadLeakageError` naming the exact leaking column and timestamp. The agent fixes the bug before any backtest is allowed to run.
  - Once verified causal, `warden` caches the feature matrix to `cache/features/{feature_hash}.parquet`.

### Step 4: Autonomous Agent Phase 3 — Stage 1 Family-Specific Signal Screen (~10 Seconds)

Before running a full portfolio simulation, the agent runs:
```bash
uv run warden screen experiments/active/hyp_2026_003.yaml --json
```
- **What `warden` does here (`src/warden/gates.py`):**
  - Evaluates the signal using the **correct statistical gate for that strategy family**:
    - If the Substack post is a **cross-sectional factor/ML idea** (`cross_sectional_ml`): `warden` computes Purged Walk-Forward out-of-sample Rank IC and Newey-West HAC $t$-stat (requiring $\text{Mean IC} \ge 0.015$ and $t_{\text{HAC}} \ge 2.0$).
    - If the Substack post is a **time-series breakout or technical rule** (`rules_ts`): `warden` *never* forces it through cross-sectional IC; instead it checks conditional return edge after 1.5x one-way cost, Profit Factor $\ge 1.15$, time-in-market $\ge 5\%$, and block-bootstrap $p < 0.05$.
    - If the Substack post is a **stop-loss, volatility target, or regime filter** (`meta_overlay`): `warden` checks whether it cuts 99% CVaR / Max Drawdown by $\ge 15\%$ or cuts turnover by $\ge 20\%$ without hurting Sharpe by more than `0.05`.

### Step 5: Autonomous Agent Phase 4 — Stage 2/3 Walk-Forward Backtest, Stress Suite & 3-Strike Cap (~30 Seconds)

If Stage 1 passes (or as part of the 3-trial exploration), the agent runs:
```bash
uv run warden backtest experiments/active/hyp_2026_003.yaml --json
```
- **What `warden` does here (`src/warden/runner.py` & `registry.py`):**
  1. **Causal Next-Bar Execution:** Simulates trades with `execution_delay_bars >= 1` and deducts mandatory taker fees, slippage, and funding rates from `contracts/*.yaml`.
  2. **Automatic Stress Testing:** Immediately re-runs the strategy under **1.5x costs**, **2.0x costs** (requiring `stress_2x_cost_sharpe > 0.50`), **+1 bar extra latency**, and **$\pm 15\%$ parameter perturbations**.
  3. **Deflated Sharpe Ratio ($K_{\text{eff}}$ DSR):** Appends the trial's out-of-sample return series to `registry/returns/{contract_id}/{run_id}.parquet`, computes the correlation matrix against **all previous trials ever run on that dataset**, calculates the effective number of independent trials $K_{\text{eff}}$, and computes the Deflated Sharpe Ratio probability ($\text{DSR} \ge 0.90$).
  4. **The 3-Strike Rule:** If Trial #1 fails (e.g., gross Sharpe is `1.8` but net Sharpe after fees is `0.4` because turnover is too high), the agent can diagnose the issue and try at most **2 targeted revisions** (e.g., Trial #2: adding a minimum weight-change buffer or slower rebalance cadence; Trial #3: filtering by volatility regime). After 3 trials, `warden` permanently locks `HYP-2026-003` (`HypothesisBudgetExhaustedError` on Trial #4) so the agent can never p-hack parameters to fit noise.

### Step 6: Autonomous Agent Phase 5 — Adversarial Audit & Memory Update (~30 Seconds)

1. **If the Substack idea FAILS (which ~85–95% of public blog strategies do once real fees, 1-bar delay, and walk-forward purging are applied!):**
   - The agent marks the hypothesis `REJECTED`, writes a concise post-mortem in `.workspace/memory/LESSONS.md` explaining *why* it failed (e.g., *"Substack post 218001957's 15m RSI rule had Gross SR 1.9 on same-bar close, but collapsed to Net SR -0.4 under 1-bar delay and 6 bps fees"*), and stops. **You just saved weeks of manual coding and avoided trading a losing strategy.**
2. **If the Substack idea SURVIVES (`verdict: CANDIDATE_FOR_QUALIFICATION`):**
   - The agent runs `uv run warden audit-check --run-id <RUN_ID> --json` (and/or spawns the read-only `@adversarial-auditor` subagent), generating `.workspace/hypotheses/AUDIT_<RUN_ID>.md` verifying all 6 ML4T checks (target alignment, top-5% outlier bar removal survival, timestamp causality, fold-local fitting, 2x-cost survival, and DSR).

### Step 7: Your Final Role as Human CIO (Takes You 2 Minutes)

You only step back in at the very end when the agent reports back:
- **Case A (Strategy Rejected):** You read the 5-line summary in `.workspace/memory/LESSONS.md` and move on to your next idea.
- **Case B (Candidate Promoted!):** You review `.workspace/hypotheses/AUDIT_<RUN_ID>.md`. If you agree the economic mechanism makes sense, **you** run the human-only qualification command to test the frozen strategy *once* on your sealed `/data/holdout/` data:
  ```bash
  uv run warden qualify --run-id <RUN_ID> --unseal-holdout --human-confirm-unseal
  ```
  If `val_to_holdout_sharpe_ratio >= 0.50` and `holdout_net_sharpe > 0.80`, `warden` stamps `PROMOTED`, and you can run that exact `Strategy` class in `ml4t-live` shadow/paper mode (`SafeBroker(..., execution_mode="shadow")`).

---

## 4. The Four Everyday Operating Modes (Copy-Paste Prompts)

Depending on what you want to do on any given day, you can operate `quant-lab` in four modes:

### Mode 1: "Test This Article / Paper / Idea" (Directed Replication Mode)
Use this when you find a Substack post (like `post/218001957`), a tweet thread, an SSRN paper, or have your own trading idea:

```markdown
Read AGENTS.md and the research material in <URL_OR_PATH_IN_WORKSPACE_LITERATURE>.
1. Check `uv run warden status --json` and `.workspace/memory/LESSONS.md` to see our current contracts and past lessons.
2. Formulate a falsifiable hypothesis in `.workspace/hypotheses/HYP-2026-XXX.md` mapped to the most appropriate contract in `contracts/`.
3. Implement the causal feature pipeline and Strategy class in `strategies/candidates/` and `experiments/active/`.
4. Execute `uv run warden register-hypothesis`, `uv run warden verify`, `uv run warden screen`, and `uv run warden backtest`.
5. Remember the Linear-First Gate (test linear/simple baseline before GBDT) and the 3-trial limit.
6. If any trial achieves `CANDIDATE_FOR_QUALIFICATION`, run `uv run warden audit-check --run-id <ID> --json`.
7. Record the empirical outcome in `.workspace/memory/LESSONS.md`, run `uv run warden handoff`, and present a concise executive summary table.
```

### Mode 2: "Autonomous Discovery Campaign" (Agent Proposes & Tests Ideas Within a Theme)
Use this when you don't have a specific article, and want the agent to act as an autonomous alpha researcher overnight or during the afternoon:

```markdown
Read AGENTS.md, `.workspace/memory/LESSONS.md`, and run `uv run warden status --json`.
Run an autonomous research campaign on contract `CRYPTO_PERPS_1H_V1` (or `FX_MAJORS_15M_V1`):
- Budget: Register and test at most 2 distinct economic hypotheses (maximum 6 total trials across this session) focused on <THEME, e.g., "funding rate dispersion + volatility compression" or "cross-sectional lead-lag between BTC/ETH and mid-cap perps">.
- For each hypothesis: pre-register in `.workspace/hypotheses/`, pass `warden verify` (zero lookahead), run `warden screen`, and only run `warden backtest` if Stage 1 passes.
- Log all negative results and microstructure lessons to `.workspace/memory/LESSONS.md`.
- Run `uv run warden handoff` at the end and output a comparison table of all trials run.
```

### Mode 3: "Migrate One Legacy Strategy from `quant-research`" (Selective Salvage Mode)
Use this to test whether a strategy from your old bloated `~/quant-research` repository actually survives strict purged walk-forward validation and realistic transaction costs:

```markdown
Read AGENTS.md. Inspect `~/quant-research/<path_to_old_strategy>` in strictly read-only mode.
Port ONLY the core economic signal/strategy into a clean `< 200 LOC` `quant-lab` pipeline (`strategies/candidates/`) and register it as a hypothesis on `CRYPTO_PERPS_1H_V1`.
Run `warden verify`, `warden screen`, and `warden backtest`. Report whether the legacy strategy passes prefix-invariance lookahead checks and survives 2x transaction costs.
```

### Mode 4: "Human CIO Review & Holdout Qualification" (Your Weekly 15-Minute Review)
Once a week (or after an agent campaign), you inspect the ledger and qualify any winning candidates:

```bash
# 1. Check overall registry health, trial counts, and top candidates:
uv run warden status

# 2. Resume/verify that no files or SQLite records drifted since the agent's last handoff:
uv run warden continue

# 3. Query the SQLite registry directly via DuckDB or sqlite3 for all trials:
sqlite3 -header -column registry/registry.db "
SELECT run_id, hypothesis_id, strategy_family,
       ROUND(gross_sharpe, 2) AS gross_sr,
       ROUND(net_sharpe, 2) AS net_sr,
       ROUND(stress_2x_cost_sharpe, 2) AS sr_2x_cost,
       ROUND(deflated_sharpe_prob, 3) AS dsr,
       global_trial_n AS N,
       ROUND(effective_trials_keff, 2) AS K_eff,
       verdict
FROM trials
ORDER BY created_at DESC;
"

# 4. If a run has verdict = CANDIDATE_FOR_QUALIFICATION and passes your review of AUDIT_<RUN_ID>.md,
#    unseal the holdout dataset ONCE for that hypothesis lineage:
uv run warden qualify --run-id <RUN_ID> --unseal-holdout --human-confirm-unseal
```

---

## 5. Complete CLI Reference (`uv run warden --help`)

Every command in `quant-lab` is exposed through a single deterministic CLI (`uv run warden <subcommand>`) that works identically whether called by you in a terminal or by `omp`, `opencode`, `claude`, or `codex`:

| Command | Who Calls It | What It Does |
| :--- | :--- | :--- |
| `uv run warden status [--json]` | Human & Agent | Displays registered contracts, hypotheses, total trials $N$, current effective trial rank $K_{\text{eff}}$, and worktree integrity status. |
| `uv run warden check-worktree` | Automatic / Human | Verifies SHA-256 checksums of `src/warden/**`, `contracts/**`, and `AGENTS.md` against `.warden_manifest.sha256` and enforces LOC budgets (`src/warden/ < 1,200 LOC`, active experiments `< 250 LOC`). |
| `uv run warden register-hypothesis <path> [--json]` | Agent | Validates `.workspace/hypotheses/HYP-XXXX.md` frontmatter, checks Stage 0 expected-move-to-cost feasibility, and initializes the 3-strike trial counter in `registry.db`. |
| `uv run warden verify <exp_yaml> [--json]` | Agent | Runs the **Prefix-Invariance Leakage Prover** (future price/volume/funding shock test) and caches verified features to `cache/features/{hash}.parquet`. |
| `uv run warden screen <exp_yaml> [--json]` | Agent | Runs **Stage 1 Family-Specific Signal Triage** (`cross_sectional_ml` HAC Rank IC vs. `rules_ts` conditional edge & Profit Factor vs. `carry_rv` vs. `meta_overlay`). |
| `uv run warden backtest <exp_yaml> [--json]` | Agent (or via `Herdr`) | Runs **Stage 2 & 3** Purged Walk-Forward simulation, mandatory cost/slippage/funding deduction, `2x` cost stress, `+1 bar` latency stress, parameter neighborhood stability, and $K_{\text{eff}}$ Deflated Sharpe Ratio. |
| `uv run warden audit-check --run-id <ID> [--json]` | Agent & Subagent | Runs the **6-Check Adversarial Audit** (target alignment, top-5% outlier removal survival, causality, fold isolation, 2x-cost survival, DSR gate) and writes `.workspace/hypotheses/AUDIT_<ID>.md`. |
| `uv run warden handoff --notes "..."` | Agent | Writes a timestamped session transition file in `.workspace/transitions/YYYY-MM-DD/HHMMSS.md` with `# expect:` state assertions. |
| `uv run warden continue [<path>]` | Human & Agent | Replays the latest transition file's `# expect:` assertions to verify zero unlogged Git or SQLite drift between sessions. |
| `uv run warden qualify --run-id <ID> --unseal-holdout --human-confirm-unseal` | **Human CIO Only** | Evaluates a `CANDIDATE_FOR_QUALIFICATION` run **once** on the sealed `/data/holdout/` dataset and permanently locks that hypothesis lineage against future holdout retries. |

---

## 6. How to Add a New Market or Timeframe (`contracts/*.yaml`)

Whenever you want to expand research to a new market (e.g., `XAUUSD` 5m metals, `NAS100` 1h indices, or `CRYPTO_PERPS_15M`), **only you (the human)** add a new YAML file in `contracts/` and re-sign `.warden_manifest.sha256`:

```bash
# 1. Create or edit a contract in contracts/ (e.g., contracts/metals_xauusd_15m_v1.yaml)
# 2. Update the SHA-256 governance manifest so warden recognizes your approved change:
uv run warden generate-manifest
# 3. Verify worktree integrity passes:
uv run warden check-worktree
```

This guarantees that an AI agent can never secretly loosen a contract's fee floor or validation dates to make a failing strategy look profitable.
