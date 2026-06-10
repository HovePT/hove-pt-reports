"""
trainerize.py — logs into Trainerize and extracts the last 4 weeks of
exercise logs for every client.

NOTE: Selectors marked # [SELECTOR] need verifying against the live UI.
"""
import asyncio
import os
from datetime import date, timedelta, datetime
from collections import defaultdict
from playwright.async_api import async_playwright, Page

TRAINERIZE_URL = "https://app.trainerize.com"


async def login(page: Page) -> None:
    await page.goto(TRAINERIZE_URL + "/login")
    await page.wait_for_load_state("networkidle")

    await page.fill('input[name="email"]',    os.environ["TRAINERIZE_EMAIL"])    # [SELECTOR]
    await page.fill('input[name="password"]', os.environ["TRAINERIZE_PASSWORD"]) # [SELECTOR]
    await page.click('button[type="submit"]')                                      # [SELECTOR]
    await page.wait_for_load_state("networkidle")
    print("✓ Trainerize: logged in")


async def get_clients(page: Page) -> list[dict]:
    """Return list of {name, email, client_id}."""
    await page.goto(TRAINERIZE_URL + "/clients")
    await page.wait_for_load_state("networkidle")

    clients = []
    # TODO: update after inspecting the live Clients page
    rows = await page.query_selector_all(".client-list-item")  # [SELECTOR]
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

    print(f"✓ Trainerize: found {len(clients)} clients")
    return clients


async def get_workout_logs(page: Page, client_id: str) -> list[dict]:
    """
    Returns raw workout log entries for the last 28 days.
    Each entry: {date, exercise_name, set_data: [{weight, reps}]}
    """
    cutoff = date.today() - timedelta(days=28)
    url = f"{TRAINERIZE_URL}/clients/{client_id}/workouts/log"  # [URL — verify]
    await page.goto(url)
    await page.wait_for_load_state("networkidle")

    logs = []
    # TODO: update selectors after inspecting the workout log page
    workout_blocks = await page.query_selector_all(".workout-log-entry")  # [SELECTOR]
    for block in workout_blocks:
        date_el = await block.query_selector(".log-date")  # [SELECTOR]
        raw_date = (await date_el.inner_text()).strip() if date_el else ""

        try:
            log_date = datetime.strptime(raw_date, "%d %b %Y").date()
        except ValueError:
            continue

        if log_date < cutoff:
            continue

        exercise_blocks = await block.query_selector_all(".exercise-entry")  # [SELECTOR]
        for ex_block in exercise_blocks:
            name_el = await ex_block.query_selector(".exercise-name")  # [SELECTOR]
            ex_name = (await name_el.inner_text()).strip() if name_el else "Unknown"

            set_els = await ex_block.query_selector_all(".set-row")    # [SELECTOR]
            sets = []
            for set_el in set_els:
                weight_el = await set_el.query_selector(".weight")     # [SELECTOR]
                reps_el   = await set_el.query_selector(".reps")       # [SELECTOR]
                try:
                    weight = float((await weight_el.inner_text()).replace("kg","").strip()) if weight_el else 0
                    reps   = int((await reps_el.inner_text()).strip())                       if reps_el   else 0
                except (ValueError, AttributeError):
                    weight, reps = 0, 0
                sets.append({"weight": weight, "reps": reps})

            logs.append({"date": log_date.isoformat(), "exercise_name": ex_name, "sets": sets})

    return logs


def summarise_exercises(logs: list[dict], top_n: int = 4) -> list[dict]:
    """
    From raw logs, pick the top_n most-logged exercises and build
    week-by-week best-set data for the chart.
    Returns list of {name, best, week_labels, values}
    """
    today = date.today()
    # 4 week buckets
    weeks = []
    for w in range(4):
        ws = today - timedelta(days=today.weekday() + 7 * (3 - w))
        we = ws + timedelta(days=6)
        weeks.append((ws, we))

    week_labels = [ws.strftime("%-d %b") for ws, _ in weeks]

    # Count frequency per exercise
    freq = defaultdict(int)
    for log in logs:
        freq[log["exercise_name"]] += 1

    top_exercises = sorted(freq, key=lambda x: -freq[x])[:top_n]

    result = []
    for ex in top_exercises:
        ex_logs = [l for l in logs if l["exercise_name"] == ex]

        # Best set per week = max weight lifted (with at least 1 rep)
        week_bests = []
        for ws, we in weeks:
            week_logs = [
                l for l in ex_logs
                if ws <= date.fromisoformat(l["date"]) <= we
            ]
            best = 0.0
            for log in week_logs:
                for s in log["sets"]:
                    if s["reps"] > 0 and s["weight"] > best:
                        best = s["weight"]
            week_bests.append(best)

        overall_best = max(week_bests) if week_bests else 0
        best_str = f"{overall_best:.1f} kg" if overall_best else "—"

        result.append({
            "name": ex,
            "best": best_str,
            "week_labels": week_labels,
            "values": week_bests,
        })

    return result


async def scrape_all(headless: bool = True) -> dict:
    """
    Returns dict keyed by client email:
      {email: {exercises: [...], top_lift_by_week: [...]}}
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page    = await browser.new_page()

        await login(page)
        clients = await get_clients(page)

        results = {}
        for client in clients:
            logs      = await get_workout_logs(page, client["client_id"])
            exercises = summarise_exercises(logs)

            # top lift per week (for Fresha session table)
            top_by_week = []
            for i, (ws, we) in enumerate([
                (date.today() - timedelta(days=date.today().weekday() + 7*(3-w)),
                 date.today() - timedelta(days=date.today().weekday() + 7*(3-w)) + timedelta(days=6))
                for w in range(4)
            ]):
                week_logs = [l for l in logs if ws <= date.fromisoformat(l["date"]) <= we]
                if week_logs:
                    best_ex = max(
                        week_logs,
                        key=lambda l: max((s["weight"] for s in l["sets"]), default=0)
                    )
                    top_by_week.append(best_ex["exercise_name"])
                else:
                    top_by_week.append("—")

            results[client["email"]] = {
                "exercises": exercises,
                "top_lift_by_week": top_by_week,
            }

        await browser.close()

    return results


if __name__ == "__main__":
    import sys, json
    headless = "--inspect" not in sys.argv
    data = asyncio.run(scrape_all(headless=headless))
    print(json.dumps(data, indent=2))
