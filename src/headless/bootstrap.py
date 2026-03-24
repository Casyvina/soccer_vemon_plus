from __future__ import annotations

from typing import Any


def extract_selenium_session(driver: Any) -> dict[str, Any]:
    cookies: list[dict] = []
    user_agent = ""

    if driver is None:
        return {"cookies": cookies, "user_agent": user_agent}

    try:
        cookies = driver.get_cookies() or []
    except Exception:
        cookies = []

    try:
        user_agent = str(driver.execute_script("return navigator.userAgent") or "")
    except Exception:
        user_agent = ""

    return {
        "cookies": cookies,
        "user_agent": user_agent,
    }


def apply_selenium_session_to_client(driver: Any, client) -> dict[str, Any]:
    session_data = extract_selenium_session(driver)
    cookies = session_data.get("cookies") or []
    applied = client.apply_selenium_cookies(cookies)

    user_agent = str(session_data.get("user_agent") or "").strip()
    if user_agent:
        client.set_user_agent(user_agent)

    return {
        "cookies_found": len(cookies),
        "cookies_applied": applied,
        "user_agent": user_agent,
    }
