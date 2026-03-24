from __future__ import annotations

import time
from dataclasses import dataclass

from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from headless.selenium_fetch import SeleniumPageSourceFetcher


@dataclass
class OddsPageFetchResult:
    day_offset: int
    page_url: str
    selected_day_label: str
    html: str


class SeleniumOddsPageFetcher(SeleniumPageSourceFetcher):
    def fetch_day_page(self, day_offset: int) -> OddsPageFetchResult:
        pages = self.fetch_day_pages([day_offset])
        return pages[int(day_offset)]

    def fetch_day_pages(self, day_offsets: list[int]) -> dict[int, OddsPageFetchResult]:
        offsets = self._normalize_day_offsets(day_offsets)
        driver = self._create_driver()
        try:
            self._open_home(driver)
            current_offset = 0
            results: dict[int, OddsPageFetchResult] = {}

            for offset in offsets:
                self._move_to_day_offset(
                    driver,
                    current_offset=current_offset,
                    target_offset=offset,
                )
                current_offset = offset
                self._expand_all_collapsed_leagues(driver)
                results[offset] = OddsPageFetchResult(
                    day_offset=offset,
                    page_url=str(driver.current_url or "").strip(),
                    selected_day_label=self._selected_day_label(driver),
                    html=driver.page_source,
                )

            return results
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def _open_home(self, driver) -> None:
        self._load_url_with_retries(
            driver,
            self._safe_get("core", "default_url", "https://www.flashscore.com/"),
        )
        self._dismiss_cookie_overlay(driver)
        self._dismiss_message(driver)
        self._wait_for_filters(driver)
        self._activate_odds_tab(driver)

    def _wait_for_filters(self, driver) -> None:
        wait = WebDriverWait(driver, self.timeout_seconds)
        selectors = [
            (By.CSS_SELECTOR, ".filters__tab"),
            (By.CSS_SELECTOR, "#live-table"),
            (By.TAG_NAME, "body"),
        ]

        for selector in selectors:
            try:
                wait.until(EC.presence_of_element_located(selector))
                return
            except TimeoutException:
                continue

    def _activate_odds_tab(self, driver) -> None:
        wait = WebDriverWait(driver, self.timeout_seconds)
        odds_tab = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".filters__tab[data-analytics-alias='odds']")
            )
        )
        if "selected" not in str(odds_tab.get_attribute("class") or ""):
            driver.execute_script("arguments[0].click();", odds_tab)

        wait.until(
            lambda d: "selected"
            in str(
                d.find_element(
                    By.CSS_SELECTOR,
                    ".filters__tab[data-analytics-alias='odds']",
                ).get_attribute("class")
                or ""
            )
        )
        self._wait_for_odds_content(driver)

    def _wait_for_odds_content(self, driver) -> None:
        wait = WebDriverWait(driver, self.timeout_seconds)
        selectors = [
            (By.CSS_SELECTOR, "section.event.odds"),
            (By.CSS_SELECTOR, ".event__odds"),
            (By.CSS_SELECTOR, ".headerLeague__wrapper"),
        ]

        for selector in selectors:
            try:
                wait.until(EC.presence_of_element_located(selector))
                return
            except TimeoutException:
                continue

    def _move_to_day_offset(
        self,
        driver,
        *,
        current_offset: int,
        target_offset: int,
    ) -> None:
        delta = int(target_offset) - int(current_offset)
        if delta == 0:
            self._wait_for_odds_content(driver)
            return

        direction = "next" if delta > 0 else "prev"
        for _ in range(abs(delta)):
            previous_label = self._selected_day_label(driver)
            button = WebDriverWait(driver, self.timeout_seconds).until(
                EC.element_to_be_clickable(
                    (
                        By.CSS_SELECTOR,
                        f"[data-day-picker-arrow='{direction}']",
                    )
                )
            )
            driver.execute_script("arguments[0].click();", button)
            WebDriverWait(driver, self.timeout_seconds).until(
                lambda d: self._selected_day_label(d) != previous_label
            )
            self._wait_for_odds_content(driver)
            self._dismiss_message(driver)

    def _expand_all_collapsed_leagues(self, driver) -> None:
        for _ in range(40):
            collapsed = [
                button
                for button in driver.find_elements(
                    By.CSS_SELECTOR,
                    "section.event.odds [data-testid='wcl-accordionButton'][aria-expanded='false']",
                )
                if self._is_displayed(button)
            ]
            if not collapsed:
                return

            clicked = 0
            for button in collapsed:
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        button,
                    )
                    driver.execute_script("arguments[0].click();", button)
                    clicked += 1
                    time.sleep(0.05)
                except Exception:
                    continue

            if not clicked:
                return

            time.sleep(0.2)

    def _dismiss_message(self, driver) -> None:
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, ".message__close")
            for button in buttons[:1]:
                driver.execute_script("arguments[0].click();", button)
        except Exception:
            pass

    def _load_url_with_retries(self, driver, url: str, attempts: int = 3) -> None:
        last_error = None
        for attempt in range(max(1, int(attempts))):
            try:
                driver.get(url)
                return
            except WebDriverException as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise
                time.sleep(2.0)

        if last_error is not None:
            raise last_error

    @staticmethod
    def _selected_day_label(driver) -> str:
        try:
            return str(
                driver.find_element(
                    By.CSS_SELECTOR,
                    "[data-testid='wcl-dayPickerButton']",
                ).text
                or ""
            ).strip()
        except Exception:
            return ""

    @staticmethod
    def _is_displayed(element) -> bool:
        try:
            return bool(element.is_displayed())
        except (StaleElementReferenceException, Exception):
            return False

    @staticmethod
    def _normalize_day_offsets(day_offsets: list[int]) -> list[int]:
        if not day_offsets:
            return [0]

        normalized: list[int] = []
        seen: set[int] = set()
        for value in day_offsets:
            offset = int(value)
            if offset < 0 or offset > 5:
                raise ValueError("Day offsets must be between 0 and 5.")
            if offset in seen:
                continue
            seen.add(offset)
            normalized.append(offset)
        return sorted(normalized)
