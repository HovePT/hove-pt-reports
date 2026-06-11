"""
fresha.py - v6: replace wait_for_load_state("networkidle") with wait_for_url().

Selectors verified against live UI on 2026-06-11:
  - Row:        tr[data-qa^="customer-list-table-row"]
  - Client ID:  row data-href attr -> split("/").pop()
  - Name:       td:nth-child(2) p:nth-child(1)
  - Email:      td:nth-child(2) p:nth-child(2)
  - Pagination: [data-qa="pagination-next"] (aria-disabled="true" = last page)
  - Appt cards: [data-qa^="appointment-card-"]
  - Status:     [data-qa="status-label"]
  - Caption:    [data-qa="appointment-caption"]
  - Cookie btn: button[data-qa*="accept"] (confirmed working)

v3 fix: force=True bypasses actionability checks on submit button.
v4 fix: page.press('Enter') on input fields — native form submit that no overlay can block.
v5 fix: log URL/title after landing + 30s timeout on email input.
v6 fix: post-login wait uses wait_for_url(lambda: "/sign-in" not in url) instead of
  networkidle — Fresha keeps background connections open so networkidle never fires.
"""
import asyncio
import os
import sys
import traceback
from datetime import date, timedelta, datetime
from playwright.async_api import async_playwright, Page

sys.stdout.reconfigure(line_buffering=True)

FRESHA_URL = "https://partners.fresha.com"


async def _ss(page: Page, path: str) -> None:
    try:
        await page.screenshot(path=path, timeout=10000)
    except Exception as e:
        print(f"  [screenshot skipped: {e}]", flush=True)


async def _goto(page: Page, url: str, wait_ms: int = 3000) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(wait_ms)


async def _dismiss_overlays(page: Page) -> None:
    """Dismiss cookie consent / GDPR banners that intercept pointer events."""
    for selector in [
        '[data-qa="cookie-accept"]',
        '[data-qa="accept-cookies"]',
        '[data-qa*="consent"] button',
        'button[data-qa*="accept"]',
    ]:
        try:
            el = await page.query_selector(selector)
            if el:
                await el.click(force=True)
                await page.wait_for_timeout(500)
                print(f"  Dismissed overlay: {selector}", flush=True)
                return
        except Exception:
            pass
    # Fallback: zero out pointer-events on the obfuscated overlay divs
    await page.evaluate("""
        document.querySelectorAll('div[class*="Ys_"]').forEach(el => {
            el.style.pointerEvents = 'none';
            el.style.display = 'none';
        });
    """)


async def login(page: Page) -> None:
    await _goto(page, FRESHA_URL + "/users/sign-in", wait_ms=3000)
    url_after = await page.evaluate("location.href")
    title_after = await page.evaluate("document.title")
    print(f"  [login] landed at: {url_after} | title: {title_after}", flush=True)
    await _ss(page, "debug_fresha_1_landing.png")
    await _dismiss_overlays(page)

    # Step 1: enter email — press Enter to submit (bypasses any overlay on the button)
    await page.wait_for_selector('input[type="email"]', timeout=30000)
    await page.fill('input[type="email"]', os.environ["FRESHA_EMAIL"])
    await _ss(page, "debug_fresha_2_email_filled.png")
    await page.press('input[type="email"]', 'Enter')

    # Step 2: enter password — press Enter to submit
    await page.wait_for_selector('input[type="password"]', timeout=15000)
    await _dismiss_overlays(page)
    await _ss(page, "debug_fresha_3_password.png")
    await page.fill('input[type="password"]', os.environ["FRESHA_PASSWORD"])
    await page.press('input[type="password"]', 'Enter')
    # Wait for URL to change away from /sign-in (fires as soon as redirect begins)
    try:
        await page.wait_for_url(
            lambda url: "/sign-in" not in url,
            timeout=15000,
            wait_until="commit",
        )
    except Exception:
        # Fallback: click submit button if Enter didn't trigger navigation
        print("  Enter didn't navigate — trying force click on submit...", flush=True)
        await page.click('button[type="submit"]', force=True)
        await page.wait_for_url(
            lambda url: "/sign-in" not in url,
            timeout=15000,
            wait_until="commit",
        )
    await page.wait_for_timeout(3000)
    await _ss(page, "debug_fresha_4_logged_in.png")

    pathname = await page.evaluate("location.pathname")
    print(f"✓ Fresha: logged in – pathname: {pathname}", flush=True)


async def get_clients(page: Page) -> list[dict]:
    """Return list of {name, email, client_id} for all clients across paginated list."""
    await _goto(page, FRESHA_URL + "/clients/list", wait_ms=5000)
    await _ss(page, "debug_fresha_5_clients.png")

    clients = []
    page_num = 0

    while True:
        page_num += 1
        try:
            await page.wait_for_selector('[data-qa="customer-list-table-body"]', timeout=10000)
        except Exception:
            print(f"  [clients page {page_num}: table not found, stopping]", flush=True)
            break

        await page.wait_for_timeout(2000)
        rows = await page.query_selector_all('tr[data-qa^="customer-list-table-row"]')
        print(f"  Clients page {page_num}: {len(rows)} rows", flush=True)

        for row in rows:
            data_href = await row.get_attribute("data-href") or ""
            client_id = data_href.split("/")[-1]
            ps        = await row.query_selector_all("td:nth-child(2) p")
            name      = (await ps[0].inner_text()).strip() if len(ps) > 0 else ""
            email     = (await ps[1].inner_text()).strip() if len(ps) > 1 else ""
            if name and client_id:
                clients.append({"name": name, "email": email, "client_id": client_id})

        # Pagination
        next_btn = await page.query_selector('[data-qa="pagination-next"]')
        if not next_btn:
            break
        aria_disabled = await next_btn.get_attribute("aria-disabled")
        if aria_disabled == "true":
            break
        await next_btn.click()
        await page.wait_for_timeout(3000)

    print(f"✓ Fresha: found {len(clients)} clients", flush=True)
    return clients


