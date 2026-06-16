import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_TWOCAPTCHA_BASE = "https://2captcha.com"
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 120
_SUBMIT_RETRIES = 3


# ---------------------------------------------------------------------------
# Sitekey detection
# ---------------------------------------------------------------------------


async def _get_recaptcha_sitekey(page) -> str | None:
    try:
        return await page.evaluate("""
            () => {
                // 1. data-sitekey attribute
                const el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');

                // 2. reCAPTCHA iframe src
                for (const iframe of Array.from(document.querySelectorAll('iframe'))) {
                    const src = iframe.src || '';
                    if (src.includes('google.com/recaptcha') || src.includes('recaptcha.net')) {
                        const m = src.match(/[?&]k=([^&]+)/);
                        if (m) return decodeURIComponent(m[1]);
                    }
                }

                // 3. grecaptcha internal config
                try {
                    for (const key of Object.keys(___grecaptcha_cfg.clients || {})) {
                        const client = ___grecaptcha_cfg.clients[key];
                        for (const k of Object.keys(client || {})) {
                            const obj = client[k];
                            if (obj && obj.sitekey) return obj.sitekey;
                            if (obj && obj.l && obj.l.sitekey) return obj.l.sitekey;
                        }
                    }
                } catch (_) {}

                return null;
            }
        """)
    except Exception:
        return None


async def _get_hcaptcha_sitekey(page) -> str | None:
    try:
        return await page.evaluate("""
            () => {
                // 1. data-sitekey / data-hcaptcha-sitekey attribute
                const el = document.querySelector('.h-captcha[data-sitekey], [data-hcaptcha-sitekey]');
                if (el) return el.getAttribute('data-sitekey') || el.getAttribute('data-hcaptcha-sitekey');

                // 2. hCaptcha iframe src
                for (const iframe of Array.from(document.querySelectorAll('iframe'))) {
                    const src = iframe.src || '';
                    if (src.includes('hcaptcha.com')) {
                        const m = src.match(/sitekey=([^&]+)/);
                        if (m) return decodeURIComponent(m[1]);
                    }
                }

                return null;
            }
        """)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2captcha API
# ---------------------------------------------------------------------------


