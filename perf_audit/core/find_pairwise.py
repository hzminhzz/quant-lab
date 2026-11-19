"""Find the exact numpy pairwise-summation variant that reproduces np.mean(row) bitwise."""
import numpy as np


def psum_rows(x, blocksize=128, nacc=8):
    n = x.shape[1]

    def rec(a):
        m = a.shape[1]
        if m < 8:
            res = a[:, 0].copy()
            for i in range(1, m):
                res = res + a[:, i]
            return res
        if m <= blocksize:
            r = [a[:, i].copy() for i in range(nacc)]
            i = nacc
            while i < m - (m % nacc):
                for k in range(nacc):
                    r[k] = r[k] + a[:, i + k]
                i += nacc
            res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
            while i < m:
                res = res + a[:, i]
                i += 1
            return res
        n2 = (m // 2) - (m // 2) % nacc
        return rec(a[:, :n2]) + rec(a[:, n2:])

    return rec(x) / n


def mean_rows(x):
    return psum_rows(x)


rng = np.random.default_rng(0)
bad = []
for n in range(1, 330):
    rows = 9
    x = rng.normal(0, 1, size=(rows, n)) * rng.lognormal(0, 1, size=(rows, n))
    ref = np.array([np.mean(x[i]) for i in range(rows)])
    got = mean_rows(x)
    if not np.array_equal(ref, got):
        bad.append((n, float(np.max(np.abs(ref - got)))))
print("mismatched n:", bad[:20], "count", len(bad))

# real matrix check
import polars as pl
panel = pl.read_parquet("/home/quant/dev/quant-lab/data/exploratory/crypto_perps_top100_1h.parquet",
                        columns=["timestamp", "asset", "open"])
assets = sorted(panel["asset"].unique().to_list())
wide = panel.pivot(index="timestamp", on="asset", values="open").sort("timestamp").fill_null(0.0)
m = wide.select(assets).to_numpy()
delay, horizon = 1, 24
max_t = m.shape[0] - delay - horizon
entry = m[delay:delay + max_t]
f2 = np.divide(m[delay + horizon:delay + horizon + max_t] - entry, entry, out=np.zeros_like(entry), where=entry > 0)
ref = np.array([np.mean(f2[i]) for i in range(f2.shape[0])])
got = mean_rows(f2)
print("real matrix means identical:", np.array_equal(ref, got), "max diff", float(np.max(np.abs(ref - got))))
