"""
generator.py — renders the Jinja2 HTML template and converts to PDF using Playwright.
"""
import asyncio
import json
import os
from pathlib import Path
from datetime import date
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

TEMPLATE_DIR = Path(__file__).parent
TEMPLATE_FILE = "template.html"


async def render_pdf(client_data: dict, output_path: str) -> str:
    """
    client_data keys:
      client_first_name, client_last_name, client_email
      report_period         e.g. "May – June 2025"
      sessions_attended     int
      sessions_scheduled    int
      consistency_pct       int  (0–100)
      current_streak        int  (weeks)
      exercises             list of {name, best, week_labels, values}
      session_rows          list of {week_number, date, attended, top_lift, trend, trend_val}
      motivational_note     str
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    tmpl = env.get_template(TEMPLATE_FILE)

    html = tmpl.render(
        client_first_name=client_data["client_first_name"],
        client_last_name=client_data["client_last_name"],
        report_period=client_data["report_period"],
        sessions_attended=client_data["sessions_attended"],
        sessions_scheduled=client_data["sessions_scheduled"],
        consistency_pct=client_data["consistency_pct"],
        current_streak=client_data["current_streak"],
        motivational_note=client_data["motivational_note"],
        generated_date=date.today().strftime("%d %B %Y"),
        exercises_json=json.dumps(client_data["exercises"]),
        session_rows_json=json.dumps(client_data["session_rows"]),
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        await page.pdf(
            path=output_path,
            format="A4",
            print_background=True,
            margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"},
        )
        await browser.close()

    return output_path


def build_motivational_note(client_data: dict) -> str:
    """Generate a short motivational paragraph based on stats."""
    name = client_data["client_first_name"]
    pct = client_data["consistency_pct"]
    streak = client_data["current_streak"]

    if pct == 100:
        opening = f"<strong>Perfect attendance, {name} — not a single session missed.</strong>"
    elif pct >= 80:
        opening = f"<strong>Strong work, {name}.</strong> {pct}% consistency is exactly the kind of reliability that compounds into real results."
    else:
        opening = f"<strong>Keep at it, {name}.</strong> Life happens — the important thing is you keep coming back."

    if streak >= 4:
        streak_line = f" You're currently on a <strong>{streak}-week streak</strong>, which is fantastic momentum."
    elif streak >= 2:
        streak_line = f" A <strong>{streak}-week streak</strong> in progress — let's keep it going."
    else:
        streak_line = " Let's build that streak back up this week."

    return (
        opening + streak_line +
        " The progress charts above show your lifts trending in the right direction. "
        "Stay consistent, trust the process, and I'll see you in the gym. — <strong>Timmo</strong>"
    )
