"""
email_sender.py — sends a branded PDF report to a client via Gmail SMTP.
Uses an App Password (not your main Gmail password).
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path


GMAIL_ADDRESS  = os.environ["GMAIL_ADDRESS"]   # e.g. timmoquantickpt@gmail.com
GMAIL_APP_PASS = os.environ["GMAIL_APP_PASS"]  # 16-char App Password from Google


EMAIL_HTML_TEMPLATE = """\
<html>
<body style="font-family: 'Helvetica Neue', Arial, sans-serif; background:#f0f2f5; margin:0; padding:0;">
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:12px; overflow:hidden;">
    <div style="background:#071a2f; padding:28px 36px; display:flex; align-items:center; gap:12px;">
      <img src="https://personaltrainerhove.co.uk/assets/original-hove-personal-training-logo.png"
           height="44" style="filter:brightness(0) invert(1);" alt="Hove PT">
      <div>
        <div style="color:#fff; font-size:16px; font-weight:700;">Hove Personal Training</div>
        <div style="color:#c9a84c; font-size:11px; text-transform:uppercase; letter-spacing:1px;">Brighton &amp; Hove · Est. 2012</div>
      </div>
    </div>
    <div style="padding:28px 36px;">
      <p style="font-size:15px; color:#071a2f; margin-bottom:14px;">Hi {first_name},</p>
      <p style="font-size:14px; color:#3a4a5a; line-height:1.7; margin-bottom:18px;">
        Your weekly progress report is attached. It covers the last 4 weeks of training —
        your sessions attended, consistency score, and how your key lifts have been
        progressing. Open it up and have a look at how far you've come.
      </p>
      <p style="font-size:14px; color:#3a4a5a; line-height:1.7; margin-bottom:24px;">
        As always, any questions just reply to this email or drop me a message.
        See you in the gym.
      </p>
      <p style="font-size:14px; color:#071a2f; font-weight:600;">— Timmo</p>
    </div>
    <div style="background:#071a2f; padding:16px 36px; font-size:11px; color:#4a6a8a;">
      Hove Personal Training · New Church Road, Hove ·
      <a href="https://personaltrainerhove.co.uk" style="color:#c9a84c;">personaltrainerhove.co.uk</a>
    </div>
  </div>
</body>
</html>
"""


def send_report(
    to_email: str,
    client_first_name: str,
    pdf_path: str,
    report_period: str,
) -> None:
    subject = f"Your Progress Report – {report_period} | Hove Personal Training"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Timmo | Hove Personal Training <{GMAIL_ADDRESS}>"
    msg["To"]      = to_email

    html_body = EMAIL_HTML_TEMPLATE.format(first_name=client_first_name)
    msg.attach(MIMEText(html_body, "html"))

    # Attach PDF
    pdf_bytes = Path(pdf_path).read_bytes()
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(pdf_bytes)
    encoders.encode_base64(attachment)
    pdf_filename = Path(pdf_path).name
    attachment.add_header("Content-Disposition", f'attachment; filename="{pdf_filename}"')
    msg.attach(attachment)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        server.sendmail(GMAIL_ADDRESS, to_email, msg.as_string())

    print(f"✓ Email sent → {to_email}")
