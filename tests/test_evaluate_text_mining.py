import numpy as np
import pandas as pd

import evaluate_text_mining as etm


def test_compare_methods_scores_against_reference_and_skips_unclear():
    data = pd.DataFrame({
        "reference_label": ["positive", "negative", "negative", "neutral", "unclear"],
        "vader_label":     ["positive", "positive", "negative", "neutral", "negative"],
        "textblob_label":  ["positive", "negative", "negative", "positive", "positive"],
        "ai_label":        ["positive", "negative", "unclear", "neutral", "neutral"],
    })
    table, confusion = etm.compare_methods(data, "test labels")
    by = table.set_index("method")

    assert (by["reference"] == "test labels").all()
    assert (by["comments"] == 4).all()  # the "unclear" comment is left out
    assert by.loc["VADER (project method)", "accuracy"] == 0.75
    assert by.loc["VADER (project method)", "negative_recall"] == 0.5  # caught 1 of the 2 complaints
    assert by.loc["TextBlob", "negative_recall"] == 1.0
    assert by.loc["AI review (SCRUM-28 spot check)", "accuracy"] == 0.75  # its "unclear" counts as a miss
    assert by.loc["Always positive (baseline)", "accuracy"] == 0.25
    assert by.loc["Always positive (baseline)", "cohen_kappa"] == 0  # no better than chance
    vader = confusion[confusion["method"] == "VADER (project method)"]
    assert vader["comments"].sum() == 4


def test_weekly_signals_keeps_full_weeks_and_needs_enough_comments():
    dates = pd.date_range("2026-01-08", "2026-01-25")  # Thursday to Sunday: only 12-18 and 19-25 are full weeks
    master = pd.DataFrame({"product_id": "P001", "date": dates.strftime("%Y-%m-%d"),
                           "search_interest": 10.0, "comments_published": 1})
    comments = pd.DataFrame({"product_id": "P001", "comment_id": [f"c{i}" for i in range(6)],
                             "published_at": ["2026-01-13T10:00:00Z"] * 5 + ["2026-01-20T10:00:00Z"],
                             "sentiment_compound": [0.5, 0.5, 0.5, 0.5, 0.5, -0.9]})
    weekly = etm.weekly_signals(master, comments)

    assert list(weekly["week"].dt.strftime("%Y-%m-%d")) == ["2026-01-12", "2026-01-19"]
    assert list(weekly["comments"]) == [7, 7]
    assert weekly["sentiment"].iloc[0] == 0.5  # 5 scored comments: enough
    assert np.isnan(weekly["sentiment"].iloc[1])  # 1 scored comment: too few


def test_lead_lag_finds_a_one_week_lead():
    rng = np.random.default_rng(0)
    rows = []
    for phone in ("P001", "P002", "P003"):
        comments = rng.integers(0, 50, size=30).cumsum()
        search = np.r_[comments[0], comments[:-1]]  # search this week = comments last week
        weeks = pd.date_range("2026-01-12", periods=30, freq="7D")
        rows.append(pd.DataFrame({"product_id": phone, "week": weeks, "search": search, "comments": comments,
                                  "sentiment": rng.normal(size=30)}))
    result = etm.lead_lag(pd.concat(rows, ignore_index=True))
    rho = result[result["signal"] == "comments_change"].set_index("lag_weeks")["spearman_rho"]

    assert rho.idxmax() == 1  # comments one week earlier match this week's search change
    assert rho[1] > 0.9 and abs(rho[0]) < 0.5
