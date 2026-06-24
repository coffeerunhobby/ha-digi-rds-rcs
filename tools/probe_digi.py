"""Local dev probe for the Digi (RCS & RDS) web client.

Runs the *real* ``custom_components/digi/api.py`` against www.digi.ro outside of
Home Assistant, so the login / 2FA / invoice-scraping flow can be smoke-tested
with real credentials.

This is a development tool only — it is NOT part of the integration and is not
shipped to Home Assistant.

Credentials are read from ``tools/.digi_creds.json`` (git-ignored):

    {"username": "you@example.com", "password": "..."}

Because Digi accounts usually require 2FA, the flow is staged across commands so
you can supply the code you receive:

    python tools/probe_digi.py login      # login; if 2FA, sends a code
    python tools/probe_digi.py code 123456 # validate the received code
    python tools/probe_digi.py fetch       # fetch + print redacted invoices

Session cookies and 2FA context are cached in ``tools/.digi_state.json`` between
commands (also git-ignored).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG_DIR = os.path.join(ROOT, "custom_components", "digi")
CREDS_FILE = os.path.join(HERE, ".digi_creds.json")
STATE_FILE = os.path.join(HERE, ".digi_state.json")


def _load_digi_package() -> types.ModuleType:
    """Load const/models/api from the integration without importing Home Assistant."""
    pkg_name = "digiprobe"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [PKG_DIR]
    sys.modules[pkg_name] = pkg

    def _load(mod_name: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{mod_name}", os.path.join(PKG_DIR, f"{mod_name}.py")
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{mod_name}"] = module
        spec.loader.exec_module(module)
        return module

    _load("const")
    _load("models")
    return _load("api")


api = _load_digi_package()


def _read_creds() -> dict[str, str]:
    if not os.path.exists(CREDS_FILE):
        raise SystemExit(
            f"Missing {CREDS_FILE}. Create it with "
            '{"username": "...", "password": "..."}'
        )
    with open(CREDS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _read_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _write_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, default=str)


def _mask(value):
    if value is None:
        return None
    text = str(value)
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}…{text[-2:]}"


async def _cmd_login() -> None:
    import aiohttp

    creds = _read_creds()
    async with aiohttp.ClientSession() as session:
        client = api.DigiApiClient(session)
        try:
            final_url, html = await client.login(
                creds["username"], creds["password"]
            )
            print(f"login final_url: {final_url}")

            state: dict = {"cookies": client.export_cookies()}

            if "/auth/2fa" in final_url:
                ctx = await client.get_2fa_context(html)
                methods = list(ctx.methods.keys())
                print(f"2FA required. methods={methods}")
                method = "sms" if "sms" in methods else methods[0]
                await client.send_2fa_code(ctx, method)
                print(f"Sent a {method.upper()} code. Run: probe_digi.py code <CODE>")
                state.update(
                    {
                        "stage": "2fa",
                        "method": method,
                        "methods": ctx.methods,
                        "selections": ctx.selections,
                        "cookies": client.export_cookies(),
                    }
                )
            elif "/auth/address-select" in final_url:
                options = await client.get_address_options(html)
                print("Address selection available:")
                for opt in options:
                    print(f"  value={opt.value!r} label={opt.label!r}")
                state["stage"] = "address"
                state["cookies"] = client.export_cookies()
            else:
                print("Logged in without 2FA. Run: probe_digi.py fetch")
                state["stage"] = "ready"

            _write_state(state)
        finally:
            await client.close()


async def _cmd_code(code: str) -> None:
    import aiohttp

    state = _read_state()
    if state.get("stage") != "2fa":
        raise SystemExit("No pending 2FA. Run 'login' first.")

    async with aiohttp.ClientSession() as session:
        client = api.DigiApiClient(session)
        client.import_cookies(state.get("cookies") or [])
        try:
            ctx = api.TwoFactorContext(
                methods=state["methods"],
                html="",
                selections=state.get("selections") or {},
            )
            final_url, html = await client.validate_2fa_code(
                ctx, state["method"], code
            )
            print(f"after 2FA final_url: {final_url}")
            state["cookies"] = client.export_cookies()

            if "/auth/address-select" in final_url:
                options = await client.get_address_options(html)
                print("Address selection available:")
                for opt in options:
                    print(f"  value={opt.value!r} label={opt.label!r}")
                state["stage"] = "address"
            else:
                print("2FA OK. Run: probe_digi.py fetch")
                state["stage"] = "ready"

            _write_state(state)
        finally:
            await client.close()


async def _cmd_fetch(history_limit: int) -> None:
    import aiohttp

    state = _read_state()
    cookies = state.get("cookies") or []
    if not cookies:
        raise SystemExit("No session cookies. Run 'login' (and 'code') first.")

    async with aiohttp.ClientSession() as session:
        client = api.DigiApiClient(session)
        client.import_cookies(cookies)
        try:
            data = await client.async_fetch_data(history_limit=history_limit)
            state["cookies"] = client.export_cookies()
            _write_state(state)

            print(f"\nlast_update: {data.last_update}")
            print(f"addresses: {len(data.invoices_by_address)}")
            for key, entry in data.invoices_by_address.items():
                print(f"\n── address {_mask(key)} ── {_mask(entry.address)}")
                print(f"   invoices: {len(entry.history)}  unpaid: {entry.unpaid_count}")
                latest = entry.latest or {}
                print(
                    "   latest:",
                    {
                        "invoice_number": _mask(latest.get("invoice_number")),
                        "issue_date": latest.get("issue_date"),
                        "due_date": latest.get("due_date"),
                        "amount": latest.get("amount"),
                        "rest": latest.get("rest"),
                        "status": latest.get("status"),
                        "services": [
                            s.get("name") for s in (latest.get("services") or [])
                        ],
                    },
                )
        finally:
            await client.close()


async def _cmd_diag(history_limit: int) -> None:
    import re

    import aiohttp

    state = _read_state()
    cookies = state.get("cookies") or []
    if not cookies:
        raise SystemExit("No session cookies. Run 'login' (and 'code') first.")

    async with aiohttp.ClientSession() as session:
        client = api.DigiApiClient(session)
        client.import_cookies(cookies)
        try:
            print(f"imported cookies: {[c.get('key') for c in cookies]}")
            resp = await client._request(
                "GET", api.INVOICES_URL, allow_redirects=True
            )
            html = await client._read_text(resp)
            final_url = str(resp.url)
            print(f"status: {resp.status}")
            print(f"final_url: {final_url}")
            print(f"html length: {len(html)}")

            for needle in (
                "/auth/login",
                "/auth/2fa",
                "/auth/address-select",
                "Facturi curente",
                "Facturi achitate",
                "client-invoices-cfg",
                "my-account-tbl-row",
                "data-invoice-address",
                "address-",
            ):
                print(f"  contains {needle!r}: {html.count(needle)}")

            current_rows = len(api.RE_CURRENT_ROW.findall(html))
            archive_rows = len(api.RE_ROW.findall(html))
            print(f"  RE_CURRENT_ROW matches: {current_rows}")
            print(f"  RE_ROW matches: {archive_rows}")

            try:
                parsed = client._parse_invoice_page(html)
                print(f"  _parse_invoice_page rows: {len(parsed['rows'])}")
            except Exception as err:  # noqa: BLE001
                print(f"  _parse_invoice_page raised: {err!r}")

            # Persist the raw page (git-ignored) for offline inspection.
            dump = os.path.join(HERE, ".digi_invoices.html")
            with open(dump, "w", encoding="utf-8") as fh:
                fh.write(html)
            print(f"  raw page written to {dump}")
        finally:
            await client.close()


def main() -> None:
    # Windows consoles default to cp1252; force UTF-8 so Romanian text prints.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]
    if cmd == "login":
        asyncio.run(_cmd_login())
    elif cmd == "code":
        if len(args) < 2:
            raise SystemExit("Usage: probe_digi.py code <CODE>")
        asyncio.run(_cmd_code(args[1]))
    elif cmd == "fetch":
        limit = int(args[1]) if len(args) > 1 else 6
        asyncio.run(_cmd_fetch(limit))
    elif cmd == "diag":
        limit = int(args[1]) if len(args) > 1 else 6
        asyncio.run(_cmd_diag(limit))
    else:
        raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
