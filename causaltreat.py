import numpy as np
from statsmodels.regression.linear_model import WLS


def partition(d, prob_m=0.5):
    """Assign observations to main ("m") and auxiliary ("a") sample, stratified by treatment status

    Parameters
    ----------
    d : ndarray
        treatment indicator
    prob_m : float, optional
        [description], by default 0.5

    Returns
    -------
    ndarray
        vector with sample information
    """

    rowid_treat = np.where(d == 1)[0].flatten()  # treated obs
    rowid_ctrl = np.where(d == 0)[0].flatten()  # control obs

    rowid_treat_main = np.random.choice(
        a=rowid_treat,
        size=np.floor(prob_m * len(rowid_treat)).astype(int),
        replace=False,
    )
    rowid_ctrl_main = np.random.choice(
        a=rowid_ctrl, size=np.floor(prob_m * len(rowid_ctrl)).astype(int), replace=False
    )

    sample = np.repeat("a", repeats=len(d))
    rowid_main = np.append(rowid_treat_main, rowid_ctrl_main)
    sample[rowid_main] = "m"

    return sample


def ml_proxy(model, x, y, d, sample):
    """Wrapper to calculate ML Proxy from sklearn-type models

    Parameters
    ----------
    model : [type]
        Model instance, must implement .fit and .predict
    x : ndarray
        Features
    y : ndarray
        vector of outcomes
    d : ndarray
        treatment indicator
    sample : ndarray
        vector with sample information, typically generated by partition()

    Returns
    -------
    (ndarray, ndarray)
        vectors with estimated baseline effect and estimated treatment effect
    """
    id_ctrl_a = (sample == "a") & (d == 0)
    model.fit(x[id_ctrl_a,], y[id_ctrl_a])
    pred_ctrl = model.predict(x)

    id_treat_a = (sample == "a") & (d == 1)
    model.fit(x[id_treat_a,], y[id_treat_a] - pred_ctrl[id_treat_a])
    pred_treat = model.predict(x)

    b_hat = pred_ctrl  # baseline effect given X
    s_hat = pred_treat  # treatment effect given X

    return b_hat, s_hat


def blp(y, d, prop, b_hat, s_hat, print_table=True):
    """Return intercept and slope for Best Linear Predictor (BLP)

    Parameters
    ----------
    y : ndarray
        vector of outcomes
    d : ndarray
        treatment indicator
    prop : ndarray
        treatment propensity
    b_hat : ndarray
        [description]
    s_hat : ndarray
        [description]
    print_table : bool, optional
        Toggle results table, by default True

    Returns
    -------
    dict
        results for ATE and HET
    """

    # Calculate model matrix
    y_reg = y  # outcome
    w_reg = (prop * (1 - prop)) ** (-1)  # weights
    x_reg = np.column_stack(
        (
            np.repeat(1, repeats=len(y)),  # constant
            b_hat,  # baseline b0
            d - prop,  # average treatment effect ate
            (d - prop) * (s_hat - np.mean(s_hat)),  # heterogeneity het
        )
    )
    labels = ["const.", "b0", "ate", "het"]

    # Run weighted least squares
    wls = WLS(endog=y_reg, exog=x_reg, w=w_reg)
    wls = wls.fit()

    if print_table:
        print(wls.summary(xname=labels))

    return {
        "ate": wls.params[labels.index("ate")],
        "het": wls.params[labels.index("het")],
    }


def quantile_grid(x, q):
    """Cut x into q intervals of equal size

    Parameters
    ----------
    x : ndarray
        numeric vector
    q : int
        number of intervals

    Returns
    -------
    tuple
        tuple of bin indices, edges, and associated quantiles
    """
    bin_pct = np.linspace(0, 100, num=q, endpoint=False)
    bin_edges = np.percentile(a=x, q=bin_pct)
    bin_indices = (
        np.digitize(x=x, bins=np.append(bin_edges, np.Inf)) - 1
    )  # Reference: left edge
    return bin_indices, bin_edges, bin_pct


