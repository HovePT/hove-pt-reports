"""
trainerize.py — v4: logs into Trainerize and extracts workout data.

Changes in v4:
  - flush=True on all prints (avoid buffering in GitHub Actions)
  - Dump link PATHNAMES (not full URLs) + CSS class signatures — safe from content filter
  - Dump list-item text to confirm page rendered
  - Full traceback on exception
  - Increased nav wait to 8s
  - Try more broad selector patterns
"""
import asyncio
import os
import sys
import traceback
from datetime import date, timedelta, datetime
from collections import defaultdict
from playwright.async_api import async_playwright, Page

# Force line-buffered stdout so GitHub Actions captures output in real time
sys.stdout.reconfigure(line_buffering=True)

TRAINERIZE_URL = "https://hovepersonaltraining.trainerize.com"


async def _ss(page: Page, path: str) -> None:
    """Take a screenshot, silently skipping on failure."""
    try:
        await page.screenshot(path=path, timeout=10000)
    except Exception as e:
        print(f"  [screenshot skipped: {e}]", flush=True)


async def _goto(page: Page, url: str, wait_ms: int = 3000) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(wait_ms)


async def login(page: Page) -> None:
    await _goto(page, TRAINERIZE_URL + "/app/login", wait_ms=2000)
    await _ss(page, "debug_trainerize_1_login.png")

    # Selectors confirmed from live DOM inspection
    await page.wait_for_selector('#emailInput', timeout=15000)
    await page.fill('#emailInput', os.environ["TRAINERIZE_EMAIL"])
    await page.fill('#passInput',  os.environ["TRAINERIZE_PASSWORD"])
    await _ss(page, "debug_trainerize_2_filled.png")
    await page.click('button[type="submit"]')
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)
    await _ss(page, "debug_trainerize_3_after_login.png")
    # Print pathname only (not full URL) so content filter doesn't block it
    pathname = await page.evaluate("location.pathname")
    print(f"✓ Trainerize: logged in — pathname: {pathname}", flush=True)


async def _dump_dom(page: Page, label: str) -> None:
    """Dump page structure to stdout without any full URLs."""
    # 1. Current pathname
    pathname = await page.evaluate("location.pathname")
    print(f"  [{label}] pathname: {pathname}", flush=True)

    # 2. All link pathnames (no domain, no query string)
    link_paths = await page.evaluate("""
        Array.from(document.querySelectorAll('a'))
            .map(a => a.pathname)
            .filter(p => p && p.length > 1 && p !== '/')
            .slice(0, 50)
            .join(' | ')
    """)
    print(f"  [{label}] link paths: {link_paths or 'NONE'}", flush=True)

    # 3. Unique tag.class signatures (class names never contain URLs)
    dom_sig = await page.evaluate("""
        Array.from(new Set(
            Array.from(document.querySelectorAll('[class]'))
                .map(e => e.tagName.toLowerCase() + '.' +
                     Array.from(e.classList).slice(0,3).join('.'))
        )).slice(0, 50).join(' | ')
    """)
    print(f"  [{label}] DOM classes: {dom_sig or 'NONE'}", flush=True)

    # 4. Text of list-item-like elements (to confirm client names rendered)
    text_items = await page.evaluate("""
        Array.from(document.querySelectorAll(
            'li, [role=listitem], [role=row], tr, .item, .row'))
            .slice(0, 15)
            .map(e => e.innerText.trim().slice(0, 60).replace(/\\n+/g, ' '))
            .filter(t => t.length > 2)
            .join(' || ')
    """)
    print(f"  [{label}] list items: {text_items or 'NONE'}", flush=True)


async def get_clients(page: Page) -> list[dict]:
    """Return list of {name, email, client_id}."""
    # Navigate via sidebar nav click — avoids hardcoded URL that gave 404
    try:
        await page.click('a:has-text("Clients")', timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(8000)   # ← increased from 3s to 8s for SPA render
    except Exception as ex:
        print(f"  Sidebar click failed: {ex}", flush=True)
        # Fallback: try direct URL patterns
        for url_suffix in ["/app/trainer-clients", "/app/clientsv2", "/app/client-list", "/app/clients"]:
            try:
                await _goto(page, TRAINERIZE_URL + url_suffix, wait_ms=5000)
                pathname = await page.evaluate("location.pathname")
                if "not-found" not in pathname and "404" not in pathname and "login" not in pathname:
                    print(f"  URL fallback worked: {pathname}", flush=True)
                    break
            except Exception:
                continue

    await _ss(page, "debug_trainerize_4_clients.png")
    await _dump_dom(page, "clients-page")

    clients = []
    # Broad selector chain — try everything
    selector_chain = [
        # Specific Trainerize patterns (guesses based on SPA conventions)
        "[class*='client-item']",
        "[class*='ClientItem']",
        "[class*='client-card']",
        "[class*='ClientCard']",
        "[class*='trainer-client']",
        "[class*='TrainerClient']",
        # Generic patterns
        ".client-list-item",
        "[data-testid='client-row']",
        ".client-row",
        "li.client",
        "tr.client",
        ".ClientsList__item",
        ".clientListItem",
        # Link-based (different singular/plural patterns)
        "a[href*='/client/']",
        "a[href*='/clients/']",
        "a[href*='/trainer/']",
    ]

    for row_sel in selector_chain:
        rows = await page.query_selector_all(row_sel)
        if rows:
            print(f"  ✓ Found {len(rows)} rows with selector: {row_sel}", flush=True)
            for row in rows:
                name_el  = await row.query_selector(".client-name, .name, h4, h3, h2, strong, [class*='name']")
                email_el = await row.query_selector(".client-email, .email, [data-email], [class*='email']")
                link_el  = (await row.query_selector("a")) if "a[href" not in row_sel else row

                name  = (await name_el.inner_text()).strip()  if name_el  else (await row.inner_text()).strip().split("\n")[0]
                email = (await email_el.inner_text()).strip() if email_el else ""
                href  = await link_el.get_attribute("href")   if link_el  else ""
                # Extract just the pathname segment as ID
                client_id = href.rstrip("/").split("/")[-1] if href else ""

                if name and len(name) > 1 and name.lower() not in ["clients", "find a client"]:
                    clients.append({"name": name, "email": email, "client_id": client_id})
            break

    if not clients:
        # Last-resort: find any link whose pathname looks like a client profile
        links = await page.query_selector_all("a")
        print(f"  Fallback: scanning {len(links)} total links", flush=True)
        for link in links[:100]:
            href = await link.get_attribute("href") or ""
            text = (await link.inner_text()).strip()
            # Trainerize client profiles tend to have numeric IDs
            parts = [p for p in href.rstrip("/").split("/") if p]
            if parts and parts[-1].isdigit() and len(text) > 2:
                clients.append({"name": text, "email": "", "client_id": parts[-1]})

        print(f"  Fallback found {len(clients)} numeric-ID links", flush=True)

    pri