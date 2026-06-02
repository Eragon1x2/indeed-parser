import asyncio
import logging
import random
from typing import Union, Literal

from playwright.async_api import Page, ElementHandle, Frame, TimeoutError as PlaywrightTimeoutError

from playwright_captcha.solvers.click.common.shadow_root import search_shadow_root_iframes, search_shadow_root_elements
from playwright_captcha.solvers.click.cloudflare.utils.dom_helpers import get_ready_checkbox
from playwright_captcha.types import FrameworkType

logger = logging.getLogger("camoufox_captcha.cloudflare")


async def custom_detect_cloudflare_challenge(
        queryable: Union[Page, Frame, ElementHandle],
) -> bool:
    try:
        element = await queryable.query_selector('script[src*="/cdn-cgi/challenge-platform/"]')
        return element is not None
    except Exception:
        return False


async def custom_hybrid_solve_cloudflare(
        page: Page,
        challenge_type: Literal["interstitial", "turnstile"] = "interstitial",
        expected_content_selector: str | None = None,
        solve_attempts: int = 10,
        wait_checkbox_delay: int = 6,
        wait_checkbox_attempts: int = 10,
) -> bool:
    logger.info(f"Starting hybrid Cloudflare {challenge_type} solver...")

    if challenge_type == "turnstile":
        for _ in range(8):
            await asyncio.sleep(1)
            try:
                token = await page.evaluate(
                    "() => { const el = document.querySelector('[name=\"cf-turnstile-response\"]'); "
                    "return el ? el.value : null; }"
                )
                if token:
                    logger.info("Turnstile auto-solved by browser.")
                    return True
            except Exception:
                pass

    for attempt in range(solve_attempts):
        if attempt > 0:
            await asyncio.sleep(5)
            logger.warning(f"Retrying hybrid solve ({attempt + 1}/{solve_attempts})...")

        if expected_content_selector:
            try:
                element = await page.query_selector(expected_content_selector)
                if element and await element.is_visible():
                    logger.info(f"Expected content '{expected_content_selector}' visible, no challenge needed.")
                    return True
            except Exception:
                pass

        is_challenge = await custom_detect_cloudflare_challenge(page)
        if not is_challenge:
            logger.info("Cloudflare script not detected on page.")
            return True

        try:
            cf_iframes = await search_shadow_root_iframes(
                framework=FrameworkType.CAMOUFOX,
                captcha_container=page,
                src_filter='/cdn-cgi/challenge-platform/'
            )
        except Exception as e:
            logger.error(f"Error searching for iframes: {e}")
            continue

        if not cf_iframes:
            logger.error("Cloudflare iframes not found in shadow DOM.")
            continue

        try:
            checkbox_data = await get_ready_checkbox(
                framework=FrameworkType.CAMOUFOX,
                iframes=cf_iframes,
                delay=wait_checkbox_delay,
                attempts=wait_checkbox_attempts,
            )
        except Exception as e:
            logger.error(f"Error getting checkbox: {e}")
            continue

        if not checkbox_data:
            logger.error("Cloudflare checkbox not found or not ready.")
            continue

        iframe, checkbox = checkbox_data
        logger.info("Found checkbox inside closed shadow DOM iframe.")

        await asyncio.sleep(random.uniform(1.5, 3.5))

        try:
            iframe_element = await iframe.frame_element()
            await iframe_element.scroll_into_view_if_needed()

            box = await checkbox.bounding_box()
            if box:
                # bounding_box() already returns page-relative coords
                x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
                y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
                logger.info(f"Physical mouse click at ({x:.2f}, {y:.2f})")
                await page.mouse.move(x, y, steps=random.randint(10, 20))
                await asyncio.sleep(random.uniform(0.3, 0.7))
                await page.mouse.click(x, y)
                logger.info("Physical click performed.")
            else:
                logger.warning("No bounding box, falling back to virtual click.")
                await checkbox.click()
        except Exception as e:
            logger.error(f"Failed to perform click: {e}")
            continue

        if challenge_type == "turnstile":
            logger.info("Waiting for Turnstile success element...")
            success = False
            for _ in range(15):
                await asyncio.sleep(1)
                try:
                    success_elements = await search_shadow_root_elements(FrameworkType.CAMOUFOX, iframe, 'div[id="success"]')
                    if success_element := next(iter(success_elements), None):
                        try:
                            await success_element.wait_for_element_state("visible", timeout=1000)
                            logger.info("Turnstile solved successfully.")
                            success = True
                            break
                        except PlaywrightTimeoutError:
                            pass
                except Exception:
                    pass
            if success:
                return True
        else:
            logger.info(f"Waiting for redirect or '{expected_content_selector}'...")
            for _ in range(15):
                await asyncio.sleep(1)
                try:
                    if expected_content_selector:
                        element = await page.query_selector(expected_content_selector)
                        if element and await element.is_visible():
                            logger.info(f"Element '{expected_content_selector}' visible, bypass successful.")
                            return True
                    else:
                        return True
                except Exception:
                    pass

        logger.warning("Failed to confirm bypass on this attempt.")

    logger.error("Max solving attempts reached.")
    return False
