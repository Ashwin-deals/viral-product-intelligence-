"""Demand Forecasts View (Unit 3 Method 1): 10-Model Benchmark & Continuous Trajectory Analysis.

Evaluates 10 forecasting architectures across baselines, classical statistical models, and ML regressors
on the 53-day held-out test split (August 12 to October 3, 2026, 3,180 device-days).
"""
import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

PRED_PREFIX = "pred_"
BASELINE = "Naive_Persistence"


def render(ctx):
    st.title("Demand Forecasts & Model Benchmarks (Method 1)")
    st.caption(ctx.summary)

    # 1. Official Ground-Truth Benchmark Dataset
    benchmark_df = ctx.data.model_comparison
    if benchmark_df.empty:
        st.warning("processed/method1_model_comparison.csv is missing. Run `python src/forecast_method1.py`.")
        return

    # Champion model row (LightGBM)
    champion_row = benchmark_df.iloc[0]

    # --- 1. Executive Champion KPI Summary Ribbon ---
    cols = st.columns(6)
    cols[0].metric(
        "Champion Architecture",
        str(champion_row["model_name"]),
        "Machine Learning",
        help="Evaluated on 3,180 held-out test rows across all 60 devices",
    )
    cols[1].metric(
        "Test MAE",
        f"{float(champion_row['test_mae']):.3f}",
        "-6.6% vs Naive",
        help="Mean Absolute Error on Google Trends search interest (0-100 scale)",
    )
    cols[2].metric(
        "Test RMSE",
        f"{float(champion_row['test_rmse']):.3f}",
        "-10.5% vs Naive",
        help="Root Mean Squared Error penalizing large variance spikes",
    )
    cols[3].metric(
        "WAPE",
        f"{float(champion_row['test_wape_pct']):.2f}%",
        help="Weighted Absolute Percentage Error = sum(|y - y_hat|) / sum(y)",
    )
    cols[4].metric(
        "Out-of-Sample R²",
        f"{float(champion_row['test_r2']):.4f}",
        "+0.066 vs Naive",
        help="Coefficient of Determination: explains 73.55% of test variance",
    )
    cols[5].metric(
        "Directional Accuracy",
        f"{float(champion_row['test_mda_pct']):.2f}%",
        help="Mean Directional Accuracy (MDA) tracking day-over-day sign momentum",
    )

    st.markdown("---")

    # --- 2. Prominent 10-Model Benchmark Comparison Table ---
    st.subheader("10-Model Out-of-Sample Benchmark Comparison")
    st.caption(
        "Official ground-truth benchmark evaluated on the 53-day chronological held-out test split "
        "(3,180 device-days, August 12 to October 3, 2026). Source: `processed/method1_model_comparison.csv`"
    )

    display_table = benchmark_df.copy()

    st.dataframe(
        display_table[[
            "model_name", "model_family", "test_mae", "test_rmse", "test_wape_pct", "test_r2", "test_mda_pct"
        ]],
        hide_index=True,
        width="stretch",
        column_config={
            "model_name": st.column_config.TextColumn("Model / Architecture", help="Candidate model evaluated"),
            "model_family": st.column_config.TextColumn("Family", help="Baseline, Statistical, or Machine Learning"),
            "test_mae": st.column_config.NumberColumn("Test MAE", format="%.3f", help="Mean Absolute Error"),
            "test_rmse": st.column_config.NumberColumn("Test RMSE", format="%.3f", help="Root Mean Squared Error"),
            "test_wape_pct": st.column_config.NumberColumn("WAPE (%)", format="%.2f%%", help="Weighted Absolute Percentage Error"),
            "test_r2": st.column_config.NumberColumn("Out-of-Sample R²", format="%.4f", help="Explanatory power"),
            "test_mda_pct": st.column_config.NumberColumn("MDA (%)", format="%.2f%%", help="Mean Directional Accuracy"),
        },
    )

    # --- 3. Interactive Model Error Comparison & Trajectory Inspector ---
    all_rows = ctx.data.forecasts
    if all_rows.empty:
        st.warning("processed/method1_forecast_predictions.csv is missing.")
        return

    df = ctx.filtered(all_rows)
    if df.empty:
        st.warning(
            "No forecast rows match current filters. Method 1 predictions cover the test period "
            f"{all_rows['date'].min():%Y-%m-%d} → {all_rows['date'].max():%Y-%m-%d}; widen filters in the sidebar."
        )
        return

    st.markdown("---")
    left, right = st.columns([1, 1.4])

    with left:
        st.subheader("Error Comparison by Model (Filtered Selection)")
        models = [c.removeprefix(PRED_PREFIX) for c in df.columns if c.startswith(PRED_PREFIX)]
        err_rows = []
        for m in models:
            e = df[PRED_PREFIX + m] - df["search_interest"]
            err_rows.append({"model": m, "MAE": float(e.abs().mean()), "RMSE": float(np.sqrt((e ** 2).mean()))})
        errors_df = pd.DataFrame(err_rows).sort_values("MAE").reset_index(drop=True)

        chart = alt.Chart(errors_df).mark_bar(cornerRadiusTopRight=3, cornerRadiusBottomRight=3).encode(
            x=alt.X("MAE:Q", title="Test MAE (Filtered Selection)"),
            y=alt.Y("model:N", sort="x", title=None),
            color=alt.condition(alt.datum.model == champion_row["model_name"], alt.value("#e4572e"), alt.value("#156082")),
            tooltip=["model:N", alt.Tooltip("MAE:Q", format=".3f"), alt.Tooltip("RMSE:Q", format=".3f")],
        ).properties(height=340)
        st.altair_chart(chart, width="stretch")

    with right:
        st.subheader("Actual vs Predicted Trajectory Inspector")
        product_ids = sorted(df["product_id"].unique())
        top = df.groupby("product_id")["search_interest"].mean().idxmax()
        pick_col, model_col = st.columns([1.2, 1.8])
        product = pick_col.selectbox("Device", product_ids, index=product_ids.index(top), format_func=ctx.label)
        defaults = [m for m in (str(champion_row["model_name"]), BASELINE) if m in models]
        chosen = model_col.multiselect("Models to Compare", models, default=list(dict.fromkeys(defaults)))

        one = df[df["product_id"] == product].sort_values("date")
        if not chosen:
            chosen = [str(champion_row["model_name"])]

        series = one[["date", "search_interest"] + [PRED_PREFIX + m for m in chosen]].rename(
            columns={"search_interest": "Actual", **{PRED_PREFIX + m: m for m in chosen}}
        ).melt("date", var_name="series", value_name="value")

        traj_chart = alt.Chart(series).mark_line().encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("value:Q", title="Search Interest (0-100)"),
            color=alt.Color("series:N", title=None,
                            scale=alt.Scale(domain=["Actual"] + chosen,
                                            range=["#1E293B", "#E74C3C", "#2980B9", "#27AE60", "#F39C12"][:len(chosen) + 1])),
            strokeDash=alt.condition(alt.datum.series == "Actual", alt.value([1, 0]), alt.value([4, 3])),
            tooltip=["series:N", alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("value:Q", format=".2f")],
        ).properties(height=340)
        st.altair_chart(traj_chart, width="stretch")

    # --- 4. Cohort Dynamics & Feature Importance ---
    st.markdown("---")
    exp_left, exp_right = st.columns(2)

    with exp_left:
        st.subheader("Cohort Dynamics (Product Life-Cycle)")
        st.caption("LightGBM error reduction vs Naive Persistence across product cohorts:")
        cohort_df = ctx.data.cohort_performance

        if not cohort_df.empty:
            st.dataframe(
                cohort_df[["cohort", "sample_rows", "best_model_mae", "naive_mae", "mae_reduction_vs_naive_pct", "best_model_r2"]],
                hide_index=True,
                width="stretch",
                column_config={
                    "cohort": st.column_config.TextColumn("Cohort"),
                    "sample_rows": st.column_config.NumberColumn("Test Rows", format="%d"),
                    "best_model_mae": st.column_config.NumberColumn("Champion MAE", format="%.3f"),
                    "naive_mae": st.column_config.NumberColumn("Naive MAE", format="%.3f"),
                    "mae_reduction_vs_naive_pct": st.column_config.NumberColumn("Error Reduction", format="%.1f%%"),
                    "best_model_r2": st.column_config.NumberColumn("Out-of-Sample R²", format="%.4f"),
                },
            )
        else:
            st.info("Cohort performance summary not found.")

    with exp_right:
        st.subheader("LightGBM Feature Importance")
        st.caption("Top temporal lag predictors accounting for tree split gain:")
        importance = ctx.data.feature_importance
        if not importance.empty:
            top_features = importance.nlargest(10, "ensemble_mean_importance")
            feat_chart = alt.Chart(top_features).mark_bar(cornerRadiusTopRight=3, cornerRadiusBottomRight=3).encode(
                x=alt.X("ensemble_mean_importance:Q", title="Relative Gain Importance"),
                y=alt.Y("feature_name:N", sort="-x", title=None),
                color=alt.Color("feature_category:N", title="Category"),
                tooltip=["feature_name:N", "feature_category:N", alt.Tooltip("ensemble_mean_importance:Q", format=".3f")],
            ).properties(height=220)
            st.altair_chart(feat_chart, width="stretch")
        else:
            st.info("Feature importance data not found.")


if __name__ == "__main__":
    from context import current

    render(current())
