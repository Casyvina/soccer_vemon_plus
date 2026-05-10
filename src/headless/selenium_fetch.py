from __future__ import annotations

import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from headless.routes import MatchRouteSet


class SeleniumPageSourceFetcher:
    def __init__(
        self,
        config=None,
        browser_name: str | None = None,
        headless: bool = True,
        timeout_seconds: int = 15,
    ):
        self.config = config
        self.browser_name = self._normalize_browser_name(
            browser_name
            or self._safe_get("browser", "default", "FireFox")
        )
        self.headless = bool(headless)
        self.timeout_seconds = max(5, int(timeout_seconds))
        self._driver = None

    def open(self):
        if self._driver is None:
            self._driver = self._create_driver()
        return self

    def close(self) -> None:
        driver = self._driver
        self._driver = None
        if driver is None:
            return
        try:
            driver.quit()
        except Exception:
            pass

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def fetch_pages(self, routes: MatchRouteSet) -> dict[str, str]:
        return self.fetch_urls(
            [
                ("match", routes.match_url),
                ("h2h_overall", routes.h2h_overall_url),
                ("standings_overall", routes.standings_overall_url),
                ("standings_home", routes.standings_home_url),
                ("standings_away", routes.standings_away_url),
            ]
        )

    def fetch_url(self, url: str, key: str = "generic") -> str:
        pages = self.fetch_urls([(key, url)])
        return str(pages.get(key) or "")

    def fetch_urls(self, items: list[tuple[str, str]], batch_size: int = 3) -> dict[str, str]:
        driver = self._driver
        owns_driver = driver is None
        if driver is None:
            driver = self._create_driver()
        try:
            pages: dict[str, str] = {}
            item_list = list(items)
            for batch_start in range(0, len(item_list), batch_size):
                batch = item_list[batch_start: batch_start + batch_size]
                print(f"  Batch loading {[k for k, _ in batch]}")
                handles: list[str] = []

                # Open all tabs in the batch simultaneously
                for i, (key, url) in enumerate(batch):
                    if i == 0:
                        try:
                            driver.get(url)
                        except TimeoutException:
                            pass
                        handles.append(driver.current_window_handle)
                    else:
                        driver.execute_script("window.open('');")
                        driver.switch_to.window(driver.window_handles[-1])
                        try:
                            driver.get(url)
                        except TimeoutException:
                            pass
                        handles.append(driver.current_window_handle)

                # Collect results from each tab
                for (key, url), handle in zip(batch, handles):
                    driver.switch_to.window(handle)
                    self._dismiss_cookie_overlay(driver)
                    self._wait_for_page(driver, key)
                    html = driver.page_source
                    pages[key] = html
                    print(f"  Done [{key}] — {len(html) // 1024} KB")

                # Close extra tabs, keep only the first
                for handle in handles[1:]:
                    try:
                        driver.switch_to.window(handle)
                        driver.close()
                    except Exception:
                        pass
                driver.switch_to.window(handles[0])

            return pages
        finally:
            if owns_driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _wait_for_page(self, driver, key: str) -> None:
        # Summary pages load halftime data asynchronously — give them extra time
        effective_timeout = (
            max(self.timeout_seconds, 20)
            if key == "summary" or key.startswith("summary")
            else self.timeout_seconds
        )
        wait = WebDriverWait(driver, effective_timeout)
        selectors: dict[str, tuple[str, str]] = {
            "match": (
                By.XPATH,
                (
                    "//div[contains(@class,'duelParticipant__home')]"
                    "//div[contains(@class,'participant__participantName')]//a"
                ),
            ),
            "h2h_overall": (
                By.CSS_SELECTOR,
                ".h2h__section .rows a.h2h__row",
            ),
            "standings_overall": (
                By.CSS_SELECTOR,
                ".ui-table__body .ui-table__row",
            ),
            "standings_home": (
                By.CSS_SELECTOR,
                ".ui-table__body .ui-table__row",
            ),
            "standings_away": (
                By.CSS_SELECTOR,
                ".ui-table__body .ui-table__row",
            ),
            "summary": (
                By.CSS_SELECTOR,
                ".smv__participantRow, .wclHeaderSection--summary",
            ),
        }

        # Keys like "summary_last_<mid>" and "summary_h2h_<mid>" share the summary wait
        lookup = key if key in selectors else ("summary" if key.startswith("summary") else None)
        selector = selectors.get(lookup) if lookup else None
        if not selector:
            return

        try:
            wait.until(EC.presence_of_element_located(selector))
        except TimeoutException:
            # Keep the page_source for inspection even when a page did not fully render.
            return

    def fetch_summary_pages(self, items: list[tuple[str, str]]) -> dict[str, str]:
        """Fetch match summary HTML — opens all URLs in parallel tabs, then clicks Summary on each.

        items: list of (key, match_url) pairs — use the main match URL, not a sub-route.
        Returns {key: page_source_html}.
        """
        driver = self._driver
        owns_driver = driver is None
        if driver is None:
            driver = self._create_driver()
        try:
            pages: dict[str, str] = {}
            item_list = list(items)
            if not item_list:
                return pages

            # Phase 1: open all match URLs in parallel tabs
            handles: list[tuple[str, str]] = []
            for i, (key, match_url) in enumerate(item_list):
                if i == 0:
                    try:
                        driver.get(match_url)
                    except TimeoutException:
                        pass
                    handles.append((key, driver.current_window_handle))
                else:
                    driver.execute_script("window.open('');")
                    driver.switch_to.window(driver.window_handles[-1])
                    try:
                        driver.get(match_url)
                    except TimeoutException:
                        pass
                    handles.append((key, driver.current_window_handle))

            # Phase 2: click Summary tab on each loaded tab sequentially
            for key, handle in handles:
                driver.switch_to.window(handle)
                self._dismiss_cookie_overlay(driver)
                html = self._click_summary_and_capture(driver)
                pages[key] = html
                print(f"  Done [summary {key}] — {len(html) // 1024} KB")

            # Cleanup extra tabs
            for _, handle in handles[1:]:
                try:
                    driver.switch_to.window(handle)
                    driver.close()
                except Exception:
                    pass
            if handles:
                driver.switch_to.window(handles[0][1])

            return pages
        finally:
            if owns_driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _click_summary_and_capture(self, driver) -> str:
        """On an already-loaded match page, click Summary tab and return page source."""
        wait = WebDriverWait(driver, self.timeout_seconds)
        try:
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".detailOver [data-testid='wcl-tabs']")
            ))
        except TimeoutException:
            return driver.page_source
        self._click_tab_by_text(
            driver,
            ".detailOver [data-testid='wcl-tabs'] button[data-testid='wcl-tab']",
            "summary",
        )
        time.sleep(0.5)
        self._click_tab_by_text(
            driver,
            "div[data-testid='wcl-tabs'][data-type='secondary'] button[data-testid='wcl-tab']",
            "summary",
            required=False,
        )
        try:
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR,
                 ".smv__participantRow, .wclHeaderSection--summary, "
                 "div.tabContent__match-summary")
            ))
        except TimeoutException:
            pass
        return driver.page_source

    def _fetch_summary_tab(self, driver, match_url: str) -> str:
        """Navigate to match_url, click Summary primary then secondary tab, return page source."""
        try:
            try:
                driver.get(match_url)
            except TimeoutException:
                pass
            self._dismiss_cookie_overlay(driver)

            wait = WebDriverWait(driver, self.timeout_seconds)

            # Wait for primary tabs container
            try:
                wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".detailOver [data-testid='wcl-tabs']")
                ))
            except TimeoutException:
                return driver.page_source

            # Click primary "Summary" tab
            self._click_tab_by_text(
                driver,
                ".detailOver [data-testid='wcl-tabs'] button[data-testid='wcl-tab']",
                "summary",
            )

            time.sleep(0.8)

            # Click secondary "Summary" tab inside tabContent__match-summary (if present)
            self._click_tab_by_text(
                driver,
                "div[data-testid='wcl-tabs'][data-type='secondary'] button[data-testid='wcl-tab']",
                "summary",
                required=False,
            )

            # Wait for actual summary content to appear
            try:
                wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                     ".smv__participantRow, .wclHeaderSection--summary, "
                     "div.tabContent__match-summary")
                ))
            except TimeoutException:
                pass

            return driver.page_source
        except Exception:
            try:
                return driver.page_source
            except Exception:
                return ""

    def _click_tab_by_text(
        self, driver, selector: str, text: str, required: bool = True
    ) -> bool:
        """Find a tab button with matching text and click it. Returns True if clicked."""
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, selector)
            for btn in buttons:
                if btn.text.strip().lower() == text.lower():
                    driver.execute_script("arguments[0].click();", btn)
                    # Wait briefly for it to become active
                    try:
                        WebDriverWait(driver, 5).until(
                            lambda d: any(
                                b.text.strip().lower() == text.lower() and (
                                    b.get_attribute("data-selected") == "true"
                                    or b.get_attribute("aria-selected") == "true"
                                    or "active" in (b.get_attribute("class") or "").lower()
                                    or "selected" in (b.get_attribute("class") or "").lower()
                                )
                                for b in d.find_elements(By.CSS_SELECTOR, selector)
                            )
                        )
                    except TimeoutException:
                        pass
                    return True
            return False
        except Exception:
            return False

    def _dismiss_cookie_overlay(self, driver) -> None:
        try:
            buttons = driver.find_elements(By.ID, "onetrust-accept-btn-handler")
            if buttons:
                driver.execute_script("arguments[0].click();", buttons[0])
        except Exception:
            pass

    def _create_driver(self):
        browser = self.browser_name
        if browser == "chrome":
            return self._create_chrome_driver()
        if browser == "edge":
            return self._create_edge_driver()
        return self._create_firefox_driver()

    def _create_chrome_driver(self):
        options = ChromeOptions()
        options.page_load_strategy = "eager"
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        ext_path = self._safe_get("browser_drivers", "chrome_extension", "")
        if ext_path:
            options.add_argument(f"--disable-extensions-except={ext_path}")
            options.add_argument(f"--load-extension={ext_path}")

        driver_path = self._safe_get("browser_drivers", "chrome", "")
        service = ChromeService(driver_path) if driver_path else None
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(30)
        return driver

    def _create_firefox_driver(self):
        options = FirefoxOptions()
        options.page_load_strategy = "eager"
        if self.headless:
            options.add_argument("--headless")
        options.set_preference("permissions.default.image", 2)
        options.set_preference("dom.webdriver.enabled", False)
        options.set_preference("network.prefetch-next", False)
        options.set_preference("network.dns.disablePrefetch", True)
        options.set_preference(
            "general.useragent.override",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) "
            "Gecko/20100101 Firefox/131.0",
        )

        driver_path = self._safe_get("browser_drivers", "firefox", "")
        service = FirefoxService(driver_path) if driver_path else None
        driver = webdriver.Firefox(service=service, options=options)
        driver.set_page_load_timeout(30)
        return driver

    def _create_edge_driver(self):
        options = EdgeOptions()
        options.page_load_strategy = "eager"
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        driver_path = self._safe_get("browser_drivers", "edge", "")
        service = EdgeService(driver_path) if driver_path else None
        driver = webdriver.Edge(service=service, options=options)
        driver.set_page_load_timeout(30)
        return driver

    def _safe_get(self, section: str, key: str, default=None):
        try:
            return self.config.get(section, key, default=default)
        except Exception:
            return default

    @staticmethod
    def _normalize_browser_name(value: str) -> str:
        text = str(value or "").strip().lower()
        if text in {"firefox", "firefoxesr"}:
            return "firefox"
        if text in {"fire fox"}:
            return "firefox"
        if text == "brave":
            return "chrome"
        return text or "firefox"
