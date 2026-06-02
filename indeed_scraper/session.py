import json
import logging
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from browserforge.fingerprints import Screen
from camoufox.async_api import AsyncNewBrowser
from playwright.async_api import async_playwright
from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path

from indeed_scraper.utils.auth import perform_login_flow

if TYPE_CHECKING:
    from indeed_scraper.utils.graphql import IndeedGraphQLClient
    from indeed_scraper.utils.temp_mail import TempMailManager

logger = logging.getLogger(__name__)

_ACCOUNTS_DIR = Path("accounts")


async def _load_valid_session(graphql_client: "IndeedGraphQLClient") -> dict | None:
    _ACCOUNTS_DIR.mkdir(exist_ok=True)
    for account_file in _ACCOUNTS_DIR.glob("*.json"):
        try:
            with open(account_file, encoding="utf-8") as f:
                session_data = json.load(f)
            email = session_data.get("email")
            logger.info(f"Testing session for {email}...")
            if await graphql_client.test_session(session_data):
                logger.info(f"Session for {email} is valid.")
                return session_data
            logger.warning(f"Session for {email} is invalid/expired.")
        except Exception as e:
            logger.warning(f"Failed to load session from {account_file.name}: {e}")
    return None


async def _create_session(
    mail_manager: "TempMailManager",
    captcha_api_key: str = "",
) -> dict:
    _ACCOUNTS_DIR.mkdir(exist_ok=True)
    user_data_dir = f"playfox_data/account_{secrets.token_hex(4)}"
    addon_path = get_addon_path()

    async with async_playwright() as p:
        logger.info(f"Launching Camoufox (user_data_dir={user_data_dir})...")
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
        )

        page = context.pages[0] if context.pages else await context.new_page()
        logger.info("Navigating to Indeed auth page...")
        await page.goto("https://secure.indeed.com/auth?hl=pl")

        session_data = await perform_login_flow(
            page, mail_manager, user_data_dir, captcha_api_key
        )

        email = session_data.get("email")
        if not email:
            raise Exception("No email in session after login.")

        account_file = _ACCOUNTS_DIR / f"{email}.json"
        with open(account_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Session saved to {account_file}")

        await context.close()
        return session_data


async def get_session(
    graphql_client: "IndeedGraphQLClient",
    mail_manager: "TempMailManager",
    force_login: bool = False,
    captcha_api_key: str = "",
) -> dict:
    if not force_login:
        session = await _load_valid_session(graphql_client)
        if session:
            return session
    logger.info("No valid session. Starting login flow...")
    return await _create_session(mail_manager, captcha_api_key)
