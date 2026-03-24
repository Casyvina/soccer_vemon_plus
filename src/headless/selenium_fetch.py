from __future__ import annotations

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
        timeout_seconds: int = 20,
    ):
        self.config = config
        self.browser_name = self._normalize_browser_name(
            browser_name
            or self._safe_get("browser", "default", "FireFox")
        )
        self.headless = bool(headless)
        self.timeout_seconds = max(5, int(timeout_seconds))

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

    def fetch_urls(self, items: list[tuple[str, str]]) -> dict[str, str]:
        driver = self._create_driver()
        try:
            pages: dict[str, str] = {}
            for key, url in items:
                driver.get(url)
                self._dismiss_cookie_overlay(driver)
                self._wait_for_page(driver, key)
                pages[key] = driver.page_source
            return pages
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def _wait_for_page(self, driver, key: str) -> None:
        wait = WebDriverWait(driver, self.timeout_seconds)
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
        }

        selector = selectors.get(key)
        if not selector:
            return

        try:
            wait.until(EC.presence_of_element_located(selector))
        except TimeoutException:
            # Keep the page_source for inspection even when a page did not fully render.
            return

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

        driver_path = self._safe_get("browser_drivers", "chrome", "")
        service = ChromeService(driver_path) if driver_path else None
        return webdriver.Chrome(service=service, options=options)

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
        return webdriver.Firefox(service=service, options=options)

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
        return webdriver.Edge(service=service, options=options)

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