def gates(y, d, prop, s_hat, q=10, print_table=True):
    """Calculate Group Average Treatment Effect

    Parameters
    ----------
    y : ndarray
        vector of outcomes
    d : ndarray
        treatment indicator
    prop : ndarray
        treatment propensity
    s_hat : ndarray
        estimated treatment effect
    q : int, optional
        number of groups, by default 10
    print_table : bool, optional
        toggle results table, by default True

    Returns
    -------
    dict
        results with baseline and treatment effect for each group
    """

    # Define groups
    bin_indices, bin_edges, bin_pct = quantile_grid(
        x=s_hat + 1e-16 * np.random.uniform(size=len(s_hat)), q=q  # Break ties
    )

    # Dummy coding
    s_onehot = np.zeros((len(s_hat), len(bin_edges)))
    s_onehot[np.arange(0, len(s_hat)), bin_indices] = 1

    # Calculate model matrix
    x_reg = np.column_stack(
        (s_onehot, s_onehot * np.reshape(d - prop, newshape=(-1, 1)))
    )
    w_reg = (prop * (1 - prop)) ** (-1)  # weights
    y_reg = y

    # Run weighted least squares
    labels_baseline = [
        f"Baseline: p={p / 100:.2f} ({x:.2f})"
        for p, x in zip(bin_pct.tolist(), bin_edges.tolist())
    ]
    labels_treatment = [
        f"Treatment: p={p / 100:.2f} ({x:.2f})"
        for p, x in zip(bin_pct.tolist(), bin_edges.tolist())
    ]
    labels = labels_baseline + labels_treatment

    wls = WLS(endog=y_reg, exog=x_reg, w=w_reg)
    wls = wls.fit()

    if print_table:
        print(wls.summary(xname=labels))

    return {
        "coef_baseline": wls.params[: len(labels_baseline)],
        "coef_treatment": wls.params[len(labels_baseline) :],
        "bin_values": bin_edges,
        "bin_count": np.sum(s_onehot, axis=0),
    }


def combine(model, x, y, d, prop, second_stage="blp", q=10, prob_m=0.5):
    """Combine first and second stage for a given model

    Parameters
    ----------
    model : model
        model instance, must implement .fit and .predict
    x : ndarray
        features
    y : ndarray
        vector of outcomes
    d : ndarray
        treatment indicator
    prop : ndarray
        treatment propensity
    second_stage : str, optional
        method used in second stage, by default "blp"
    q : int, optional
        number of groups, only relevant if second_stage="gates", by default 10
    prob_m : float, optional
        share of dataset used in main sample, by default 0.5

    Returns
    -------
    dict
        dictionary with estimation results

    Raises
    ------
    ValueError
        if second_stage is neither "blp" nor "gates"
    """

    # Step 1:
    # Partition the data in to "m" (for main) and "a" (for auxiliary)
    # Likelihood for main sample = prob_m
    # Stratify by treatment status
    sample = partition(d, prob_m=prob_m)

    # Step 2: Fit model on auxiliary sample and collect predictions for main sample
    b_hat, s_hat = ml_proxy(model, x, y, d, sample)

    # Step 3: Calculate estimates on main sample
    if second_stage == "blp":
        results = blp(
            y=y[sample == "m"],
            d=d[sample == "m"],
            prop=prop[sample == "m"],
            b_hat=b_hat[sample == "m"],
            s_hat=s_hat[sample == "m"],
            print_table=False,
        )
        return results

    elif second_stage == "gates":
        results = gates(
            y=y[sample == "m"],
            d=d[sample == "m"],
            prop=prop[sample == "m"],
            s_hat=s_hat[sample == "m"],
            q=q,
            print_table=False,
        )
        return results

    else:
        raise ValueError('Argument second_stage must be "blp" or "gates"')
