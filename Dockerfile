FROM python:3.12-slim

WORKDIR /app

# Install git (needed for Aider)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Copy source and install
COPY pyproject.toml README.md ./
COPY src/ src/
COPY MEMORY.md .
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "sambot.main:app", "--host", "0.0.0.0", "--port", "8000"]
