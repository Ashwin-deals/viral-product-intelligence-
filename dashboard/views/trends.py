import altair as alt
import numpy as np
import streamlit as st

MAX_SERIES = 12
METRICS = {
    "search_interest": "Search interest (0-100)",
    "search_interest_growth": "Day-over-day growth",
    "search_interest_accel": "Growth acceleration",
}


def render(ctx):
    st.title("Search-interest trends")
    st.caption(ctx.summary)

    df = ctx.filtered(ctx.data.master)
    if df.empty:
        st.warning("No master-dataset rows match the current filters.")
        return

    left, right = st.columns([2, 1])
    metric = left.selectbox("Metric", list(METRICS), format_func=METRICS.get)
    window = right.slider("Rolling mean (days)", min_value=1, max_value=28, value=7)

    n_products = df["product_id"].nunique()
    top = df.groupby("product_id")["search_interest"].mean().nlargest(MAX_SERIES).index
    if n_products > MAX_SERIES:
        st.info(
            f"Showing the {MAX_SERIES} products with the highest mean search interest out of "
            f"{n_products}. Use the sidebar product filter to compare others."
        )

    plot = df[df["product_id"].isin(top)].sort_values("date").copy()
    plot["product"] = plot["product_id"].map(ctx.label)
    plot["value"] = (
        plot[metric].replace([np.inf, -np.inf], np.nan)
        .groupby(plot["product_id"])
        .transform(lambda s: s.rolling(window, min_periods=1).mean())
    )

    base = alt.Chart(plot).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("value:Q", title=f"{METRICS[metric]} ({window}-day mean)" if window > 1 else METRICS[metric]),
        color=alt.Color("product:N", title="Product"),
    )
    line = base.mark_line().encode(
        tooltip=["product:N", alt.Tooltip("date:T"), alt.Tooltip("value:Q", format=".2f")]
    )
    surges = base.transform_filter(alt.datum.surge_label == 1).mark_point(shape="triangle-up", filled=True, size=50)
    st.altair_chart((line + surges).interactive(bind_y=False), width="stretch")
    st.caption("Triangles mark surge days (`surge_label = 1`). Drag or scroll on the chart to zoom the time axis.")

    st.subheader("Weekly surge calendar")
    weekly = df.assign(
        product=df["product_id"].map(ctx.label),
        week=df["date"].dt.to_period("W").dt.start_time,
    ).groupby(["product", "week"], as_index=False)["surge_label"].sum()
    heat = alt.Chart(weekly).mark_rect().encode(
        x=alt.X("week:T", title="Week starting"),
        y=alt.Y("product:N", title=None),
        color=alt.Color("surge_label:Q", title="Surge days", scale=alt.Scale(scheme="oranges")),
        tooltip=["product:N", alt.Tooltip("week:T", title="Week"), alt.Tooltip("surge_label:Q", title="Surge days")],
    ).properties(height=max(120, 14 * weekly["product"].nunique()))
    st.altair_chart(heat, width="stretch")


if __name__ == "__main__":  # executed by st.navigation as a page script
    from context import current

    render(current())
