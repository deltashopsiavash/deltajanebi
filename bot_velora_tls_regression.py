#!/usr/bin/env python3
"""Regression tests for Velora-only TLS 1.2 API transport."""
import asyncio
import ssl

import httpx
import external_bot_delta_v16 as entry
import delta_sana_tls_compat as compat
import external_bot_delta_v15 as router
import bot_resilience

SANA_SITE = {"id": 1, "base_url": "https://veloraaccessorie.ir", "api_key": "sample-sana-key"}
DELTA_SITE = {"id": 2, "base_url": "https://deltajanebishop.ir", "api_key": "sample-delta-key"}


def check_host_and_tls():
    assert compat._is_velora(SANA_SITE)
    assert compat._is_velora({"base_url":"https://VELORAACCESSORIE.IR/", "api_key":"x"})
    for host in (
        "https://veloraaccessorie.ir.evil.org",
        "http://veloraaccessorie.ir",
        "https://test.veloraaccessorie.ir",
        "https://veloraaccessorie.ir:8443",
        "https://veloraaccessorie.ir@evil.org",
        "https://other.example",
    ):
        assert not compat._is_velora({"base_url": host, "api_key": "x"}), host
    ctx = compat._tls12_context()
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.maximum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


async def check_api_routes():
    assert router.core.api is compat._route_api
    assert bot_resilience.resilient_api is compat._route_poll_api

    invocations = []
    def handle(request):
        invocations.append(request)
        assert request.url.host == "veloraaccessorie.ir"
        assert request.url.path == "/api/bot/v1/"
        assert request.headers["Authorization"] == "Bearer sample-sana-key"
        assert request.method == "POST"
        req = __import__("json").loads(request.content)
        assert req["action"] in {"settings_get", "events_poll", "backup_status"}
        return httpx.Response(200, json={"ok": True, "data": {"site_name": "VELORA"}})
    original_client = compat._api_client
    original_api = compat._original_api
    original_poll = compat._original_resilient_api
    compat._api_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    delegated = []
    async def original(site, action, payload=None, timeout=25):
        delegated.append((site["id"], action))
        return {"ok": True, "site": {"platform": "deltajanebi"}}
    compat._original_api = original
    compat._original_resilient_api = original
    try:
        s = await router.core.api(SANA_SITE, "settings_get")
        assert s["data"]["site_name"] == "VELORA"
        await router.core.api(SANA_SITE, "backup_status", timeout=30)
        await bot_resilience.resilient_api(SANA_SITE, "events_poll", {"limit": 3})
        assert len(invocations) == 3, len(invocations)
        assert not delegated
        response = await router.core.api(DELTA_SITE, "ping")
        assert response["site"]["platform"] == "deltajanebi"
        await bot_resilience.resilient_api(DELTA_SITE, "events_poll")
        assert delegated == [(2, "ping"), (2, "events_poll")], delegated
    finally:
        await compat._api_client.aclose()
        compat._api_client = original_client
        compat._original_api = original_api
        compat._original_resilient_api = original_poll


async def check_reject_unverified_and_bad_key():
    def handler(req):
        return httpx.Response(401, json={"ok": False, "error": "invalid api key"})
    orig = compat._api_client
    compat._api_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        try:
            await compat._velora_api(SANA_SITE, "settings_get")
        except RuntimeError as exc:
            assert "کلید API" in str(exc)
        else:
            raise AssertionError("Invalid API key must be rejected")
    finally:
        await compat._api_client.aclose()
        compat._api_client = orig


async def main():
    check_host_and_tls()
    await check_api_routes()
    await check_reject_unverified_and_bad_key()
    print("Velora TLS 1.2 transport, Sana isolation, scheduled events and API errors: OK")


asyncio.run(main())
