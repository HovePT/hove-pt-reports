FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt --break-system-packages
RUN playwright install chromium

COPY . .

CMD ["python", "main.py"]
