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
        await page.wait_for_timeout(8000)   # increased from 3s to 8s for SPA render
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
        "[class*='client-item']",
        "[class*='ClientItem']",
        "[class*='client-card']",
        "[class*='ClientCard']",
        "[class*='trainer-client']",
        "[class*='TrainerClient']",
        ".client-list-item",
        "[data-testid='client-row']",
        ".client-row",
        "li.client",
        "tr.client",
        ".ClientsList__item",
        ".clientListItem",
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
                client_id = href.rstrip("/").split("/")[-1] if href else ""

                if name and len(name) > 1 and name.lower() not in ["clients", "find a client"]:
                    clients.append({"name": name, "email": email, "client_id": client_id})
            break

    if not clients:
        links = await page.query_selector_all("a")
        print(f"  Fallback: scanning {len(links)} total links", flush=True)
        for link in links[:100]:
            href = await link.get_attribute("href") or ""
            text = (await link.inner_text()).strip()
     .      parts = [p for p in href.rstrip("/").split("/") if p]
            if parts and parts[-1].isdigit() and len(text) > 2:
                clients.append({"name": text, "email": "", "client_id": parts[-1]})

        print(f"  Fallback found {len(clients)} numeric-ID links", flush=True)

    print(f"✓ Trainerize: found {len(clients)} clients", flush=True)
    return clients


async def get_workout_logs(page: Page, client_id: str) -> list[dict]:
    """
    Returns raw workout log entries for the last 28 days.
    Each entry: {date, exercise_name, sets: [{weight, reps}]}
    """
    cutoff = date.today() - timedelta(days=28)

    for url_pattern in [
           f"{TRAINERIZE_URL}/app/client/{client_id}/workouts/log",
        f"{TRAINERIZE_URL}/app/trainer-clients/{client_id}/workouts",
        f"{TRAINERIZE_URL}/clients/{client_id}/workouts/log",
    ]:
        try:
            await _goto(page, url_pattern, wait_ms=4000)
            pathname = await page.evaluate("location.pathname")
            if "login" not in pathname and "not-found" not in pathname:
                break
        except Exception:
            continue

    logs = []
    workout_blocks = await page.query_selector_all(
        ".workout-log-entry, [class*='workout'], [class*='WorkoutLog'], [class*='activity-log']"
    )
    for block in workout_blocks:
        date_el = await block.query_selector(".log-date, [class*='date'], time")
        raw_date = (await date_el.inner_text()).strip() if date_el else ""

        try:
            log_date = datetime.strptime(raw_date, "%d %b %Y").date()
        except ValueError:
            continue

        if log_date < cutoff:
            continue

        exercise_blocks = await block.query_selector_all(".exercise-entry, [class*='exercise']")
        for ex_block in exercise_blocks:
            name_el = await ex_block.query_selector(".exercise-name, [class*='name']")
            ex_name = (await name_el.inner_text()).strip() if name_el else "Unknown"

            set_els = await ex_block.query_selector_all(".set-row, [class*='set']")
            sets = []
            for set_el in set_els:
                weight_el = await set_el.query_selector(".weight, [class*='weight']")
                reps_el   = await set_el.query_selector(".reps, [class*='reps']")
                try:
                    weight = float((await weight_el.inner_text()).replace("kg", "").strip()) if weight_el else 0
                    reps   = int((await reps_el.inner_text()).strip())                        if reps_el   else 0
                except (ValueError, AttributeError):
                    weight, reps = 0, 0
                sets.append({"weight": weight, "reps": reps})

            logs.append({"date": log_date.isoformat(), "exercise_name": ex_name, "sets": sets})

    return logs


def summarise_exercises(logs: list[dict], top_n: int = 4) -> list[dict]:
    today = date.today()
    weeks = []
    for w in range(4):
        ws = today - timedelta(days=today.weekday() + 7 * (3 - w))
        we = ws + timedelta(days=6)
        weeks.append((ws, we))

    week_labels = [ws.strftime("%-d %b") for ws, _ in weeks]
    freq = defaultdict(int)
    for log in logs:
        freq[log["exercise_name"]] += 1
    top_exercises = sorted(freq, key=lambda x: -freq[x])[:top_n]

    result = []
    for ex in top_exercises:
        ex_logs = [l for l in logs if l["exercise_name"] == ex]
        week_bests = []
        for ws, we in weeks:
            week_logs = [l for l in ex_logs if ws <= date.fromisoformat(l["date"]) <= we]
            best = 0.0
            for log in week_logs:
                for s in log["sets"]:
                    if s["reps"] > 0 and s["weight"] > best:
                        best = s["weight"]
            week_bests.append(best)
        overall_best = max(week_bests) if week_bests else 0
        result.append({
            "name": ex,
            "best": f"{overall_best:.1f} kg" if overall_best else "—",
            "week_labels": week_labels,
            "values": week_bests,
        })
    return result


async def scrape_all(headless: bool = True) -> dict:
    """
    Returns dict keyed by client email:
      {email: {exercises: [...], top_lift_by_week: [...]}}
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

            results = {}
            for client in clients:
                logs      = await get_workout_logs(page, client["client_id"])
                exercises = summarise_exercises(logs)

                today = date.today()
                top_by_week = []
                for w in range(4):
                    ws = today - timedelta(days=today.weekday() + 7 * (3 - w))
                    we = ws + timedelta(days=6)
                    week_logs = [l for l in logs if ws <= date.fromisoformat(l["date"]) <= we]
                    if week_logs:
                        best_ex = max(week_logs, key=lambda l: max((s["weight"] for s in l["sets"]), default=0))
                        top_by_week.append(best_ex["exercise_name"])
                    else:
                        top_by_week.append("—")

                results[client["email"]] = {
                    "exercises": exercises,
                    "top_lift_by_week": top_by_week,
                }

            await context.close()
            await browser.close()

        return results

    except Exception:
        print("✗ Trainerize scrape_all EXCEPTION:", flush=True)
        traceback.print_exc()
        return {}


if __name__ == "__main__":
    import json
    headless = "--inspect" not in sys.argv
    data = asyncio.run(scrape_all(headless=headless))
    print(json.dumps(data, indent=2))
