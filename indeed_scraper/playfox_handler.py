import logging
from typing import override

from camoufox.async_api import AsyncNewBrowser
from scrapy_playfox.handler import ScrapyPlaywrightDownloadHandler as PlayfoxHandler

logger = logging.getLogger("scrapy-playfox-custom")


class CustomPlayfoxDownloadHandler(PlayfoxHandler):
    @override
    async def _maybe_launch_browser(self, persistent: bool = False, user_data_dir: str = "") -> None:
        async with self.browser_launch_lock:
            if not hasattr(self, "browser"):
                logger.info("Launching browser %s (custom handler)", self.browser_type.name)

                kwargs = {}
                if persistent and user_data_dir:
                    kwargs["user_data_dir"] = user_data_dir

                self.browser = await AsyncNewBrowser(
                    playwright=self.playwright,
                    **self.config.launch_options,
                    persistent_context=persistent,
                    **kwargs
                )
                logger.info("Browser %s launched", self.browser_type.name)
                self.stats.inc_value("playwright/browser_count")
                self.browser.on("disconnected", self._browser_disconnected_callback)
