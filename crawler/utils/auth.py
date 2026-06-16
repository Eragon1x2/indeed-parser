import asyncio
import json
import logging
import os
import random
import secrets
import string
from pathlib import Path
from typing import TYPE_CHECKING

from crawler.utils.captcha_solver import solve_page_captcha

if TYPE_CHECKING:
    from playwright.async_api import Page
    from crawler.utils.temp_mail import TempMailManager

logger = logging.getLogger(__name__)

_AUTH_URL = "https://secure.indeed.com/auth?hl=pl"
_EMAIL_XPATH = '//input[@name="__email"]'
_EMAIL_SUBMIT_XPATH = '//button[@type="submit" and @data-tn-element="auth-page-email-submit-button"]'
_OTP_INPUT_ID = "passcode-input"
_OTP_SUBMIT_XPATH = '//button[@data-tn-element="otp-verify-login-submit-button"]'

_REJECTION_PHRASES = (
    # Polish
    "tymczasow", "jednorazow", "niedozwolon", "prawidłowy adres",
    "nie są obsługiwan", "nieprawidłow", "użyj innego",
    # English
    "temporary", "disposable", "not supported", "not allowed",
    "invalid email", "use a different", "use another",
)
_SECURITY_PHRASES = (
    "kontrola bezpieczeństwa", "security check", "nie została ukończona",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_device_id() -> str:
    return "".join(secrets.choice(string.digits + string.ascii_lowercase) for _ in range(16))


async def _get_body_text(page: "Page") -> str:
    return (await page.evaluate("document.body ? document.body.innerText : ''")).lower()


async def _otp_is_visible(page: "Page") -> bool:
    el = await page.query_selector(f"#{_OTP_INPUT_ID}")
    return bool(el and await el.is_visible())


async def _extract_cookies(page: "Page") -> tuple[dict, str, str | None]:
    cookies = await page.context.cookies()
    ctk = next((c["value"] for c in cookies if c["name"].upper() == "CTK"), None)
    if not ctk:
        ctk = await page.evaluate(
            "window.oneHostContext ? window.oneHostContext.ctk : null"
        )
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return {c["name"]: c["value"] for c in cookies}, cookie_str, ctk


# ---------------------------------------------------------------------------
# Email acceptance flow
# ---------------------------------------------------------------------------


async def _wait_for_captcha_or_otp(page: "Page") -> None:
    """Poll up to 10 seconds for captcha or OTP to appear after email submit."""
    for _ in range(20):
        has_captcha = await page.evaluate("""
            () => {
                if (document.querySelector('[data-sitekey], .g-recaptcha, .h-captcha')) return true;
                for (const f of document.querySelectorAll('iframe')) {
                    const s = f.src || '';
                    if (s.includes('google.com/recaptcha') || s.includes('recaptcha.net') || s.includes('hcaptcha.com'))
                        return true;
                }
                return false;
            }
        """)
        if has_captcha:
            logger.info("CAPTCHA detected.")
            return
        if await _otp_is_visible(page):
            logger.info("OTP input appeared without captcha.")
            return
        await asyncio.sleep(0.5)


async def _wait_for_callback_or_submit(page: "Page", submit_xpath: str) -> None:
    """After captcha token injected: wait for callback auto-submit, else click manually."""
    for _ in range(6):
        if await _otp_is_visible(page):
            logger.info("OTP visible — form submitted by CAPTCHA callback.")
            return
        body = await _get_body_text(page)
        if any(p in body for p in _SECURITY_PHRASES):
            return
        await page.wait_for_timeout(500)

    logger.info("Clicking submit after CAPTCHA solve...")
    await page.locator(submit_xpath).evaluate("el => el.click()")
    await page.wait_for_timeout(random.randint(2000, 4000))  # noqa: S311


async def _email_was_accepted(page: "Page") -> bool:
    """
    Wait up to 35s for OTP screen or rejection message.
    Returns True if OTP appeared, False if rejected or timed out.
    """
    rejection_js = " || ".join(f'h.includes("{p}")' for p in _REJECTION_PHRASES)
    security_js  = " || ".join(f'h.includes("{p}")' for p in _SECURITY_PHRASES)

    condition = f"""() => {{
        const otp = document.getElementById('{_OTP_INPUT_ID}');
        if (otp && otp.offsetParent !== null) return true;
        const h = document.body ? document.body.innerText.toLowerCase() : '';
        if ({security_js}) return true;
        return {rejection_js};
    }}"""

    timed_out = False
    try:
        await page.wait_for_function(condition, timeout=35000)
    except Exception:
        timed_out = True
        logger.warning("Timed out waiting for OTP or rejection message.")

    if timed_out:
        return await _otp_is_visible(page)

    body = await _get_body_text(page)
    if any(p in body for p in _SECURITY_PHRASES):
        logger.warning("Indeed security check triggered.")
        return False

    return await _otp_is_visible(page)


async def _get_accepted_email(
    page: "Page",
    mail_manager: "TempMailManager",
    captcha_api_key: str,
    max_attempts: int = 10,
) -> tuple[str, str]:
    spent: list[str] = []

    for attempt in range(max_attempts):
        # Reload page on retry
        if attempt > 0:
            logger.info("Reloading auth page before retry...")
            await page.goto(_AUTH_URL)
            await page.wait_for_selector(_EMAIL_XPATH, timeout=30000)

        # Pick next provider
        try:
            email, token = await mail_manager.create_new_email(spent)
        except Exception:
            logger.error("All email providers exhausted.")
            break

        provider = token.split(":")[0]
        logger.info(f"Trying email: {email}  [{provider}]")

        await page.locator(_EMAIL_XPATH).fill(email)
        await page.locator(_EMAIL_SUBMIT_XPATH).evaluate("el => el.click()")

        await _wait_for_captcha_or_otp(page)

        captcha_solved = await solve_page_captcha(page, captcha_api_key)
        if not captcha_solved:
            logger.error(f"CAPTCHA unsolvable for [{provider}] — skipping.")
            spent.append(provider)
            continue

        if captcha_api_key:
            await _wait_for_callback_or_submit(page, _EMAIL_SUBMIT_XPATH)

        if not await _email_was_accepted(page):
            logger.warning(f"Email {email} rejected by Indeed — [{provider}] blacklisted.")
            spent.append(provider)
            await page.locator(_EMAIL_XPATH).fill("")
            continue

        return email, token

    raise RuntimeError("All email providers rejected by Indeed.")


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


async def perform_login_flow(
    page: "Page",
    mail_manager: "TempMailManager",
    user_data_dir: str | None = None,
    captcha_api_key: str = "",
) -> dict:
    await page.wait_for_selector(_EMAIL_XPATH, timeout=60000)

    # Dismiss cookie banner
    cookie_btn = page.locator("#onetrust-accept-btn-handler")
    if await cookie_btn.is_visible():
        await cookie_btn.click(timeout=5000)
        logger.info("Cookie banner dismissed.")

    await page.locator(_EMAIL_XPATH).click()
    await page.wait_for_timeout(random.randint(5000, 10000))  # noqa: S311

    email, token = await _get_accepted_email(page, mail_manager, captcha_api_key)

    code = await mail_manager.wait_for_otp_code(token)
    if not code:
        raise RuntimeError("Failed to receive OTP from email provider.")

    logger.info("Entering OTP code...")
    await page.wait_for_selector(f"#{_OTP_INPUT_ID}", timeout=60000)
    await page.locator(f"#{_OTP_INPUT_ID}").fill(code)
    await page.wait_for_timeout(random.randint(2000, 5000))  # noqa: S311

    logger.info("Submitting OTP...")
    submit = page.locator(_OTP_SUBMIT_XPATH)
    await submit.wait_for(state="visible", timeout=15000)
    await submit.evaluate("el => el.click()")
    await page.wait_for_timeout(10000)

    cookies_dict, cookie_string, ctk = await _extract_cookies(page)
    if not ctk:
        logger.warning("CTK cookie not found — session may not work correctly.")

    return {
        "email": email,
        "cookies": cookies_dict,
        "cookie_string": cookie_string,
        "ctk": ctk or "",
        "device_id": _generate_device_id(),
        "user_data_dir": user_data_dir or f"playfox_data/account_{email.split('@')[0]}",
    }


# ---------------------------------------------------------------------------
# Account creation entry point
# ---------------------------------------------------------------------------


async def create_new_account(accounts_dir: Path, spider_logger: logging.Logger) -> dict | None:
    from browserforge.fingerprints import Screen
    from camoufox.async_api import AsyncNewBrowser
    from playwright.async_api import async_playwright
    from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path
    from config import settings
    from crawler.utils.temp_mail import (
        TempMailManager,
        MailTmProvider,
        MailGwProvider,
        GuerrillaMailProvider,
        MailnesiaProvider,
    )

    spider_logger.info("Initializing new account login flow via Playwright...")
    try:
        _CRAWLER_DIR = Path(__file__).parent.parent
        user_data_dir = str(_CRAWLER_DIR / "playfox_data" / f"account_{secrets.token_hex(4)}")
        addon_path = get_addon_path()
        proxy_args = {"proxy": {"server": str(settings.scraper.proxy)}} if settings.scraper.proxy else {}

        async with async_playwright() as p:
            context = await AsyncNewBrowser(
                playwright=p,
                headless=False,
                humanize=True,
                screen=Screen(max_width=1280, max_height=800),
                geoip=False,
                disable_coop=True,
                persistent_context=True,
                user_data_dir=user_data_dir,
                main_world_eval=True,
                addons=[os.path.abspath(addon_path)],
                i_know_what_im_doing=True,
                config={"forceScopeAccess": True},
                **proxy_args,
            )

            page = context.pages[0] if context.pages else await context.new_page()
            spider_logger.info("Navigating to Indeed auth page...")
            await page.goto(_AUTH_URL)

            mail_manager = TempMailManager([
                MailTmProvider(),
                MailGwProvider(),
                GuerrillaMailProvider(),
                GuerrillaMailProvider(),  # second instance picks a different random domain
                MailnesiaProvider(),
            ])

            session_data = await perform_login_flow(page, mail_manager, user_data_dir, settings.captcha_api_key)

            email = session_data["email"]
            account_file = accounts_dir / f"{email}.json"
            account_file.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
            spider_logger.info(f"Saved session to {account_file}")

            await context.close()
            return session_data

    except Exception:
        spider_logger.exception("Failed to create new account")
        return None
