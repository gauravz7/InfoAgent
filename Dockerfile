# Container for the digest pipeline, run as a Cloud Run Job (daily via Scheduler).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
# Lean runtime: the headless pipeline needs only these three.
RUN pip install --no-cache-dir google-genai requests pillow

# Vertex/Gemini auth is native ADC from the attached service account.
ENTRYPOINT ["bash", "run_job.sh"]
