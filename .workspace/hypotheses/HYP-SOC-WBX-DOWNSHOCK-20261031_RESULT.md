# Native Stage1 outcome — HYP-SOC-WBX-DOWNSHOCK-20261031

**FROZEN REJECT. No retuning, same-family re-registration, Stage3 or production holdout.**

- Source: Wen et al., North American Journal of Economics and Finance 62 (2022), DOI 10.1016/j.najef.2022.101733; original spot intraday academic mechanism, new Binance perpetual adaptation.
- Prespecified source/rules committed before testing: AI-research-loop `research/independent-crypto-reversal-b16` prereg commit `1c8a95a`. Contract `AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1`.
- Actual native registered HYP id `HYP-SOC-WBX-DOWNSHOCK-20261031`; prefix verification PASS 87,552 pinned market rows after resolving only FeaturePipeline interface engineering and a separately held writer lock.
- Native screening receipt `ATT-31fddf3942a229ee-001`; durable `warden-screen-5292c81134b7a0629d`. PF **1.0590651473** (required 1.15) => **REJECT**, p0.01 gross active-return bootstrap, time-in-market25.92%, conditional gross +2.7175bps per active bar, Warden trade-edge proxy21.009bps, avg holding 7.731 is a turnover proxy not realized holding (four fixed bars).
- Warden costs 6.5bps/side plus funding. Gross bootstrap signal is NOT independent executable after-cost alpha. Source selection multiplicity unmeasured.
- No native full trial, no 2x-cost Stage3, no protected holdout, no capital/order. Registry after **149 hypotheses, 72 full trials, 184 selection attempts, source usage13/20**.
- Strategy `strategies/candidates/soc_wbx_downshock_1h_20261031.py` SHA256 `7d02e44a0c8ea3d706e2496baba366bc001c824be520864d6e157def4387a54e`; spec `experiments/active/soc_wbx_downshock_1h_20261031.yaml` SHA256 `8b3e998cda7590e9696c2dc0bc165864e16939d76ee50398a358946da70e0129`; tests `tests/experiments/test_soc_wbx_downshock_1h_20261031.py` 3 pass, Ruff pass.
- Primary evidence snapshot in AI-research-loop `examples/STRATEGY_RESEARCH_20261031_WBX_DOWNSHOCK_RESULT.json` and `.md`, separate frozen prereg.
- Warden worktree integrity PASS, governance guard READY. Existing dirty/untracked files and quarantined lineages untouched.

The source strategy is a falsified **adaptation** only, not a refutation of the paper's intraday reversal result. Next hypothesis must be economically independent, based on truly available PIT basis/liquidation/order-flow data and authorized matching contract.
