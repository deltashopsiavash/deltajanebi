"""TLS 1.2 transport for the Velora SanaShop site ONLY.

On a specific connection (ger1 -> veloraaccessorie.ir), TCP/443 works but
TLS 1.3 ClientHello times out. Confirmed with curl:
  curl -4 https://veloraaccessorie.ir/  -> SSL connection timeout
  curl -4 --tlsv1.2 --tls-max 1.2 https://veloraaccessorie.ir/ -> HTTP 200

This overlay does not modify SanaShop's code/DB or weaken other site TLS.
TLS 1.2 remains certificate-verified with standard system trust roots.
"""
import asyncio
import logging
import ssl
from urllib.parse import urlsplit

import httpx
import bot_resilience

log = logging.getLogger(__name__)
VELORA_HOST = "veloraaccessorie.ir"
_api_client = None
_original_api = None
_original_resilient_api = None


def _is_velora(site):
    try:
        base = urlsplit(str(site["base_url"]).strip())
        return (
            base.scheme == "https"
            and base.hostname == VELORA_HOST
            and base.port in (None, 443)
            and not base.username
            and not base.password
        )
    except (KeyError, TypeError, ValueError):
        return False


def _tls12_context():
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def _get_client():
    global _api_client
    if _api_client is None or _api_client.is_closed:
        _api_client = httpx.AsyncClient(
            verify=_tls12_context(),
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=16,
                max_keepalive_connections=10,
                keepalive_expiry=90,
            ),
            headers={"User-Agent": "DeltaJanebiUnifiedBot/VeloraTLS12"},
        )
    return _api_client


async def _velora_api(site, action, payload=None, timeout=25):
    """Same API contract as SanaShop core.api, scoped to the Velora domain."""
    base = str(site["base_url"]).rstrip("/")
    url = base + "/api/bot/v1/"
    request_timeout = httpx.Timeout(
        timeout=max(float(timeout), 10.0),
        connect=min(max(float(timeout), 10.0), 12.0),
    )
    last_error = None
    for attempt in range(2):
        try:
            response = await _get_client().post(
                url,
                json={"action": action, "payload": payload or {}},
                headers={"Authorization": "Bearer " + site["api_key"]},
                timeout=request_timeout,
            )
            if response.status_code == 401:
                raise RuntimeError("کلید API اتصال VELORA اشتباه است.")
            if response.status_code == 403:
                raise RuntimeError("دسترسی API سایت VELORA رد شد (HTTP 403).")
            if response.status_code == 404:
                raise RuntimeError("API مدیریت سایت VELORA پیدا نشد (HTTP 404).")
            if response.is_redirect:
                raise RuntimeError("API سایت VELORA آدرس دیگری را بازمی‌گرداند؛ URL ذخیره‌شده را بررسی کنید.")
            try:
                data = response.json()
            except ValueError:
                raise RuntimeError("پاسخ API سایت VELORA معتبر نیست (HTTP %s)." % response.status_code) from None
            if response.status_code >= 400 or not isinstance(data, dict) or not data.get("ok", False):
                reason = data.get("error") if isinstance(data, dict) else None
                raise RuntimeError(str(reason or f"HTTP {response.status_code}"))
            return data
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            log.warning(
                "VELORA TLS 1.2 site API connection failure action=%s retry=%s type=%s",
                action, attempt + 1, type(exc).__name__,
            )
            if attempt == 0:
                await asyncio.sleep(1)
    raise RuntimeError(
        "اتصال TLS 1.2 به سایت VELORA برقرار نشد؛ مسیر شبکه ger1 به finland را بررسی کنید."
    ) from last_error


async def _route_api(site, action, payload=None, timeout=25):
    if _is_velora(site):
        return await _velora_api(site, action, payload, timeout)
    return await _original_api(site, action, payload, timeout)


async def _route_poll_api(site, action, payload=None, timeout=25):
    if _is_velora(site):
        return await _velora_api(site, action, payload, timeout)
    return await _original_resilient_api(site, action, payload, timeout)


def install(core):
    """Patch only shared entrypoint's API dispatch and event polling.

    The source SanaShop package and its database are not written. Called after
    SanaShop v16 imports in the Delta unified bot entrypoint.
    """
    global _original_api, _original_resilient_api
    if core.api is not _route_api:
        _original_api = core.api
        core.api = _route_api
    if bot_resilience.resilient_api is not _route_poll_api:
        _original_resilient_api = bot_resilience.resilient_api
        bot_resilience.resilient_api = _route_poll_api
