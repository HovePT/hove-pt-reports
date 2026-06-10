"""
fresha.py — logs into Fresha and extracts session history for all clients
over the last 4 weeks.

NOTE: Selectors marked # [SELECTOR] need to be verified against the live UI
once Chrome is connected. Run `python -m scrapers.fresha --inspect` to
open a headed browser at the right page.
"""
import asyncio
import os
from datetime import date, timedelta
from playwright.async_api import async_playwright, Page


FRESHA_URL = "https://partners.fresha.com"


async def login(page: Page) -> None:
    # Fresha login is two-step: email → Continue → password → Continue
    await page.goto(FRESHA_URL + "/users/sign-in")
    await page.wait_for_load_state("networkidle")

    # Step 1: Enter email and click Continue
    # Use evaluate() to bypass any overlay/reCAPTCHA div intercepting pointer events
    await page.fill('input[type="email"]', os.environ["FRESHA_EMAIL"])
    await page.locator('[data-qa="continue"]').evaluate("el => el.click()")
    await page.wait_for_load_state("networkidle")

    # Step 2: Wait for password field, enter password, submit
    await page.wait_for_selector('input[type="password"]', timeout=15000)
    await page.fill('input[type="password"]', os.environ["FRESHA_PASSWORD"])
    await page.locator('[data-qa="continue"]').evaluate("el => el.click()")
    await page.wait_for_load_state("networkidle")
    print("✓ Fresha: logged in")


async def get_clients(page: Page) -> list[dict]:
    """Return list of {name, email, client_id} for all active clients."""
    await page.goto(FRESHA_URL + "/clients")
    await page.wait_for_load_state("networkidle")

    clients = []
    # TODO: update selector after inspecting the live Clients page
    rows = await page.query_selector_all(".client-row")  # [SELECTOR]
    for row in rows:
        name_el  = await row.query_selector(".client-name")   # [SELECTOR]
        email_el = await row.query_selector(".client-email")  # [SELECTOR]
        link_el  = await row.query_selector("a")              # [SELECTOR]

        name  = (await name_el.inner_text()).strip()  if name_el  else ""
        email = (await email_el.inner_text()).strip() if email_el else ""
        href  = await link_el.get_attribute("href")   if link_el  else ""
        client_id = href.split("/")[-1] if href else ""

        if name:
            clients.append({"name": name, "email": email, "client_id": client_id})

    print(f"✓ Fresha: found {len(clients)} clients")
    return clients


async def get_sessions_for_client(page: Page, client_id: str) -> list[dict]:
    """
    Returns sessions from the last 28 days for a single client.
    Each session: {date, status}  status = 'attended' | 'cancelled' | 'no-show'
    """
    cutoff = date.today() - timedelta(days=28)
    url = f"{FRESHA_URL}/clients/{client_id}/appointments"  # [URL — verify]
    await page.goto(url)
    await page.wait_for_load_state("networkidle")

    sessions = []
    # TODO: update selectors after inspecting the client appointment history page
    rows = await page.query_selector_all(".appointment-row")  # [SELECTOR]
    for row in rows:
        date_el   = await row.query_selector(".appointment-date")    # [SELECTOR]
        status_el = await row.query_selector(".appointment-status")  # [SELECTOR]

        raw_date = (await date_el.inner_text()).strip() if date_el else ""
        status   = (await status_el.inner_text()).strip().lower() if status_el else ""

        # Parse date — Fresha likely shows "12 May 2025"
        try:
            from datetime import datetime
            session_date = datetime.strptime(raw_date, "%d %b %Y").date()
        except ValueError:
            continue

        if session_date >= cutoff:
            sessions.append({"date": session_date.isoformat(), "status": status})

    return sessions


def summarise_sessions(sessions: list[dict]) -> dict:
    """
    Given raw session list, return:
      sessions_attended, sessions_scheduled, consistency_pct,
      current_streak (weeks), session_rows (for table)
    """
    from collections import defaultdict
    from datetime import datetime

    today = date.today()
    # Build 4 week buckets (most recent first)
    weeks = []
    for w in range(4):
        week_start = today - timedelta(days=today.weekday() + 7 * (3 - w))
        week_end   = week_start + timedelta(days=6)
        weeks.append((week_start, week_end))

    attended_total = 0
    scheduled_total = 0
    session_rows = []

    for i, (ws, we) in enumerate(weeks):
        week_sessions = [
            s for s in sessions
            if ws <= date.fromisoformat(s["date"]) <= we
        ]
        week_attended  = sum(1 for s in week_sessions if "attended" in s["status"] or "complete" in s["status"])
        week_scheduled = len(week_sessions)

        attended_total  += week_attended
        scheduled_total += week_scheduled

        prev_row = session_rows[-1] if session_rows else None
        if prev_row:
            if week_attended > prev_row["_raw_attended"]:
                trend, trend_val = "up", f"+{week_attended - prev_row['_raw_attended']}"
            elif week_attended < prev_row["_raw_attended"]:
                trend, trend_val = "down", str(week_attended - prev_row["_raw_attended"])
            else:
                trend, trend_val = "same", ""
        else:
            trend, trend_val = "same", ""

        session_rows.append({
            "week_number": i + 1,
            "date": ws.strftime("%d %b"),
            "attended": f"{week_attended} / {week_scheduled}",
            "top_lift": "—",  # filled in by merge with Trainerize data
            "trend": trend,
            "trend_val": trend_val,
            "_raw_attended": week_attended,
        })

    consistency_pct = round(attended_total / scheduled_total * 100) if scheduled_total else 0

    # Streak = consecutive weeks (most recent first) with ≥ 1 attended session
    streak = 0
    for row in reversed(session_rows):
        if row["_raw_attended"] > 0:
            streak += 1
        else:
            break

    return {
        "sessions_attended": attended_total,
        "sessions_scheduled": scheduled_total,
        "consistency_pct": consistency_pct,
        "current_streak": streak,
        "session_rows": session_rows,
    }


async def scrape_all(headless: bool = True) -> list[dict]:
    """Main entry — returns list of per-client Fresha summaries."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page    = await browser.new_page()

        await login(page)
        clients = await get_clients(page)

        results = []
        for client in clients:
            sessions = await get_sessions_for_client(page, client["client_id"])
            summary  = summarise_sessions(sessions)
            results.append({**client, **summary})

        await browser.close()

    return results


if __name__ == "__main__":
    import sys
    headless = "--inspect" not in sys.argv
    data = asyncio.run(scrape_all(headless=headless))
    import json
    print(json.dumps(data, indent=2))
