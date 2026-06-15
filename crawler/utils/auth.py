import logging
import random
import secrets
import string
from pathlib import Path
from typing import TYPE_CHECKING

from crawler.utils.captcha_solver import solve_page_captcha
from crawler.utils.cf_solver import custom_hybrid_solve_cloudflare

if TYPE_CHECKING:
    from playwright.async_api import Page

    from crawler.utils.temp_mail import TempMailManager

logger = logging.getLogger(__name__)

_EMAIL_XPATH = '//input[@name="__email"]'
_EMAIL_SUBMIT_XPATH = (
    '//button[@type="submit" and @data-tn-element="auth-page-email-submit-button"]'
)
_REJECTION_PHRASES = (
    "Tymczasowe adresy e-mail",
    "Temporary email addresses are not supported",
    "temporary email",
)


def _generate_device_id() -> str:
    chars = string.digits + string.ascii_lowercase
    return "".join(secrets.choice(chars) for _ in range(16))


async def _extract_cookies(page: "Page") -> tuple[dict, str, str | None]:
    cookies = await page.context.cookies()
    ctk_value = next((c["value"] for c in cookies if c["name"].upper() == "CTK"), None)
    if not ctk_value:
        try:
            ctk_value = await page.evaluate(
                "window.oneHostContext ? window.oneHostContext.ctk : null"
            )
        except Exception as e:
            logger.debug(f"CTK JS eval failed: {e}")
    cookie_string = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return {c["name"]: c["value"] for c in cookies}, cookie_string, ctk_value


async def _await_email_result(page: "Page") -> bool:
    """Wait for either OTP screen or rejection message. Returns True if accepted."""
    rejection_js = " || ".join(f'h.includes("{p}")' for p in _REJECTION_PHRASES)
    try:
        await page.wait_for_function(
            f"""() => {{
                const otp = document.getElementById('passcode-input');
                if (otp && otp.offsetParent !== null) return true;
                const h = document.body ? document.body.innerHTML : '';
                return {rejection_js};
            }}""",
            timeout=20000,
        )
    except Exception:
        logger.warning("Email submit result unknown (timeout waiting for OTP or error).")
        return True

    try:
        otp = await page.query_selector("#passcode-input")
        if otp and await otp.is_visible():
            return True
    except Exception as e:
        logger.debug(f"OTP element check failed: {e}")
    return False


async def _get_accepted_email(
    page: "Page",
    mail_manager: "TempMailManager",
    captcha_api_key: str,
) -> tuple[str, str]:
    rejected_providers: list[str] = []
    for _ in range(3):
        email, token = await mail_manager.create_new_email(rejected_providers)
        logger.info(f"Trying email: {email}")

        await page.locator(_EMAIL_XPATH).fill(email)
        await page.locator(_EMAIL_SUBMIT_XPATH).click()

        if not await solve_page_captcha(page, captcha_api_key):
            logger.error("Image CAPTCHA detected but could not be solved.")
        elif captcha_api_key:
            await page.wait_for_timeout(random.randint(2000, 4000))  # noqa: S311

        accepted = await _await_email_result(page)
        if not accepted:
            provider_name = token.split(":")[0]
            rejected_providers.append(provider_name)
            logger.warning(
                f"Email {email} rejected by Indeed ({provider_name} blacklisted)."
            )
            await page.locator(_EMAIL_XPATH).fill("")
            continue

        return email, token

    raise Exception("All email providers rejected by Indeed.")


