"""Viral Radar & KPI Summary View: Real-time multimodal early-warning system.

Combines Google Trends consumer search momentum (growth & acceleration) with YouTube
audience sentiment and comment buzz to map products into 4 viral quadrants and compute
an actionable Viral Breakout Score (0-100).
"""
import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from filters import COHORT_ORDER

RADAR_METRICS_X = {
    "search_accel": "Search Acceleration (Momentum)",
    "search_growth": "Day-over-Day Growth",
    "mean_interest": "Mean Search Interest (0-100)",
}

RADAR_METRICS_Y = {
    "sentiment_mean": "Audience Sentiment (VADER Compound)",
    "sentiment_pos_ratio": "Positive Sentiment Share",
    "comments_published": "Total Comments Published",
}

TIER_ACTIONS = {
    "Viral Breakout": "Scale channel inventory & initiate hero promotional campaigns",
    "Emerging Hype": "Seed creator units & amplify search advertisement coverage",
    "Core Stable": "Maintain baseline safety stock & optimize price discipline",
    "Fading Demand": "Trigger targeted clearance discounts & manage channel drawdown",
}


def compute_radar_dataset(master_df: pd.DataFrame, signals_df: pd.DataFrame, catalog: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-product multimodal metrics and compute the composite Viral Breakout Score."""
    if master_df.empty:
        return pd.DataFrame()

    # 1. Product-level search dynamics
    grouped = master_df.groupby(["product_id", "product_name", "brand", "product_type"], as_index=False).agg(
        mean_interest=("search_interest", "mean"),
        peak_interest=("search_interest", "max"),
        search_growth=("search_interest_growth", "mean"),
        search_accel=("search_interest_accel", "mean"),
        surge_days=("surge_label", "sum"),
        days_monitored=("date", "nunique"),
    )

    # 2. Merge sentiment signals if available
    if not signals_df.empty:
        sig_agg = signals_df.groupby("product_id", as_index=False).agg(
            comments_published=("comments_published", "sum"),
            sentiment_volume=("sentiment_volume", "sum"),
            sentiment_mean=("sentiment_mean", "mean"),
            sentiment_pos_ratio=("sentiment_pos_ratio", "mean"),
            sentiment_neg_ratio=("sentiment_neg_ratio", "mean"),
            sentiment_net_polarity=("sentiment_net_polarity", "mean"),
        )
        grouped = grouped.merge(sig_agg, on="product_id", how="left")
    else:
        grouped["comments_published"] = 0
        grouped["sentiment_volume"] = 0
        grouped["sentiment_mean"] = 0.0
        grouped["sentiment_pos_ratio"] = 0.0
        grouped["sentiment_neg_ratio"] = 0.0
        grouped["sentiment_net_polarity"] = 0.0

    # Fill NaNs cleanly
    fill_zeros = ["search_growth", "search_accel", "sentiment_mean", "sentiment_pos_ratio",
                  "sentiment_neg_ratio", "sentiment_net_polarity", "comments_published"]
    for col in fill_zeros:
        if col in grouped.columns:
            grouped[col] = grouped[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # 3. Compute Composite Viral Breakout Score (0 - 100)
    # Factor A: Normalized Interest (0-1)
    max_int = grouped["mean_interest"].max() or 1.0
    norm_interest = (grouped["mean_interest"] / max_int).clip(0.0, 1.0)

    # Factor B: Normalized Acceleration (-1 to +1 -> 0 to 1)
    accel_std = grouped["search_accel"].std()
    accel_std = accel_std if accel_std and not np.isnan(accel_std) else 1.0
    norm_accel = 1.0 / (1.0 + np.exp(-grouped["search_accel"] / accel_std))

    # Factor C: Sentiment & Buzz (0 to 1)
    norm_sent = ((grouped["sentiment_mean"] + 1.0) / 2.0).clip(0.0, 1.0)

    raw_score = (0.40 * norm_interest + 0.35 * norm_accel + 0.25 * norm_sent) * 100.0
    grouped["breakout_score"] = raw_score.round(1)

    # 4. Assign Radar Quadrant & Action Tier
    accel_threshold = 0.0
    sent_threshold = 0.05

    def classify_tier(row):
        score = row["breakout_score"]
        accel = row["search_accel"]
        sent = row["sentiment_mean"]
        if score >= 60.0 or (accel > accel_threshold and sent >= sent_threshold):
            return "Viral Breakout"
        elif sent >= sent_threshold and accel <= accel_threshold:
            return "Emerging Hype"
        elif accel > accel_threshold and sent < sent_threshold:
            return "Search Volatility"
        else:
            return "Core Stable" if row["product_type"] == "STABLE" else "Fading Demand"

    grouped["quadrant_tier"] = grouped.apply(classify_tier, axis=1)
    grouped["recommended_action"] = grouped["quadrant_tier"].map(
        lambda t: TIER_ACTIONS.get(t, "Monitor demand velocity across search and social channels")
    )

    return grouped.sort_values("breakout_score", ascending=False).reset_index(drop=True)


def render(ctx):
    st.title("Viral Radar & KPI Summary")
    st.caption(ctx.summary)

    master_df = ctx.filtered(ctx.data.master)
    if master_df.empty:
        st.warning("No master-dataset rows match the current filters. Adjust the sidebar selectors.")
        return

    signals_df = ctx.filtered(ctx.data.signals) if not ctx.data.signals.empty else pd.DataFrame()
    radar_df = compute_radar_dataset(master_df, signals_df, ctx.data.catalog)

    if radar_df.empty:
        st.warning("Unable to compute viral radar metrics with the current selection.")
        return

    # --- 1. Executive KPI Summary Ribbon ---
    breakout_count = int((radar_df["quadrant_tier"] == "Viral Breakout").sum())
    total_surge_days = int(radar_df["surge_days"].sum())
    top_product = radar_df.iloc[0]
    avg_interest = radar_df["mean_interest"].mean()
    avg_pos_share = radar_df["sentiment_pos_ratio"].mean() * 100.0

    kpi_cols = st.columns(5)
    kpi_cols[0].metric(
        "Active Breakouts",
        f"{breakout_count} / {len(radar_df)}",
        help="Products positioned in the high-momentum Viral Breakout quadrant",
    )
    kpi_cols[1].metric(
        "Total Surge Days",
        f"{total_surge_days:,}",
        help="Cumulative detected demand surge occurrences in selected window",
    )
    kpi_cols[2].metric(
        "Top Breakout Lead",
        top_product["product_name"][:18] + ("…" if len(top_product["product_name"]) > 18 else ""),
        f"Score {top_product['breakout_score']:.0f}",
        help=f"{top_product['product_name']} ({top_product['brand']}) has the highest viral propensity",
    )
    kpi_cols[3].metric(
        "Mean Demand Index",
        f"{avg_interest:.1f}",
        help="Average Google Trends search interest (0-100) across selected products",
    )
    kpi_cols[4].metric(
        "Audience Sentiment",
        f"{avg_pos_share:.1f}% Pos",
        help="Average positive sentiment ratio in YouTube comments",
    )

    st.markdown("---")

    # --- 2. Interactive 2D Viral Radar Matrix ---
    st.subheader("2D Multimodal Viral Radar Matrix")
    st.caption("Inspect products across demand momentum (X-axis) and audience sentiment (Y-axis). Bubble size represents search volume.")

    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([1.5, 1.5, 1])
    x_metric = ctrl_col1.selectbox("Radar X-Axis (Demand Velocity)", list(RADAR_METRICS_X.keys()), format_func=RADAR_METRICS_X.get)
    y_metric = ctrl_col2.selectbox("Radar Y-Axis (Audience Buzz)", list(RADAR_METRICS_Y.keys()), format_func=RADAR_METRICS_Y.get)
    show_labels = ctrl_col3.toggle("Show Product Labels", value=True)

    # Base chart
    x_title = RADAR_METRICS_X[x_metric]
    y_title = RADAR_METRICS_Y[y_metric]

    scatter = alt.Chart(radar_df).mark_circle(opacity=0.85, stroke="#1E293B", strokeWidth=1).encode(
        x=alt.X(f"{x_metric}:Q", title=x_title),
        y=alt.Y(f"{y_metric}:Q", title=y_title),
        size=alt.Size("mean_interest:Q", title="Mean Search Interest", scale=alt.Scale(range=[90, 600])),
        color=alt.Color("product_type:N", title="Cohort", sort=COHORT_ORDER,
                        scale=alt.Scale(domain=["RISING", "STABLE", "DECLINING"],
                                        range=["#E74C3C", "#2980B9", "#95A5A6"])),
        tooltip=[
            alt.Tooltip("product_name:N", title="Product"),
            alt.Tooltip("brand:N", title="Brand"),
            alt.Tooltip("product_type:N", title="Cohort"),
            alt.Tooltip("breakout_score:Q", title="Breakout Score (0-100)", format=".1f"),
            alt.Tooltip("quadrant_tier:N", title="Radar Tier"),
            alt.Tooltip(f"{x_metric}:Q", title=x_title, format=".2f"),
            alt.Tooltip(f"{y_metric}:Q", title=y_title, format=".2f"),
            alt.Tooltip("surge_days:Q", title="Surge Days"),
            alt.Tooltip("recommended_action:N", title="Prescriptive Action"),
        ],
    )

    chart = scatter
    if show_labels:
        # Add labels for top products to make the radar intuitive
        labels = alt.Chart(radar_df.head(15)).mark_text(
            align="left", baseline="middle", dx=10, fontSize=10, fontWeight="bold", color="#1E293B"
        ).encode(
            x=f"{x_metric}:Q",
            y=f"{y_metric}:Q",
            text="product_name:N",
        )
        chart = scatter + labels

    # Zero reference lines
    x_mid = 0.0 if x_metric in ["search_accel", "search_growth"] else float(radar_df[x_metric].median())
    y_mid = 0.05 if y_metric == "sentiment_mean" else float(radar_df[y_metric].median())

    vrule = alt.Chart(pd.DataFrame({"x": [x_mid]})).mark_rule(strokeDash=[4, 4], color="#94A3B8").encode(x="x:Q")
    hrule = alt.Chart(pd.DataFrame({"y": [y_mid]})).mark_rule(strokeDash=[4, 4], color="#94A3B8").encode(y="y:Q")

    final_radar_chart = (chart + vrule + hrule).properties(height=420)
    st.altair_chart(final_radar_chart, width="stretch")

    # --- 3. Early-Warning Breakout Leaderboard & Action Table ---
    st.subheader("Viral Breakout Leaderboard & Prescriptive Recommendations")
    st.caption("Ranked by composite Viral Breakout Score (0-100) combining demand acceleration, volume, and audience sentiment.")

    tier_filter = st.multiselect(
        "Filter by Radar Tier",
        options=["Viral Breakout", "Emerging Hype", "Search Volatility", "Core Stable", "Fading Demand"],
        default=["Viral Breakout", "Emerging Hype", "Search Volatility", "Core Stable", "Fading Demand"],
    )

    filtered_leaderboard = radar_df[radar_df["quadrant_tier"].isin(tier_filter)].copy()

    st.dataframe(
        filtered_leaderboard[[
            "breakout_score", "quadrant_tier", "product_name", "brand", "product_type",
            "mean_interest", "search_accel", "surge_days", "sentiment_mean", "recommended_action"
        ]],
        hide_index=True,
        width="stretch",
        column_config={
            "breakout_score": st.column_config.ProgressColumn(
                "Breakout Score", min_value=0.0, max_value=100.0, format="%.1f"
            ),
            "quadrant_tier": st.column_config.TextColumn("Radar Tier"),
            "product_name": st.column_config.TextColumn("Product Name"),
            "brand": st.column_config.TextColumn("Brand"),
            "product_type": st.column_config.TextColumn("Cohort"),
            "mean_interest": st.column_config.NumberColumn("Mean Interest", format="%.1f"),
            "search_accel": st.column_config.NumberColumn("Acceleration", format="%.2f"),
            "surge_days": st.column_config.NumberColumn("Surge Days", format="%d"),
            "sentiment_mean": st.column_config.NumberColumn("Sentiment", format="%.2f"),
            "recommended_action": st.column_config.TextColumn("Prescriptive Action"),
        },
    )

    # --- 4. Brand & Cohort Radar Distribution ---
    left_chart, right_chart = st.columns(2)
    with left_chart:
        st.subheader("Products by Radar Tier")
        tier_counts = radar_df.groupby("quadrant_tier", as_index=False)["product_id"].count().rename(
            columns={"product_id": "Count"}
        )
        bar1 = alt.Chart(tier_counts).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
            x=alt.X("Count:Q", title="Number of Products"),
            y=alt.Y("quadrant_tier:N", title=None, sort="-x"),
            color=alt.Color("quadrant_tier:N", legend=None,
                            scale=alt.Scale(domain=["Viral Breakout", "Emerging Hype", "Search Volatility", "Core Stable", "Fading Demand"],
                                            range=["#E74C3C", "#E67E22", "#F39C12", "#2980B9", "#95A5A6"])),
            tooltip=["quadrant_tier:N", "Count:Q"],
        )
        st.altair_chart(bar1, width="stretch")

    with right_chart:
        st.subheader("Mean Breakout Score by Brand")
        brand_scores = radar_df.groupby("brand", as_index=False)["breakout_score"].mean().sort_values(
            "breakout_score", ascending=False
        )
        bar2 = alt.Chart(brand_scores).mark_bar(color="#156082", cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
            x=alt.X("breakout_score:Q", title="Mean Breakout Score (0-100)"),
            y=alt.Y("brand:N", title=None, sort="-x"),
            tooltip=["brand:N", alt.Tooltip("breakout_score:Q", format=".1f")],
        )
        st.altair_chart(bar2, width="stretch")


if __name__ == "__main__":
    from context import current

    render(current())
