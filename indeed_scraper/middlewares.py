import json
import logging
import os
import secrets
from pathlib import Path

from browserforge.fingerprints import Screen
from camoufox.async_api import AsyncNewBrowser
from playwright.async_api import async_playwright
from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path
from scrapy.http import HtmlResponse
from scrapy.exceptions import IgnoreRequest

logger = logging.getLogger(__name__)


class IndeedSessionMiddleware:
    def __init__(self):
        self.accounts_dir = Path("accounts")

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    async def process_request(self, request, spider):
        if not request.meta.get("requires_auth"):
            return None

        self.accounts_dir.mkdir(parents=True, exist_ok=True)

        force_login = getattr(spider, "force_login", False) or request.meta.get("force_login", False)

        account_files = [] if force_login else list(self.accounts_dir.glob("*.json"))

        spider.logger.info(
            f"IndeedSessionMiddleware: Found {len(account_files)} cached account(s) (force_login={force_login})."
        )

        for account_file in account_files:
            try:
                with open(account_file, encoding="utf-8") as f:
                    session_data = json.load(f)

                email = session_data.get("email")
                spider.logger.info(f"IndeedSessionMiddleware: Testing session for {email}...")

                is_valid = await spider.graphql_client.test_session(session_data)
                if is_valid:
                    spider.logger.info(f"IndeedSessionMiddleware: Session for {email} is VALID.")
                    response = HtmlResponse(
                        url=request.url,
                        status=200,
                        headers=request.headers,
                        body=b"",
                        encoding="utf-8",
                        request=request,
                    )
                    response.meta["indeed_session"] = session_data
                    return response
                else:
                    spider.logger.warning(f"IndeedSessionMiddleware: Session for {email} is INVALID/EXPIRED.")
            except Exception as e:
                spider.logger.warning(
                    f"IndeedSessionMiddleware: Failed to load/test session from {account_file.name}: {e}"
                )

        spider.logger.info("IndeedSessionMiddleware: No working session. Launching browser...")
        try:
            account_id = f"account_{secrets.token_hex(4)}"
            user_data_dir = f"playfox_data/{account_id}"
            addon_path = get_addon_path()

            async with async_playwright() as p:
                spider.logger.info(f"IndeedSessionMiddleware: Launching Camoufox (user_data_dir={user_data_dir})...")
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
                    config={'forceScopeAccess': True},
                )

                page = context.pages[0] if context.pages else await context.new_page()

                spider.logger.info("IndeedSessionMiddleware: Navigating to Indeed auth page...")
                await page.goto("https://secure.indeed.com/auth?hl=pl")

                session_data = await spider.perform_login_flow(page, user_data_dir=user_data_dir)

                email = session_data.get("email")
                if not email:
                    raise Exception("No email found in session data.")

                account_file = self.accounts_dir / f"{email}.json"
                with open(account_file, "w", encoding="utf-8") as f:
                    json.dump(session_data, f, ensure_ascii=False, indent=4)
                spider.logger.info(f"IndeedSessionMiddleware: Session saved to {account_file}")

                await context.close()

                response = HtmlResponse(
                    url=request.url,
                    status=200,
                    headers=request.headers,
                    body=b"",
                    encoding="utf-8",
                    request=request,
                )
                response.meta["indeed_session"] = session_data
                return response

        except Exception as e:
            spider.logger.error(f"IndeedSessionMiddleware: Critical error during auth: {e}")
            raise IgnoreRequest(f"Failed to authenticate: {e}")

    async def process_response(self, request, response, spider):
        if not request.meta.get("requires_auth"):
            return response

        if response.status in (401, 403, 999):
            spider.logger.warning(
                f"IndeedSessionMiddleware: Received status {response.status}. Session may be invalid."
            )

            session_data = request.meta.get("indeed_session")
            if session_data:
                email = session_data.get("email")
                if email:
                    account_file = self.accounts_dir / f"{email}.json"
                    if account_file.exists():
                        try:
                            account_file.unlink()
                            spider.logger.info(f"IndeedSessionMiddleware: Deleted invalid session: {account_file}")
                        except Exception as e:
                            spider.logger.error(f"IndeedSessionMiddleware: Failed to delete session: {e}")

            spider.logger.error("IndeedSessionMiddleware: Session failed. Not retrying.")
            raise IgnoreRequest("Session invalid, aborting.")

        return response
