"""Report-only figures: end-to-end workflow diagram and KPI scenario analysis."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

NAVY, TEAL, ORANGE, GREY = "#1F3864", "#2E75B6", "#C55A11", "#595959"

# Hypothetical planning values (INR) per scenario: value of an anticipated surge-day, cost per alert actioned.
SCENARIOS = {"Worst": (10_000, 6_000), "Base": (25_000, 4_000), "Best": (50_000, 2_000)}


def box(ax, x, y, w, h, title, body, color):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc="white", ec=color, lw=1.8))
    ax.add_patch(FancyBboxPatch((x, y + h - 0.075), w, 0.075, boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc=color, ec=color, lw=1.8))
    ax.text(x + w / 2, y + h - 0.0375, title, ha="center", va="center", color="white", fontsize=9.5, weight="bold")
    ax.text(x + 0.012, y + h - 0.095, body, ha="left", va="top", fontsize=7.6, color="#222222", linespacing=1.35)


def arrow(ax, p, q, color=GREY):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=14, lw=1.4, color=color))


def workflow():
    fig, ax = plt.subplots(figsize=(13, 6.2))
    ax.set_xlim(-0.015, 1.015)
    ax.set_ylim(0, 1)
    ax.axis("off")
    box(ax, 0.005, 0.50, 0.15, 0.42, "Data sources",
        "Google Trends (India,\ndaily, 269 days)\n\nYouTube Data API v3\n(videos, statistics,\ntop-level comments)\n\nProduct registry v3\n(60 smartphones,\n13 brands, 3 cohorts)", NAVY)
    box(ax, 0.19, 0.50, 0.17, 0.42, "Unit 1: Data & EDA",
        "Collection + quota logs\nCleaning (dedup, null≠0,\nactive query version)\nTemporal alignment\nMaster dataset\n16,140 rows × 43 cols\n16 validation checks\nEDA by cohort / brand", TEAL)
    box(ax, 0.40, 0.50, 0.17, 0.42, "Unit 2: Features & Model",
        "Lags t-1,2,3,7; rolling\nmean/std (shift(1))\nOne-hot brand / cohort\nScaler fit on train only\nSurge label (85th pct)\nChronological 80/20 split\nNext-day surge classifier", TEAL)
    box(ax, 0.61, 0.71, 0.17, 0.25, "Unit 3 · Method 1",
        "Time-series forecasting\n10 models: naive, ARIMA,\nETS, Ridge, RF, GBR,\nLightGBM (53-day test)", ORANGE)
    box(ax, 0.61, 0.40, 0.17, 0.27, "Unit 3 · Method 2",
        "Text mining: 29,189\ncomments, VADER\nsentiment, LDA/NMF topics,\nreference-label check,\nweekly lead-lag test", ORANGE)
    box(ax, 0.82, 0.50, 0.175, 0.42, "Decision support",
        "Streamlit dashboard\n(6 pages, global filters)\nOverview · Viral Radar\nSearch Trends · Forecasts\nAudience Signals\nText Mining\n\nKPI scenarios & actions", NAVY)
    for p, q in [((0.155, 0.71), (0.19, 0.71)), ((0.36, 0.71), (0.40, 0.71)),
                 ((0.57, 0.80), (0.61, 0.83)), ((0.57, 0.60), (0.61, 0.55)),
                 ((0.78, 0.83), (0.82, 0.78)), ((0.78, 0.55), (0.82, 0.62))]:
        arrow(ax, p, q)
    ax.add_patch(FancyBboxPatch((0.19, 0.12), 0.805, 0.17, boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc="#F2F2F2", ec=GREY, lw=1.2, ls="--"))
    ax.text(0.5925, 0.245, "Orchestration & quality gates: src/run_full_pipeline.py", ha="center",
            fontsize=10, weight="bold", color=NAVY)
    ax.text(0.5925, 0.175, "10-stage DAG with input/output contracts · 6 cross-unit consistency checks · "
            "12-point Method 2 leakage audit · 222 automated pytest tests", ha="center", fontsize=8.6, color="#222222")
    for x in (0.275, 0.485, 0.695, 0.9075):
        arrow(ax, (x, 0.29), (x, 0.40 if x == 0.695 else 0.50), color="#A6A6A6")
    ax.text(0.005, 0.04, "Solid arrows: data flow. Dashed panel: automated validation applied to every stage.",
            fontsize=8, color=GREY, style="italic")
    fig.tight_layout()
    fig.savefig(OUT / "fig_workflow.png", dpi=200)
    plt.close(fig)


def scenarios():
    pred = pd.read_csv(OUT / "unit2_test_predictions.csv")
    y, p, chosen = pred["surge_label"].to_numpy(), pred["probability"].to_numpy(), pred["threshold"].iloc[0]
    thresholds = np.linspace(0.05, 0.95, 91)
    rows = []
    for t in thresholds:
        alert = p >= t
        tp, alerts = int((alert & (y == 1)).sum()), int(alert.sum())
        for name, (value, cost) in SCENARIOS.items():
            rows.append({"threshold": t, "scenario": name, "true_alerts": tp, "alerts": alerts,
                         "net_inr_lakh": (tp * value - alerts * cost) / 1e5})
    df = pd.DataFrame(rows)
    at = df[np.isclose(df["threshold"], thresholds[np.argmin(abs(thresholds - chosen))])]
    summary = []
    alert = p >= chosen
    tp, alerts = int((alert & (y == 1)).sum()), int(alert.sum())
    for name, (value, cost) in SCENARIOS.items():
        summary.append({"scenario": name, "value_per_anticipated_surge_day_inr": value, "cost_per_alert_inr": cost,
                        "true_alerts": tp, "alerts": alerts, "benefit_inr": tp * value, "cost_inr": alerts * cost,
                        "net_inr": tp * value - alerts * cost})
    pd.DataFrame(summary).to_csv(OUT / "scenario_summary.csv", index=False)

    fig, ax = plt.subplots(figsize=(10, 4.4))
    colors = {"Worst": "#C00000", "Base": NAVY, "Best": "#548235"}
    for name, g in df.groupby("scenario"):
        ax.plot(g["threshold"], g["net_inr_lakh"], label=f"{name} case", color=colors[name], lw=2)
    ax.axvline(chosen, color=GREY, ls="--", lw=1)
    ax.text(chosen + 0.01, ax.get_ylim()[0] * 0.75, f"selected threshold\n({chosen:.2f}, from train CV)", fontsize=8, color=GREY)
    ax.axhline(0, color="black", lw=0.8)
    ax.set(xlabel="Alert threshold on predicted surge probability (Random Forest)",
           ylabel="Net impact over 53-day test window\n(INR lakh, hypothetical)",
           title="Scenario analysis: net value of next-day surge alerts across 60 SKUs")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig_scenarios.png", dpi=200)
    plt.close(fig)
    print(pd.DataFrame(summary).to_string())


def topics():
    """Top terms per topic from the current text_topics_terms.csv (LDA and NMF, k=3)."""
    terms = pd.read_csv(ROOT / "data" / "processed" / "text_topics_terms.csv")
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.4))
    for row, method in enumerate(("lda", "nmf")):
        sub = terms[terms.method == method]
        for col, (topic, g) in enumerate(sub.groupby("topic")):
            ax = axes[row, col]
            g = g.sort_values("rank").iloc[::-1]
            ax.barh(g["term"], g["weight"], color=NAVY if method == "lda" else ORANGE)
            share = g["topic_mean_share"].iloc[0]
            label = g["label_suggestion"].iloc[0] if method == "lda" else "comparison fit"
            ax.set_title(f"{method.upper()} topic {topic}: {label}\n"
                         f"{share * 100:.1f}% of comment text", fontsize=9.5)
            ax.xaxis.set_major_locator(plt.MaxNLocator(4))
            ax.tick_params(labelsize=8)
            ax.grid(axis="x", alpha=0.3)
    fig.suptitle("Top-10 terms per topic (k = 3; brand/model tokens and domain stop-words removed)", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "fig_topics.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    workflow()
    topics()
    scenarios()
