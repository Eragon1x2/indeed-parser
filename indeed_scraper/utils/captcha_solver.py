import asyncio
import logging
import time

import requests

logger = logging.getLogger(__name__)

_TWOCAPTCHA_BASE = "https://2captcha.com"


async def _get_recaptcha_sitekey(page) -> str | None:
    try:
        return await page.evaluate("""
            () => {
                // 1. data-sitekey on any element
                const el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');

                // 2. reCAPTCHA iframes (anchor / bframe)
                for (const iframe of Array.from(document.querySelectorAll('iframe'))) {
                    const src = iframe.src || '';
                    if (src.includes('google.com/recaptcha') || src.includes('recaptcha.net')) {
                        const m = src.match(/[?&]k=([^&]+)/);
                        if (m) return decodeURIComponent(m[1]);
                    }
                }

                // 3. grecaptcha internal config (JS-rendered, no visible data-sitekey)
                try {
                    for (const key of Object.keys(___grecaptcha_cfg.clients || {})) {
                        const client = ___grecaptcha_cfg.clients[key];
                        for (const k of Object.keys(client || {})) {
                            const obj = client[k];
                            if (obj && obj.sitekey) return obj.sitekey;
                            if (obj && obj.l && obj.l.sitekey) return obj.l.sitekey;
                        }
                    }
                } catch(_) {}

                return null;
            }
        """)
    except Exception:
        return None


async def _get_hcaptcha_sitekey(page) -> str | None:
    try:
        return await page.evaluate("""
            () => {
                const el = document.querySelector(
                    '.h-captcha[data-sitekey], [data-hcaptcha-sitekey]'
                );
                if (el) {
                    return (
                        el.getAttribute('data-sitekey') ||
                        el.getAttribute('data-hcaptcha-sitekey')
                    );
                }
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


def _submit_task(api_key: str, method: str, sitekey: str, page_url: str) -> str | None:
    params: dict = {"key": api_key, "method": method, "pageurl": page_url, "json": 1}
    if method == "userrecaptcha":
        params["googlekey"] = sitekey
    else:
        params["sitekey"] = sitekey
    try:
        resp = requests.post(f"{_TWOCAPTCHA_BASE}/in.php", data=params, timeout=15)
        data = resp.json()
        if data.get("status") == 1:
            return str(data["request"])
        logger.error(f"2captcha submit error: {data}")
        return None
    except Exception as e:
        logger.error(f"2captcha submit failed: {e}")
        return None


def _poll_result(api_key: str, task_id: str, timeout: int = 120) -> str | None:
    params = {"key": api_key, "action": "get", "id": task_id, "json": 1}
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            resp = requests.get(f"{_TWOCAPTCHA_BASE}/res.php", params=params, timeout=10)
            data = resp.json()
            if data.get("status") == 1:
                return data["request"]
            if data.get("request") != "CAPCHA_NOT_READY":
                logger.error(f"2captcha poll error: {data}")
                return None
        except Exception as e:
            logger.warning(f"2captcha poll error: {e}")
    logger.error("2captcha polling timed out.")
    return None


async def _inject_recaptcha(page, token: str) -> None:
    await page.evaluate(
        """
        (token) => {
            document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {
                el.value = token;
                el.innerHTML = token;
            });
            try {
                for (const key of Object.keys(___grecaptcha_cfg.clients || {})) {
                    const client = ___grecaptcha_cfg.clients[key];
                    for (const k of Object.keys(client || {})) {
                        const obj = client[k];
                        if (obj && typeof obj.callback === 'function') {
                            try { obj.callback(token); } catch(_) {}
                        }
                        if (obj && obj.l && typeof obj.l.callback === 'function') {
                            try { obj.l.callback(token); } catch(_) {}
                        }
                    }
                }
            } catch(_) {}
        }
        """,
        token,
    )


async def _inject_hcaptcha(page, token: str) -> None:
    await page.evaluate(
        """
        (token) => {
            const ta = document.querySelector('[name="h-captcha-response"]');
            if (ta) ta.value = token;
            try {
                if (typeof hcaptcha !== 'undefined') hcaptcha.execute();
            } catch(e) {}
        }
        """,
        token,
    )


async def _wait_for_manual_solve(page, timeout: int = 300) -> None:
    logger.warning("Solve the CAPTCHA manually in the browser window (timeout: %ds).", timeout)
    for _ in range(timeout // 2):
        await asyncio.sleep(2)
        solved = await page.evaluate("""
            () => {
                try {
                    const ta = document.querySelector('[name="g-recaptcha-response"]');
                    if (ta && ta.value && ta.value.length > 0) return true;
                } catch(_) {}
                try {
                    const ta = document.querySelector('[name="h-captcha-response"]');
                    if (ta && ta.value && ta.value.length > 0) return true;
                } catch(_) {}
                return false;
            }
        """)
        if solved:
            logger.info("CAPTCHA solved manually.")
            return
    logger.warning("Manual CAPTCHA solve timed out.")


async def solve_page_captcha(page, api_key: str) -> bool:
    """
    Detects reCAPTCHA v2 or hCaptcha and solves it.
    With api_key: automatic via 2captcha.
    Without api_key: waits for manual solve in the open browser window.
    Returns True if nothing to solve or solved successfully, False on failure.
    """
    page_url = page.url

    sitekey = await _get_recaptcha_sitekey(page)
    if sitekey:
        if not api_key:
            await _wait_for_manual_solve(page)
            return True
        logger.info(f"reCAPTCHA v2 detected (sitekey={sitekey}), solving via 2captcha...")
        task_id = await asyncio.to_thread(
            _submit_task, api_key, "userrecaptcha", sitekey, page_url
        )
        if not task_id:
            return False
        token = await asyncio.to_thread(_poll_result, api_key, task_id)
        if not token:
            return False
        await _inject_recaptcha(page, token)
        logger.info("reCAPTCHA token injected.")
        return True

    sitekey = await _get_hcaptcha_sitekey(page)
    if sitekey:
        if not api_key:
            await _wait_for_manual_solve(page)
            return True
        logger.info(f"hCaptcha detected (sitekey={sitekey}), solving via 2captcha...")
        task_id = await asyncio.to_thread(
            _submit_task, api_key, "hcaptcha", sitekey, page_url
        )
        if not task_id:
            return False
        token = await asyncio.to_thread(_poll_result, api_key, task_id)
        if not token:
            return False
        await _inject_hcaptcha(page, token)
        logger.info("hCaptcha token injected.")
        return True

    return True
