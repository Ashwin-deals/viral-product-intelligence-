import altair as alt
import streamlit as st

from filters import COHORT_ORDER


def render(ctx):
    st.title("Overview")
    st.caption(ctx.summary)

    df = ctx.filtered(ctx.data.master)
    if df.empty:
        st.warning("No master-dataset rows match the current filters.")
        return

    cols = st.columns(5)
    cols[0].metric("Products", df["product_id"].nunique())
    cols[1].metric("Brands", df["brand"].nunique())
    cols[2].metric("Days", df["date"].nunique())
    cols[3].metric("Surge days", int(df["surge_label"].sum()))
    cols[4].metric("Mean search interest", f"{df['search_interest'].mean():.1f}")

    left, right = st.columns(2)
    with left:
        st.subheader("Mean search interest by cohort")
        daily = df.groupby(["date", "product_type"], as_index=False)["search_interest"].mean()
        chart = alt.Chart(daily).mark_line().encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("search_interest:Q", title="Mean search interest"),
            color=alt.Color("product_type:N", title="Cohort", sort=COHORT_ORDER),
            tooltip=[alt.Tooltip("date:T"), "product_type:N", alt.Tooltip("search_interest:Q", format=".1f")],
        )
        st.altair_chart(chart, width="stretch")
    with right:
        st.subheader("Surge days by brand")
        by_brand = df.groupby("brand", as_index=False)["surge_label"].sum()
        chart = alt.Chart(by_brand).mark_bar().encode(
            x=alt.X("surge_label:Q", title="Surge days"),
            y=alt.Y("brand:N", title=None, sort="-x"),
            tooltip=["brand:N", alt.Tooltip("surge_label:Q", title="Surge days")],
        )
        st.altair_chart(chart, width="stretch")

    st.subheader("Product summary")
    summary = (
        df.groupby(["product_id", "product_name", "brand", "product_type"], as_index=False)
        .agg(
            mean_interest=("search_interest", "mean"),
            peak_interest=("search_interest", "max"),
            surge_days=("surge_label", "sum"),
            yt_on_target_share=("yt_on_target_share", "first"),
            usable_product=("usable_product", "first"),
        )
        .sort_values("mean_interest", ascending=False)
    )
    st.dataframe(
        summary, hide_index=True, width="stretch",
        column_config={
            "product_id": "ID",
            "product_name": "Product",
            "brand": "Brand",
            "product_type": "Cohort",
            "mean_interest": st.column_config.NumberColumn("Mean interest", format="%.1f"),
            "peak_interest": st.column_config.NumberColumn("Peak interest", format="%.0f"),
            "surge_days": "Surge days",
            "yt_on_target_share": st.column_config.ProgressColumn(
                "YouTube on-target share", min_value=0.0, max_value=1.0, format="percent"),
            "usable_product": st.column_config.CheckboxColumn("Usable"),
        },
    )


if __name__ == "__main__":  # executed by st.navigation as a page script
    from context import current

    render(current())
