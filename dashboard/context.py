"""What every page receives: the loaded datasets plus the active global filters.

app.py publishes a PageContext into the session state on every rerun, before the selected
page script runs; the page reads it back with current(). Session state keeps concurrent
users' filters apart.
"""
from dataclasses import dataclass

import streamlit as st

import filters as flt
from data_loader import Datasets

CONTEXT_KEY = "_page_context"


def publish(ctx):
    st.session_state[CONTEXT_KEY] = ctx


def current():
    ctx = st.session_state.get(CONTEXT_KEY)
    if ctx is None:
        st.error("Open the dashboard through its entry point: `streamlit run dashboard/app.py`.")
        st.stop()
    return ctx


@dataclass(frozen=True)
class PageContext:
    data: Datasets
    state: flt.FilterState

    @property
    def product_ids(self):
        return flt.selected_product_ids(self.data.catalog, self.state)

    @property
    def summary(self):
        return flt.describe(self.state, self.data.catalog)

    def filtered(self, df, date_col="date"):
        return flt.apply_filters(df, self.state, self.data.catalog, date_col)

    def label(self, product_id):
        return flt.product_label(self.data.catalog)(product_id)