def _parse_appt_date(caption: str) -> date | None:
    """
    Parse date from caption like "Thu 2 Jul 4:30pm  •  Hove Personal Training".
    Returns date or None on failure.
    """
    try:
        date_part = caption.split("•")[0].strip()
        parts = date_part.split()
        if len(parts) < 3:
            return None
        today = date.today()
        day_str = f"{parts[1]} {parts[2]} {today.year}"
        appt_date = datetime.strptime(day_str, "%d %b %Y").date()
        if appt_date > today + timedelta(days=60):
            appt_date = appt_date.replace(year=today.year - 1)
        elif appt_date < today - timedelta(days=300):
            appt_date = appt_date.replace(year=today.year + 1)
        return appt_date
    except (ValueError, IndexError):
        return None


async def get_sessions_for_client(page: Page, client_id: str) -> list[dict]:
    """
    Returns sessions from the last 28 days.
    Each entry: {date (ISO str), status}
    status: 'attended' | 'scheduled' | 'cancelled' | 'no-show'
    """
    cutoff = date.today() - timedelta(days=28)
    url = f"{FRESHA_URL}/clients/list/drawer/clients/{client_id}/appointments"

    try:
        await _goto(page, url, wait_ms=3000)
        await page.wait_for_selector('[data-qa^="appointment-card-"]', timeout=10000)
    except Exception:
        return []

    cards = await page.query_selector_all('[data-qa^="appointment-card-"]')
    sessions = []

    for card in cards:
        status_el  = await card.query_selector('[data-qa="status-label"]')
        caption_el = await card.query_selector('[data-qa="appointment-caption"]')
        if not status_el or not caption_el:
            continue

        raw_status  = (await status_el.inner_text()).strip()
        raw_caption = (await caption_el.inner_text()).strip().replace("\n", " ")

        appt_date = _parse_appt_date(raw_caption)
        if appt_date is None or appt_date < cutoff:
            continue

        sl = raw_status.lower()
        today = date.today()
        if sl == "completed":
            status = "attended"
        elif sl in ("booked", "confirmed") and appt_date <= today:
            status = "attended"
        elif sl in ("booked", "confirmed") and appt_date > today:
            status = "scheduled"
        elif sl == "no show":
            status = "no-show"
        elif sl == "cancelled":
            status = "cancelled"
        else:
            status = "attended"

        sessions.append({"date": appt_date.isoformat(), "status": status})

    return sessions


async def scrape_all(headless: bool = True) -> list[dict]:
    """
    Returns list of client dicts ready for the PDF generator:
    [{name, email, client_id, sessions_attended, sessions_scheduled,
      consistency_pct, current_streak, session_rows}]
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-GB",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = await context.new_page()

            await login(page)
            clients = await get_clients(page)

            today = date.today()

            # Build 4-week windows (Mon-Sun, most recent last)
            weeks = []
            for w in range(4):
                ws = today - timedelta(days=today.weekday() + 7 * (3 - w))
                we = ws + timedelta(days=6)
                weeks.append((ws, we))

            results = []
            for client in clients:
                print(f"  Scraping sessions for {client['name']}...", flush=True)
                sessions = await get_sessions_for_client(page, client["client_id"])

                # Per-week breakdown
                session_rows = []
                for ws, we in weeks:
                    wk = [s for s in sessions if ws <= date.fromisoformat(s["date"]) <= we]
                    session_rows.append({
                        "week_label":  ws.strftime("%-d %b"),
                        "attended":    sum(1 for s in wk if s["status"] == "attended"),
                        "scheduled":   sum(1 for s in wk if s["status"] == "scheduled"),
                        "cancelled":   sum(1 for s in wk if s["status"] == "cancelled"),
                        "no_show":     sum(1 for s in wk if s["status"] == "no-show"),
                    })

                past = [s for s in sessions if date.fromisoformat(s["date"]) <= today]
                attended_total  = sum(1 for s in past     if s["status"] == "attended")
                scheduled_total = sum(1 for s in sessions if s["status"] == "scheduled")
                cancelled_total = sum(1 for s in sessions if s["status"] == "cancelled")
                noshows_total   = sum(1 for s in sessions if s["status"] == "no-show")

                denom = attended_total + cancelled_total + noshows_total
                consistency_pct = round(attended_total / denom * 100) if denom > 0 else 0

                # Current streak: consecutive weeks with >=1 attended (newest to oldest)
                streak = 0
                for ws, we in reversed(weeks):
                    if any(
                        ws <= date.fromisoformat(s["date"]) <= we
                        and s["status"] == "attended"
                        for s in sessions
                    ):
                        streak += 1
                    else:
                        break

                results.append({
                    "name":               client["name"],
                    "email":              client["email"],
                    "client_id":          client["client_id"],
                    "sessions_attended":  attended_total,
                    "sessions_scheduled": scheduled_total,
                    "consistency_pct":    consistency_pct,
                    "current_streak":     streak,
                    "session_rows":       session_rows,
                })

            await context.close()
            await browser.close()

        print(f"✓ Fresha: scraped {len(results)} clients", flush=True)
        return results

    except Exception:
        print("✗ Fresha scrape_all EXCEPTION:", flush=True)
        traceback.print_exc()
        return []


if __name__ == "__main__":
    import json
    headless = "--inspect" not in sys.argv
    data = asyncio.run(scrape_all(headless=headless))
    print(json.dumps(data, indent=2))
