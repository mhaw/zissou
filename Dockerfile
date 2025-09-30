# Stage 1: Builder
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y build-essential

# Set up virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim AS runtime

# Create a non-root user
RUN groupadd -r zissou && useradd -r -g zissou zissou

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set path to use venv
ENV PATH="/opt/venv/bin:$PATH"

# Install ffmpeg for pydub
RUN apt-get update && apt-get install -y ffmpeg
ENV PATH="/usr/bin:$PATH"

# Copy application code
COPY --chown=zissou:zissou . .

# Switch to non-root user
USER zissou

# Set metadata
LABEL maintainer="The Zissou Crew"
LABEL description="Zissou: Turn articles into your personal podcast."

# Expose port and run application
EXPOSE 8080
COPY run.sh .
CMD ["./run.sh"]
