FROM python:3.11-slim

# Install Chromium and its driver as root — this is what native Python
# runtime on Render does NOT allow, hence the "action not allowed" error.
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    libnss3 \
    libgconf-2-4 \
    libxi6 \
    libxrandr2 \
    libxcomposite1 \
    libxdamage1 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# resume.pdf must be copied in via COPY . . above — make sure it's in your repo root
ENV RESUME_PATH=/app/resume.pdf

EXPOSE 5000

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--timeout", "120"]
