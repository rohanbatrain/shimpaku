"""Statistics helpers, copied from R5 r5common.py (numpy/scipy only; the R5 scripts ran on these)."""
from __future__ import annotations

import math

import numpy as np
from scipy import stats

LOG2 = math.log(2.0)


# ---------------------------------------------------------------- interval estimates
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (Brown, Cai & DasGupta 2001
    recommend it for small n)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def garwood(k: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact (Garwood) Poisson interval for a count k."""
    lo = 0.0 if k == 0 else stats.chi2.ppf(alpha / 2, 2 * k) / 2
    hi = stats.chi2.ppf(1 - alpha / 2, 2 * k + 2) / 2
    return (lo, hi)


# ---------------------------------------------------------------- information theory
def entropy_bits(counts) -> float:
    c = np.asarray(counts, dtype=float)
    c = c[c > 0]
    if c.sum() == 0:
        return 0.0
    p = c / c.sum()
    return float(-(p * np.log2(p)).sum())


def mutual_info_bits(x, y) -> float:
    """Plug-in (maximum-likelihood) mutual information I(X;Y) in bits from paired samples."""
    x = np.asarray(x)
    y = np.asarray(y)
    _, xi = np.unique(x, return_inverse=True)
    _, yi = np.unique(y, return_inverse=True)
    table = np.zeros((xi.max() + 1, yi.max() + 1))
    np.add.at(table, (xi, yi), 1)
    n = table.sum()
    pxy = table / n
    px = pxy.sum(1, keepdims=True)
    py = pxy.sum(0, keepdims=True)
    nz = pxy > 0
    return float((pxy[nz] * np.log2(pxy[nz] / (px @ py)[nz])).sum())


def g_test(x, y) -> tuple[float, int, float]:
    """G statistic = 2 N I(X;Y) [nats]; asymptotically chi2 with (r-1)(c-1) df."""
    n = len(x)
    mi_nats = mutual_info_bits(x, y) * LOG2
    g = 2 * n * mi_nats
    r = len(np.unique(x))
    c = len(np.unique(y))
    df = max(1, (r - 1) * (c - 1))
    return g, df, float(stats.chi2.sf(g, df))


def permutation_mi(x, y, m: int = 2000, rng=None) -> tuple[float, float, float]:
    """Observed MI, mean MI under label permutation, and the Phipson-Smyth p-value (b+1)/(m+1)."""
    rng = np.random.default_rng(rng)
    obs = mutual_info_bits(x, y)
    y = np.asarray(y)
    null = np.empty(m)
    for i in range(m):
        null[i] = mutual_info_bits(x, rng.permutation(y))
    b = int((null >= obs - 1e-12).sum())
    return obs, float(null.mean()), (b + 1) / (m + 1)


# ---------------------------------------------------------------- Bayesian scoring
def log_beta_binom_marginal(k: int, n: int, a: float = 1.0, b: float = 1.0) -> float:
    """log P(k failures in n | Beta(a,b) prior on the rate), excluding the binomial coefficient
    (it cancels in Bayes factors between models of the same data)."""
    from scipy.special import betaln

    return float(betaln(k + a, n - k + b) - betaln(a, b))


def log10_bayes_factor_split(k_in: int, n_in: int, k_out: int, n_out: int) -> float:
    """Bayes factor of 'scope v has its own failure rate' vs 'one rate everywhere',
    uniform Beta(1,1) priors. Positive = evidence for the scope. The Occam factor of the
    extra parameter penalises small n automatically."""
    h1 = log_beta_binom_marginal(k_in, n_in) + log_beta_binom_marginal(k_out, n_out)
    h0 = log_beta_binom_marginal(k_in + k_out, n_in + n_out)
    return (h1 - h0) / math.log(10)


def prob_rate_greater(k_in, n_in, k_out, n_out, draws=200_000, rng=0) -> float:
    """P(p_in > p_out | data) with Beta(1,1) priors, Monte Carlo."""
    rng = np.random.default_rng(rng)
    a = rng.beta(k_in + 1, n_in - k_in + 1, draws)
    b = rng.beta(k_out + 1, n_out - k_out + 1, draws)
    return float((a > b).mean())


def bh_adjust(pvals) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (q-values)."""
    p = np.asarray(pvals, dtype=float)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(q, 1.0)
    return out


# ---------------------------------------------------------------- robust scale
def mad_scale(x) -> float:
    """1.4826 * MAD: a consistent estimator of sigma for Gaussian data. The NIST modified
    z-score 0.6745*(x-med)/MAD equals (x-med)/(1.4826*MAD)."""
    x = np.asarray(x, dtype=float)
    med = np.median(x)
    return float(1.4826 * np.median(np.abs(x - med)))