async def _submit_task(
    api_key: str, method: str, sitekey: str, page_url: str, enterprise: bool = False
) -> str | None:
    params: dict = {"key": api_key, "method": method, "pageurl": page_url, "json": 1}
    params["googlekey" if method == "userrecaptcha" else "sitekey"] = sitekey
    if enterprise:
        params["enterprise"] = 1

    for attempt in range(1, _SUBMIT_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{_TWOCAPTCHA_BASE}/in.php", data=params)
            data = resp.json()
            if data.get("status") == 1:
                return str(data["request"])
            logger.error(f"2captcha submit error (attempt {attempt}): {data}")
        except Exception as e:
            logger.error(f"2captcha submit exception (attempt {attempt}): {e}")

        if attempt < _SUBMIT_RETRIES:
            await asyncio.sleep(3)

    return None


async def _poll_result(api_key: str, task_id: str) -> str | None:
    params = {"key": api_key, "action": "get", "id": task_id, "json": 1}
    try:
        async with asyncio.timeout(_POLL_TIMEOUT), httpx.AsyncClient(timeout=10) as client:
            while True:
                await asyncio.sleep(_POLL_INTERVAL)
                try:
                    resp = await client.get(f"{_TWOCAPTCHA_BASE}/res.php", params=params)
                    data = resp.json()
                    if data.get("status") == 1:
                        return data["request"]
                    if data.get("request") != "CAPCHA_NOT_READY":  # 2captcha API typo, intentional
                        logger.error(f"2captcha unexpected poll response: {data}")
                        return None
                except Exception as e:
                    logger.warning(f"2captcha poll request failed: {e}")
    except TimeoutError:
        logger.error("2captcha polling timed out after %s seconds.", _POLL_TIMEOUT)
        return None


# ---------------------------------------------------------------------------
# Token injection
# ---------------------------------------------------------------------------


async def _inject_recaptcha(page, token: str) -> None:
    await page.evaluate(
        """
        (token) => {
            // Fill hidden textarea fields
            document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {
                el.value = token; el.innerHTML = token;
            });
            document.querySelectorAll('[name="g-recaptcha-response-enterprise"]').forEach(el => {
                el.value = token; el.innerHTML = token;
            });

            // Trigger standard grecaptcha callbacks
            const triggerCallbacks = (clients) => {
                for (const key of Object.keys(clients || {})) {
                    const client = clients[key];
                    for (const k of Object.keys(client || {})) {
                        const obj = client[k];
                        if (obj && typeof obj.callback === 'function') try { obj.callback(token); } catch (_) {}
                        if (obj && obj.l && typeof obj.l.callback === 'function') try { obj.l.callback(token); } catch (_) {}
                    }
                }
            };

            try { triggerCallbacks(___grecaptcha_cfg.clients); } catch (_) {}

            // Enterprise clients
            try {
                const cfg = window.___grecaptcha_cfg || {};
                triggerCallbacks((cfg.enterprise || {}).clients);
            } catch (_) {}
        }
        """,
        token,
    )


async def _inject_hcaptcha(page, token: str) -> None:
    await page.evaluate(
        """
        (token) => {
            // Fill response textarea
            const ta = document.querySelector('[name="h-captcha-response"]');
            if (ta) ta.value = token;

            // Also fill g-recaptcha-response if present (some sites mirror it)
            const gr = document.querySelector('[name="g-recaptcha-response"]');
            if (gr) gr.value = token;

            // Trigger hCaptcha callback registered in widget config
            try {
                for (const widgetKey of Object.keys(window.hcaptcha ? window.hcaptcha._hmt || {} : {})) {
                    const widget = window.hcaptcha._hmt[widgetKey];
                    if (widget && typeof widget.callback === 'function') {
                        try { widget.callback(token); } catch (_) {}
                    }
                }
            } catch (_) {}

            // Fallback: dispatch a custom event that some sites listen for
            document.dispatchEvent(new CustomEvent('hcaptchaSuccess', { detail: { token } }));
        }
        """,
        token,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def solve_page_captcha(page, api_key: str) -> bool:
    """
    Detect and solve any reCAPTCHA v2 or hCaptcha on *page* using 2captcha.

    Returns True if no captcha was found or it was solved successfully.
    Returns False if solving failed.
    Raises RuntimeError if a captcha is detected but no API key is configured.
    """
    page_url = page.url

    # --- reCAPTCHA v2 ---
    sitekey = await _get_recaptcha_sitekey(page)
    if sitekey:
        if not api_key:
            raise RuntimeError("reCAPTCHA v2 detected but no 2captcha API key configured.")

        is_enterprise = False
        try:
            is_enterprise = await page.evaluate(
                "() => !!(window.grecaptcha && window.grecaptcha.enterprise)"
            )
        except Exception:
            pass

        logger.info(
            "reCAPTCHA v2 detected (sitekey=%s, enterprise=%s), submitting to 2captcha...",
            sitekey,
            is_enterprise,
        )
        task_id = await _submit_task(api_key, "userrecaptcha", sitekey, page_url, enterprise=is_enterprise)
        if not task_id:
            return False
        token = await _poll_result(api_key, task_id)
        if not token:
            return False
        await _inject_recaptcha(page, token)
        logger.info("reCAPTCHA token injected successfully.")
        return True

    # --- hCaptcha ---
    sitekey = await _get_hcaptcha_sitekey(page)
    if sitekey:
        if not api_key:
            raise RuntimeError("hCaptcha detected but no 2captcha API key configured.")

        logger.info("hCaptcha detected (sitekey=%s), submitting to 2captcha...", sitekey)
        task_id = await _submit_task(api_key, "hcaptcha", sitekey, page_url)
        if not task_id:
            return False
        token = await _poll_result(api_key, task_id)
        if not token:
            return False
        await _inject_hcaptcha(page, token)
        logger.info("hCaptcha token injected successfully.")
        return True

    # No captcha found
    return True
