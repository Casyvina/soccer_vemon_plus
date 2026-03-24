from __future__ import annotations

import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from headless.selenium_fetch import SeleniumPageSourceFetcher


class SeleniumLeaguePageFetcher(SeleniumPageSourceFetcher):
    def fetch_pages(self, results_url: str, fixtures_url: str | None = None) -> dict[str, str]:
        items = [("results", results_url)]
        if fixtures_url:
            items.append(("fixtures", fixtures_url))
        return self.fetch_urls(items)

    def fetch_urls(self, items: list[tuple[str, str]]) -> dict[str, str]:
        driver = self._create_driver()
        try:
            pages: dict[str, str] = {}
            for key, url in items:
                driver.get(url)
                self._dismiss_cookie_overlay(driver)
                self._wait_for_league_page(driver)
                self._expand_all_matches(driver)
                pages[key] = driver.page_source
            return pages
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def _wait_for_league_page(self, driver) -> None:
        wait = WebDriverWait(driver, self.timeout_seconds)
        selectors = [
            (By.CSS_SELECTOR, ".event__match"),
            (By.CSS_SELECTOR, ".event__round"),
            (By.CSS_SELECTOR, ".heading"),
            (By.TAG_NAME, "body"),
        ]

        for selector in selectors:
            try:
                wait.until(EC.presence_of_element_located(selector))
                return
            except TimeoutException:
                continue

    def _expand_all_matches(self, driver) -> None:
        wait = WebDriverWait(driver, min(self.timeout_seconds, 12))

        for _ in range(200):
            try:
                current_count = len(driver.find_elements(By.CLASS_NAME, "event__match"))
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.8)

                load_more = wait.until(
                    EC.element_to_be_clickable(
                        (
                            By.XPATH,
                            "//a[.//span[contains(normalize-space(.), 'Show more')]]",
                        )
                    )
                )
                driver.execute_script("arguments[0].click();", load_more)

                wait.until(
                    lambda d: len(d.find_elements(By.CLASS_NAME, "event__match"))
                    > current_count
                )
                time.sleep(0.4)
            except TimeoutException:
                break
            except Exception:
                break
