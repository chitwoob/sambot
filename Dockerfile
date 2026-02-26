FROM python:3.12-slim

WORKDIR /app

# Install git (needed for Aider)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source
COPY src/ src/
COPY MEMORY.md .

EXPOSE 8000

CMD ["uvicorn", "sambot.main:app", "--host", "0.0.0.0", "--port", "8000"]
