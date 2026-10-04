"""
One-off inspection script.
Fetches https://www.flashscore.com/odds/ directly (new URL) and audits every
CSS selector the current codebase depends on.

Run:
    python inspect_odds_page.py
    python inspect_odds_page.py --no-headless   # visible browser
    python inspect_odds_page.py --save-html     # also dumps full HTML to _inspect_odds.html
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

# Force UTF-8 on Windows consoles so unicode chars don't crash the script
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from bs4 import BeautifulSoup


ODDS_URL = "https://www.flashscore.com/odds/"

# Every selector the codebase currently uses, with a short label
SELECTORS = [
    # --- Navigation / tabs ---
    (".filters__tab",                                   "odds tab container (old home-page flow)"),
    (".filters__tab[data-analytics-alias='odds']",      "odds tab button (old home-page flow)"),
    ("[data-testid='wcl-dayPickerButton']",             "day picker current label"),
    ("[data-day-picker-arrow='next']",                  "day picker next arrow"),
    ("[data-day-picker-arrow='prev']",                  "day picker prev arrow"),

    # --- Odds page structure ---
    ("section.event.odds",                              "top-level odds section"),
    ("section.event.odds .sportName.soccer",            "soccer sport container inside odds"),
    (".event__odds",                                    "odds block on a match row"),
    (".headerLeague__wrapper",                          "league header wrapper"),
    (".headerLeague__title-text",                       "league title text"),
    (".headerLeague__category-text",                    "country/category text"),
    (".headerLeague__title",                            "league title element (title attr fallback)"),

    # --- Collapsible leagues ---
    ("[data-testid='wcl-accordionButton']",             "accordion expand/collapse button"),
    ("[data-testid='wcl-accordionButton'][aria-expanded='false']", "collapsed accordion button"),

    # --- Match row ---
    (".event__match",                                   "match row element"),
    ("a.eventRowLink",                                  "match link (href → URL)"),
    (".event__participant--home",                       "home team name"),
    (".event__participant--away",                       "away team name"),
    (".event__time",                                    "kick-off time"),
    (".event__stage--block",                            "live stage block (in-play)"),
    (".event__stage--pkv",                              "live stage pkv (in-play)"),

    # --- Score / odds cells ---
    ("[data-testid='wcl-matchRowScore']",               "score cell"),
    ("[data-testid='wcl-matchRowScore'][data-side='1']","home score side"),
    ("[data-testid='wcl-matchRowScore'][data-side='2']","away score side"),
    (".event__odds .odds__odd",                         "individual odds cell"),
    (".event__odd--odd1",                               "home win odds (1b)"),
    (".event__odd--odd2",                               "draw odds (Xb)"),
    (".event__odd--odd3",                               "away win odds (2b)"),

    # --- Overlays ---
    ("#onetrust-accept-btn-handler",                    "cookie accept button"),
    (".message__close",                                 "dismissable message close"),
    ("#live-table",                                     "live table (home page wait)"),
]


def create_driver(headless: bool = True):
    options = ChromeOptions()
    options.page_load_strategy = "eager"
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


def accept_cookies(driver) -> None:
    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.ID, "onetrust-accept-btn-handler"))
        )
        btns = driver.find_elements(By.ID, "onetrust-accept-btn-handler")
        if btns:
            driver.execute_script("arguments[0].click();", btns[0])
            time.sleep(0.5)
    except TimeoutException:
        pass


def wait_for_any(driver, selectors: list[str], timeout: int = 15) -> str | None:
    """Wait until any selector is present; return the one that matched."""
    wait = WebDriverWait(driver, timeout)
    for sel in selectors:
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            return sel
        except TimeoutException:
            continue
    return None


def audit_selectors(html: str) -> list[tuple[str, str, int]]:
    """Return list of (selector, label, count) for every selector."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for sel, label in SELECTORS:
        try:
            found = soup.select(sel)
            results.append((sel, label, len(found)))
        except Exception as e:
            results.append((sel, label, -1))
    return results


def sniff_new_classes(html: str) -> None:
    """Print interesting class names from the rendered page that might be new."""
    soup = BeautifulSoup(html, "html.parser")
    interesting: dict[str, int] = {}
    keywords = ("event", "odds", "header", "league", "match", "sport", "day", "picker",
                 "accordion", "participant", "score", "stage", "row", "wcl", "filters")
    for tag in soup.find_all(True):
        for cls in (tag.get("class") or []):
            if any(kw in cls.lower() for kw in keywords):
                interesting[cls] = interesting.get(cls, 0) + 1

    print("\n--- Interesting class names found on the page (top 60) ---")
    top = sorted(interesting.items(), key=lambda x: -x[1])[:60]
    for cls, count in top:
        print(f"  {count:4d}x  .{cls}")


def sniff_data_attrs(html: str) -> None:
    """Print data-testid and data-* attribute values present on the page."""
    soup = BeautifulSoup(html, "html.parser")
    testids: dict[str, int] = {}
    data_attrs: dict[str, int] = {}
    for tag in soup.find_all(True):
        tid = tag.get("data-testid")
        if tid:
            testids[str(tid)] = testids.get(str(tid), 0) + 1
        for attr, val in tag.attrs.items():
            if attr.startswith("data-") and attr != "data-testid":
                key = f'{attr}="{val}"'
                data_attrs[key] = data_attrs.get(key, 0) + 1

    print("\n--- data-testid values found ---")
    for tid, count in sorted(testids.items(), key=lambda x: -x[1])[:40]:
        print(f"  {count:4d}x  [data-testid='{tid}']")

    print("\n--- Other data-* attributes (top 40) ---")
    for attr, count in sorted(data_attrs.items(), key=lambda x: -x[1])[:40]:
        print(f"  {count:4d}x  [{attr}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--save-html", action="store_true",
                        help="Save full page HTML to _inspect_odds.html")
    parser.add_argument("--wait-secs", type=int, default=4,
                        help="Extra seconds to wait after page load (default: 4)")
    args = parser.parse_args(argv)

    print(f"Opening {ODDS_URL} ...")
    driver = create_driver(headless=not args.no_headless)
    try:
        driver.get(ODDS_URL)
        accept_cookies(driver)

        # Wait for the odds content to appear
        matched = wait_for_any(driver, [
            "section.event.odds",
            ".event__odds",
            ".headerLeague__wrapper",
            ".sportName.soccer",
            ".event__match",
            "body",  # final fallback
        ], timeout=20)
        print(f"  Waited for: {matched or 'body (fallback)'}")

        # Small grace period for JS to finish rendering
        time.sleep(args.wait_secs)

        html = driver.page_source
        current_url = driver.current_url
        page_title = driver.title
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\nFinal URL : {current_url}")
    print(f"Page title: {page_title}")
    print(f"HTML size : {len(html) // 1024} KB\n")

    # --- Selector audit ---
    results = audit_selectors(html)
    ok = [(s, l, c) for s, l, c in results if c > 0]
    missing = [(s, l, c) for s, l, c in results if c == 0]

    print(f"=== FOUND ({len(ok)}/{len(results)}) ===")
    for sel, label, count in ok:
        print(f"  [{count:3d}]  {label}")
        print(f"         {sel}")

    print(f"\n=== MISSING ({len(missing)}/{len(results)}) ===")
    for sel, label, _ in missing:
        print(f"  [ 0]  {label}")
        print(f"         {sel}")

    sniff_new_classes(html)
    sniff_data_attrs(html)

    if args.save_html:
        out = Path("_inspect_odds.html")
        out.write_text(html, encoding="utf-8")
        print(f"\nHTML saved to {out.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
