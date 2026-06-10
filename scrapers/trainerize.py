"""
trainerize.py — logs into Trainerize and extracts the last 4 weeks of
exercise logs for every client.
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
    await page.fill('input[name="email"]', os.environ["TRAINERIZE_EMAIL"])
    await page.fill('input[name="password"]', os.environ["TRAINERIZE_PASSWORD"])
    await page.click('button[type="submit"]')
    await page.wait_for_load_state("networkidle")
    print("✓ Trainerize: logged in")

async def get_clients(page: Page) -> list[dict]:
    await page.goto(TRAINERIZE_URL + "/clients")
    await page.wait_for_load_state("networkidle")
    clients = []
    rows = await page.query_selector_all(".client-list-item")
    for row in rows:
        name_el  = await row.query_selector(".client-name")
        email_el = await row.query_selector(".client-email")
        link_el  = await row.query_selector("a")
        name  = (await name_el.inner_text()).strip()  if name_el  else ""
        email = (await email_el.inner_text()).strip() if email_el else ""
        href  = await link_el.get_attribute("href")   if link_el  else ""
        client_id = href.split("/")[-1] if href else ""
        if name: clients.append({"name": name, "email": email, "client_id": client_id})
    print(f"✓ Trainerize: found {len(clients)} clients")
    return clients

async def get_workout_logs(page: Page, client_id: str) -> list[dict]:
    cutoff = date.today() - timedelta(days=28)
    await page.goto(f"{TRAINERIZE_URL}/clients/{client_id}/workouts/log")
    await page.wait_for_load_state("networkidle")
    logs = []
    workout_blocks = await page.query_selector_all(".workout-log-entry")
    for block in workout_blocks:
        date_el = await block.query_selector(".log-date")
        raw_date = (await date_el.inner_text()).strip() if date_el else ""
        try: log_date = datetime.strptime(raw_date, "%d %b %Y").date()
        except ValueError: continue
        if log_date < cutoff: continue
        ex_blocks = await block.query_selector_all(".exercise-entry")
        for ex_block in ex_blocks:
            name_el = await ex_block.query_selector(".exercise-name")
            ex_name = (await name_el.inner_text()).strip() if name_el else "Unknown"
            set_els = await ex_block.query_selector_all(".set-row")
            sets = []
            for set_el in set_els:
                weight_el = await set_el.query_selector(".weight")
                reps_el   = await set_el.query_selector(".reps")
                try:
                    weight = float((await weight_el.inner_text()).replace("kg","").strip()) if weight_el else 0
                    reps   = int((await reps_el.inner_text()).strip()) if reps_el else 0
                except: weight, reps = 0, 0
                sets.append({"weight": weight, "reps": reps})
            logs.append({"date": log_date.isoformat(), "exercise_name": ex_name, "sets": sets})
    return logs

def summarise_exercises(logs: list[dict], top_n: int = 4) -> list[dict]:
    today = date.today()
    weeks = []
    for w in range(4):
        ws = today - timedelta(days=today.weekday() + 7 * (3 - w))
        weeks.append((ws, ws + timedelta(days=6)))
    week_labels = [ws.strftime("%-d %b") for ws, _ in weeks]
    freq = defaultdict(int)
    for log in logs: freq[log["exercise_name"]] += 1
    result = []
    for ex in sorted(freq, key=lambda x: -freq[x])[:top_n]:
        ex_logs = [l for l in logs if l["exercise_name"] == ex]
        week_bests = []
        for ws, we in weeks:
            week_logs = [l for l in ex_logs if ws <= date.fromisoformat(l["date"]) <= we]
            best = max((max((s["weight"] for s in l["sets"]), default=0) for l in week_logs), default=0.0)
            week_bests.append(best)
        overall_best = max(week_bests) if week_bests else 0
        result.append({"name": ez, "best": f"{overall_best:.1f} kg" if overall_best else "—", "week_labels": week_labels, "values": week_bests})
    return result

async def scrape_all(headless: bool = True) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page    = await browser.new_page()
        await login(page)
        clients = await get_clients(page)
        results = {}
        for client in clients:
            logs = await get_workout_logs(page, client["client_id"])
            exercises = summarise_exercises(logs)
            top_by_week = []
            for ws, we in [(date.today()-timedelta(days=date.today().weekday()+7*(3-w)), date.today()-timedelta(days=date.today().weekday()+7(3*w))+timedelta(days=6)) for w in range(4)]:
                wl = [l for l in logs if ws <= date.fromisoformat(l["date"]) <= we]
                if wl: top_by_week.append(max(wl, key=lambda l: max((s["weight"] for s in l["sets"]), default=0))["exercise_name"])
                else: top_by_week.append("—")
            results[client["email"]] = {"exercises": exercises, "top_lift_by_week": top_by_week}
        await browser.close()
    return results

if __name__ == "__main__":
    import sys, json
    print(json.dumps(asyncio.run(scrape_all("--inspect" not in sys.argv)), indent=2))
