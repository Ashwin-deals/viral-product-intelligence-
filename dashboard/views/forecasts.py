import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

PRED_PREFIX = "pred_"
BASELINE = "Naive_Persistence"


def model_errors(df, models):
    """MAE/RMSE per model on the given prediction rows."""
    rows = []
    for model in models:
        err = df[PRED_PREFIX + model] - df["search_interest"]
        rows.append({"model": model, "MAE": err.abs().mean(), "RMSE": float(np.sqrt((err ** 2).mean()))})
    return pd.DataFrame(rows).sort_values("MAE").reset_index(drop=True)


def render(ctx):
    st.title("Demand forecasts (Method 1)")
    st.caption(ctx.summary)

    all_rows = ctx.data.forecasts
    if all_rows.empty:
        st.warning("processed/method1_forecast_predictions.csv is missing. Run `python src/forecast_method1.py`.")
        return
    df = ctx.filtered(all_rows)
    if df.empty:
        st.warning(
            "No forecast rows match the current filters. Method 1 predictions cover the test period "
            f"{all_rows['date'].min():%Y-%m-%d} → {all_rows['date'].max():%Y-%m-%d}; widen the date range."
        )
        return

    models = [c.removeprefix(PRED_PREFIX) for c in df.columns if c.startswith(PRED_PREFIX)]
    errors = model_errors(df, models)
    best = errors.iloc[0]
    baseline_mae = errors.set_index("model")["MAE"].get(BASELINE)

    cols = st.columns(4)
    cols[0].metric("Best model (current selection)", best["model"])
    cols[1].metric("Best MAE", f"{best['MAE']:.2f}")
    if baseline_mae:
        cols[2].metric("vs naive persistence", f"{(1 - best['MAE'] / baseline_mae):.1%}", help="MAE reduction")
    cols[3].metric("Rows evaluated", f"{len(df):,}")

    left, right = st.columns([1, 2])
    with left:
        st.subheader("Error by model")
        chart = alt.Chart(errors).mark_bar().encode(
            x=alt.X("MAE:Q"),
            y=alt.Y("model:N", sort="x", title=None),
            color=alt.condition(alt.datum.model == best["model"], alt.value("#e4572e"), alt.value("#4c78a8")),
            tooltip=["model:N", alt.Tooltip("MAE:Q", format=".2f"), alt.Tooltip("RMSE:Q", format=".2f")],
        )
        st.altair_chart(chart, width="stretch")
    with right:
        st.subheader("Actual vs predicted")
        product_ids = sorted(df["product_id"].unique())
        top = df.groupby("product_id")["search_interest"].mean().idxmax()
        pick_col, model_col = st.columns(2)
        product = pick_col.selectbox("Product", product_ids, index=product_ids.index(top), format_func=ctx.label)
        defaults = [m for m in (best["model"], BASELINE) if m in models]
        chosen = model_col.multiselect("Models", models, default=list(dict.fromkeys(defaults)))

        one = df[df["product_id"] == product].sort_values("date")
        series = one[["date", "search_interest"] + [PRED_PREFIX + m for m in chosen]].rename(
            columns={"search_interest": "Actual", **{PRED_PREFIX + m: m for m in chosen}}
        ).melt("date", var_name="series", value_name="value")
        chart = alt.Chart(series).mark_line().encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("value:Q", title="Search interest"),
            color=alt.Color("series:N", title=None),
            strokeDash=alt.condition(alt.datum.series == "Actual", alt.value([1, 0]), alt.value([4, 3])),
            tooltip=["series:N", alt.Tooltip("date:T"), alt.Tooltip("value:Q", format=".2f")],
        )
        st.altair_chart(chart, width="stretch")

    with st.expander("Benchmark on the full test set (not filtered)"):
        if ctx.data.model_comparison.empty:
            st.info("processed/method1_model_comparison.csv is missing.")
        else:
            st.dataframe(ctx.data.model_comparison, hide_index=True, width="stretch")
    with st.expander("Feature importance (not filtered)"):
        importance = ctx.data.feature_importance
        if importance.empty:
            st.info("processed/method1_feature_importance.csv is missing.")
        else:
            top_features = importance.nlargest(15, "ensemble_mean_importance")
            chart = alt.Chart(top_features).mark_bar().encode(
                x=alt.X("ensemble_mean_importance:Q", title="Ensemble mean importance"),
                y=alt.Y("feature_name:N", sort="-x", title=None),
                color=alt.Color("feature_category:N", title="Category"),
                tooltip=["feature_name:N", "feature_category:N",
                         alt.Tooltip("ensemble_mean_importance:Q", format=".3f")],
            )
            st.altair_chart(chart, width="stretch")


if __name__ == "__main__":  # executed by st.navigation as a page script
    from context import current

    render(current())
