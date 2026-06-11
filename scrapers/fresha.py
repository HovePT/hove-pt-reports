"""
fresha.py - v3: cookie overlay fix using force=True on submit button clicks.
Selectors verified live 2026-06-11. Overlay class "Ys_n2_ JjH72_" blocks pointer
events on the submit button — force=True bypasses actionability checks.
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
    """Hide overlay divs that intercept pointer events on the submit button."""
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
    await page.evaluate(
        "document.querySelectorAll('div[class*=\"Ys_\"]').forEach(e=>{e.style.pointerEvents='none';e.style.display='none';})"
    )


async def login(page: Page) -> None:
    await _goto(page, FRESHA_URL + "/users/sign-in", wait_ms=2000)
    await _ss(page, "debug_fresha_1_landing.png")
    await _dismiss_overlays(page)

    await page.wait_for_selector('input[type="email"]', timeout=15000)
    await page.fill('input[type="email"]', os.environ["FRESHA_EMAIL"])
    await _ss(page, "debug_fresha_2_email_filled.png")
    await page.click('button[type="submit"]', force=True)

    await page.wait_for_selector('input[type="password"]', timeout=15000)
    await _dismiss_overlays(page)
    await _ss(page, "debug_fresha_3_password.png")
    await page.fill('input[type="password"]', os.environ["FRESHA_PASSWORD"])
    await page.click('button[type="submit"]', force=True)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(5000)
    await _ss(page, "debug_fresha_4_logged_in.png")

    pathname = await page.evaluate("location.pathname")
    print(f"Fresha: logged in - pathname: {pathname}", flush=True)


async def get_clients(page: Page) -> list[dict]:
    await _goto(page, FRESHA_URL + "/clients/list", wait_ms=5000)
    await _ss(page, "debug_fresha_5_clients.png")

    clients = []
    page_num = 0

    while True:
        page_num += 1
        try:
            await page.wait_for_selector('[data-qa="customer-list-table-body"]', timeout=10000)
        except Exception:
            print(f"  [clients page {page_num}: table not found]", flush=True)
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

        next_btn = await page.query_selector('[data-qa="pagination-next"]')
        if not next_btn:
            break
        if await next_btn.get_attribute("aria-disabled") == "true":
            break
        await next_btn.click()
        await page.wait_for_timeout(3000)

    print(f"Fresha: found {len(clients)} clients", flush=True)
    return clients


def _parse_appt_date(caption: str) -> date | None:
    try:
        date_part = caption.split("*")[0].strip() if "*" in caption else caption.split("  ")[0].strip()
        # Also handle bullet char
        for sep in ["•", "·", "*", "  "]:
            if sep in caption:
                date_part = caption.split(sep)[0].strip()
                break
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
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = await context.new_page()

            await login(page)
            clients = await get_clients(page)

            today = date.today()
            weeks = []
            for w in range(4):
                ws = today - timedelta(days=today.weekday() + 7 * (3 - w))
                we = ws + timedelta(days=6)
                weeks.append((ws, we))

            results = []
            for client in clients:
                print(f"  Sessions for {client['name']}...", flush=True)
                sessions = await get_sessions_for_client(page, client["client_id"])

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

                streak = 0
                for ws, we in reversed(weeks):
                    if any(
                        ws <= date.fromisoformat(s["date"]) <= we and s["status"] == "attended"
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

        print(f"Fresha: scraped {len(results)} clients", flush=True)
        return results

    except Exception:
        print("Fresha scrape_all EXCEPTION:", flush=True)
        traceback.print_exc()
        return []


if __name__ == "__main__":
    import json
    headless = "--inspect" not in sys.argv
    data = asyncio.run(scrape_all(headless=headless))
    print(json.dumps(data, indent=2))
