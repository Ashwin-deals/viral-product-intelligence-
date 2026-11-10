"""Capture dashboard screenshots for the report (needs the app running on localhost:8517)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "outputs" / "dashboard"
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:8517"


def settle(page):
    page.wait_for_timeout(1500)
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60000)
    page.wait_for_timeout(2500)


def go(page, title):
    page.locator('[data-testid="stSidebarNav"] a', has_text=title).first.click()
    settle(page)


def pick(page, label, option):
    box = page.locator('[data-testid="stMultiSelect"]', has_text=label).first
    box.locator("input").click()
    box.locator("input").fill(option)
    page.wait_for_timeout(800)
    page.keyboard.press("Enter")
    page.keyboard.press("Escape")
    settle(page)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=False)
    page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
    page.goto(URL)
    page.wait_for_selector('[data-testid="stSidebarNav"]', timeout=90000)
    settle(page)
    page.screenshot(path=OUT / "01_overview_all.png")
    for title, name in [("Viral Radar", "02_radar_all"), ("Forecasts", "03_forecasts_all"),
                        ("Text Mining", "04_text_mining_all")]:
        go(page, title)
        page.screenshot(path=OUT / f"{name}.png")
    pick(page, "Brand", "Samsung")
    pick(page, "Trend cohort", "RISING")
    for title, name in [("Overview", "05_overview_filtered"), ("Search Trends", "06_trends_filtered"),
                        ("Forecasts", "07_forecasts_filtered"), ("Audience Signals", "08_signals_filtered")]:
        go(page, title)
        page.screenshot(path=OUT / f"{name}.png")
    browser.close()
print("saved to", OUT)
