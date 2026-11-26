# Cash-safe target weights for Warden rules strategies

Use `warden.rebalancer.rebalance_cash_weights` in a `CausalRulesStrategy` running under a
non-leveraged evaluation contract. Store the `contract` passed to `configure_strategy`; call
the helper in `Strategy.on_data` when your signal calls for a rebalance:

```python
from warden.rebalancer import rebalance_cash_weights

# Inside on_data: weights is this decision's complete desired book, not a patch.
rebalance_cash_weights(broker, self.contract, weights)
```

- The helper reads the contract cap through Warden's execution policy. It scales the complete
  requested book proportionally if `sum(abs(weight))` exceeds `cap * (1 - headroom)`. The default
  `headroom=0.15` reserves 15% of the cap for costs and price movement; it is not a leverage override.
- It submits position reductions before additions, using the pinned ML4T broker's target-value
  primitive. Zero, omitted and dropped targets explicitly close held positions, even below the
  dust thresholds. An empty book requests full liquidation. If the broker permits shorts, a
  sign reversal closes the original leg first; pass the new target again on a later decision.
- `min_trade_value` (default 1.0 in account currency) and `min_weight_change` (default 0.0 as a
  fraction of equity) suppress small nonzero adjustments. Held exposure left in place by the
  deadband still uses the gross budget. Declare any strategy-specific numeric overrides through
  the strategy's parameter model.
- New exposure waits if an existing position lacks a current price or orders remain pending.
  A missing bar is not a fill: an exit can execute only when the Engine has an eligible price.
  Remove an inactive asset from the target book using information known at the decision time;
  do not predict the tape's last observed bar. Call again when the asset becomes executable.

The helper submits market orders and returns the submitted orders, not fills. Warden still owns
execution delay, price, commission, slippage, funding, cash checks and realized gross checks.
Headroom is a sizing buffer, not a guarantee against larger future gaps or unfilled reductions;
`warden verify` still rejects an insufficient-cash or over-cap run. Do not pre-lag targets or
substitute the next open for the decision-time reference price.

Run the synthetic Engine and Warden verify tests with:

```sh
uv run --frozen --offline pytest -q tests/test_cash_rebalancer.py
```
