import altair as alt
import pandas as pd
import streamlit as st

from filters import COHORT_ORDER

WEIGHT = "sentiment_volume"
TOPICS = {
    "topic_camera": "Camera",
    "topic_battery": "Battery",
    "topic_performance": "Performance",
    "topic_price_value": "Price / value",
    "topic_design_display": "Design / display",
}


def weighted_mean(df, columns, weight=WEIGHT):
    """Comment-volume-weighted mean of each column (NaN when there is no volume)."""
    total = df[weight].sum()
    if not total:
        return pd.Series(float("nan"), index=columns)
    return df[columns].mul(df[weight], axis=0).sum() / total


def render(ctx):
    st.title("Audience signals (Method 2)")
    st.caption(ctx.summary)

    if ctx.data.signals.empty:
        st.warning("processed/method2_daily_signals.csv is missing. Run `python src/isolate_method2_data.py`.")
        return
    df = ctx.filtered(ctx.data.signals)
    if df.empty:
        st.warning("No Method 2 rows match the current filters.")
        return

    observed = df[df["sentiment_observed"] == 1]
    overall = weighted_mean(observed, ["sentiment_mean", "sentiment_pos_ratio", "sentiment_neg_ratio"])
    cols = st.columns(4)
    cols[0].metric("Comments", f"{int(df['comments_published'].sum()):,}")
    cols[1].metric("Product-days with comments", f"{len(observed):,}")
    cols[2].metric("Mean sentiment (VADER)", f"{overall['sentiment_mean']:.2f}" if len(observed) else "–")
    cols[3].metric("Positive / negative share",
                   f"{overall['sentiment_pos_ratio']:.0%} / {overall['sentiment_neg_ratio']:.0%}" if len(observed) else "–")
    if observed.empty:
        st.info("No comments were published for the selected products and dates.")
        return

    st.subheader("Weekly comment volume and sentiment")
    weekly = df.assign(week=df["date"].dt.to_period("W").dt.start_time)
    volume = weekly.groupby(["week", "product_type"], as_index=False)["comments_published"].sum()
    sentiment = (
        weekly[weekly["sentiment_observed"] == 1]
        .groupby(["week", "product_type"])
        .apply(lambda g: weighted_mean(g, ["sentiment_mean"])["sentiment_mean"], include_groups=False)
        .rename("sentiment_mean").reset_index()
    )
    cohort_color = alt.Color("product_type:N", title="Cohort", sort=COHORT_ORDER)
    bars = alt.Chart(volume).mark_bar().encode(
        x=alt.X("week:T", title="Week starting"),
        y=alt.Y("comments_published:Q", title="Comments"),
        color=cohort_color,
        tooltip=[alt.Tooltip("week:T"), "product_type:N", "comments_published:Q"],
    ).properties(height=220)
    lines = alt.Chart(sentiment).mark_line(point=True).encode(
        x=alt.X("week:T", title="Week starting"),
        y=alt.Y("sentiment_mean:Q", title="Weighted mean sentiment", scale=alt.Scale(domain=[-1, 1])),
        color=cohort_color,
        tooltip=[alt.Tooltip("week:T"), "product_type:N", alt.Tooltip("sentiment_mean:Q", format=".2f")],
    ).properties(height=220)
    st.altair_chart(alt.vconcat(bars, lines).resolve_scale(x="shared"), width="stretch")

    st.subheader("Topic mix by cohort")
    topics = (
        observed.groupby("product_type")
        .apply(lambda g: weighted_mean(g, list(TOPICS)), include_groups=False)
        .rename(columns=TOPICS).reset_index()
        .melt("product_type", var_name="topic", value_name="share")
    )
    chart = alt.Chart(topics).mark_bar().encode(
        x=alt.X("share:Q", title="Share of comments mentioning topic", axis=alt.Axis(format="%")),
        y=alt.Y("topic:N", title=None),
        yOffset=alt.YOffset("product_type:N", sort=COHORT_ORDER),
        color=cohort_color,
        tooltip=["topic:N", "product_type:N", alt.Tooltip("share:Q", format=".1%")],
    )
    st.altair_chart(chart, width="stretch")
    st.caption(
        "Same-day descriptive signals. For modelling, Method 2 features are lagged to avoid leakage; "
        "see docs/SCRUM_24_METHOD_2_LEAKAGE_AUDIT.md."
    )


if __name__ == "__main__":  # executed by st.navigation as a page script
    from context import current

    render(current())
