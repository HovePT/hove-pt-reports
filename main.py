"""
main.py — orchestrates the full weekly report pipeline.

Run manually:    python main.py
Run for one client: python main.py --client client@email.com
Dry run (no email):  python main.py --dry-run
"""
import asyncio
import argparse
import os
import tempfile
from datetime import date, timedelta

from scrapers.fresha    import scrape_all as fresha_scrape
from scrapers.trainerize import scrape_all as trainerize_scrape
from reports.generator  import render_pdf, build_motivational_note
from email_sender       import send_report


def get_report_period() -> str:
    today = date.today()
    start = today - timedelta(days=28)
    if start.month == today.month:
        return f"{start.strftime('%-d')}–{today.strftime('%-d %B %Y')}"
    return f"{start.strftime('%-d %B')} – {today.strftime('%-d %B %Y')}"


async def run(only_email: str | None = None, dry_run: bool = False):
    period = get_report_period()
    print(f"\n=== Hove PT Reports · {period} ===\n")

    # 1. Scrape both platforms
    print("Scraping Fresha…")
    fresha_data = await fresha_scrape(headless=True)

    print("Scraping Trainerize…")
    trainerize_data = await trainerize_scrape(headless=True)

    # 2. Merge and generate PDFs
    for client in fresha_data:
        email = client["email"]

        if only_email and email != only_email:
            continue

        t_data = trainerize_data.get(email, {})
        exercises    = t_data.get("exercises", [])
        top_by_week  = t_data.get("top_lift_by_week", ["—", "—", "—", "—"])

        # Merge top lift into session rows
        for i, row in enumerate(client["session_rows"]):
            row["top_lift"] = top_by_week[i] if i < len(top_by_week) else "—"

        first, *rest = client["name"].split(" ", 1)
        last = rest[0] if rest else ""

        client_data = {
            "client_first_name": first,
            "client_last_name":  last,
            "client_email":      email,
            "report_period":     period,
            "sessions_attended": client["sessions_attended"],
            "sessions_scheduled": client["sessions_scheduled"],
            "consistency_pct":   client["consistency_pct"],
            "current_streak":    client["current_streak"],
            "exercises":         exercises,
            "session_rows":      client["session_rows"],
            "motivational_note": "",  # filled below
        }
        client_data["motivational_note"] = build_motivational_note(client_data)

        # 3. Render PDF
        safe_name = f"{first}_{last}".replace(" ", "_")
        pdf_name  = f"progress_{safe_name}_{date.today().isoformat()}.pdf"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name

        await render_pdf(client_data, pdf_path)
        print(f"✓ PDF: {pdf_name}")

        # 4. Email
        if dry_run:
            print(f"  [dry-run] Would email {email}")
        else:
            send_report(
                to_email=email,
                client_first_name=first,
                pdf_path=pdf_path,
                report_period=period,
            )

        os.unlink(pdf_path)

    print("\n✓ All done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", help="Only process this client email")
    parser.add_argument("--dry-run", action="store_true", help="Generate PDFs but don't send emails")
    args = parser.parse_args()

    asyncio.run(run(only_email=args.client, dry_run=args.dry_run))