async def perform_login_flow(
    page: "Page",
    mail_manager: "TempMailManager",
    user_data_dir: str | None = None,
    captcha_api_key: str = "",
) -> dict:
    page_title = await page.title()
    if "Security Check" in page_title:
        is_solved = await custom_hybrid_solve_cloudflare(
            page=page,
            challenge_type="interstitial",
            expected_content_selector='input[name="__email"]',
        )
        if not is_solved:
            logger.error("Failed to bypass Cloudflare interstitial.")

    await page.wait_for_selector(_EMAIL_XPATH, timeout=60000)
    await page.locator(_EMAIL_XPATH).click()
    await page.wait_for_timeout(random.randint(5000, 10000))  # noqa: S311

    email, token = await _get_accepted_email(page, mail_manager, captcha_api_key)

    code = await mail_manager.wait_for_otp_code(token)
    if not code:
        raise Exception("Failed to receive OTP from temporary email.")

    logger.info("Filling OTP passcode...")
    passcode_xpath = '//input[@id="passcode-input"]'
    await page.wait_for_selector(passcode_xpath, timeout=60000)
    await page.locator(passcode_xpath).click()
    await page.locator(passcode_xpath).fill(code)
    await page.wait_for_timeout(random.randint(2000, 5000))  # noqa: S311

    turnstile_solved = await custom_hybrid_solve_cloudflare(
        page=page, challenge_type="turnstile", solve_attempts=3
    )
    if not turnstile_solved:
        logger.warning("Turnstile not resolved. Login may fail.")

    logger.info("Clicking login submit button...")
    submit_locator = page.locator(
        '//button[@data-tn-element="otp-verify-login-submit-button"]'
    )
    try:
        await submit_locator.wait_for(state="enabled", timeout=15000)
    except Exception as e:
        logger.debug(f"Submit button not enabled within timeout: {e}")
    await submit_locator.click()
    await page.wait_for_timeout(10000)

    cookies_dict, cookie_string, ctk_value = await _extract_cookies(page)
    if not ctk_value:
        logger.warning(
            "CTK not found in cookies or JS context; session may not work correctly."
        )

    return {
        "email": email,
        "cookies": cookies_dict,
        "cookie_string": cookie_string,
        "ctk": ctk_value or "",
        "device_id": _generate_device_id(),
        "user_data_dir": user_data_dir or f"playfox_data/account_{email.split('@')[0]}",
    }


async def test_session(session_data: dict) -> bool:
    import httpx
    from config import settings

    query = """
    query JobSearch {
      jobSearch(what: "test", limit: 1) {
        results { job { key } }
      }
    }
    """
    headers = {
        "Host": "apis.indeed.com",
        "indeed-api-key": "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8",
        "indeed-ctk": session_data.get("ctk", ""),
        "Accept": "application/json",
        "indeed-locale": "pl-PL",
        "indeed-client-sub-app": "rnviewjob-ios",
        "Accept-Language": "pl",
        "User-Agent": "Indeed Jobs/41274 CFNetwork/3860.300.31 Darwin/25.2.0",
        "Indeed-App-Info": "appv=310.0; appid=com.indeed.jobsearch; osv=26.2; os=ios; dtype=tablet",
        "indeed-co": "PL",
        "Indeed-Device-ID": session_data.get("device_id", ""),
        "Indeed-BDT": "7RJszkwn9Hz32YdO2u3BUCPeHlc12bk4o6tXAD26Na+K0IBFK9p/ijT7F/ahDlUBkYNONBtY93EpmUWDO/QvYN92BTVH7lCWkQp2yxE6W8zE0mVI10eCEe5xV36ZAxiJ95FI2vNB6Xm2u4g=",
        "Content-Type": "application/json",
        "Cookie": session_data.get("cookie_string", ""),
    }
    proxy = str(settings.scraper.proxy) if settings.scraper.proxy else None
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=15) as client:
            res = await client.post(
                "https://apis.indeed.com/graphql",
                json={"query": query.strip()},
                headers=headers,
            )
            res.raise_for_status()
            data = res.json()
            for err in data.get("errors") or []:
                if any(
                    kw in err.get("message", "").lower()
                    for kw in ["auth", "permission", "unauthorized", "expired", "invalid"]
                ):
                    return False
            return True
    except Exception as e:
        logger.warning(f"Session test failed: {e}")
        return False


async def create_new_account(accounts_dir: Path, spider_logger: logging.Logger) -> dict | None:
    import json
    import os
    from browserforge.fingerprints import Screen
    from camoufox.async_api import AsyncNewBrowser
    from playwright.async_api import async_playwright
    from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path
    from config import settings
    from crawler.utils.temp_mail import TempMailManager

    spider_logger.info("Initializing new account login flow via Playwright...")
    try:
        _CRAWLER_DIR = Path(__file__).parent.parent
        user_data_dir = str(
            _CRAWLER_DIR / "playfox_data" / f"account_{secrets.token_hex(4)}"
        )
        addon_path = get_addon_path()

        proxy_args = {}
        if settings.scraper.proxy:
            proxy_args["proxy"] = {"server": str(settings.scraper.proxy)}

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
            await page.goto("https://secure.indeed.com/auth?hl=pl")

            mail_manager = TempMailManager()
            session_data = await perform_login_flow(
                page, mail_manager, user_data_dir, settings.captcha_api_key
            )

            email = session_data.get("email")
            if not email:
                raise Exception("No email in session data.")

            account_file = accounts_dir / f"{email}.json"
            with open(account_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            spider_logger.info(f"Saved session to {account_file}")

            await context.close()
            return session_data
    except Exception as e:
        spider_logger.error(f"Failed to create new account: {e}")
        return None

