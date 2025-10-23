# Stage 1: Builder
FROM python:3.11.9-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

# Set up virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV GRPC_VERBOSITY=ERROR

# Upgrade pip
RUN pip install --upgrade pip

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app app

# Run import check as a build step
RUN python app/main.py --check-imports

# Stage 2: Runtime
FROM python:3.11.9-slim AS runtime

# Create a non-root user
RUN groupadd -r zissou && useradd -r -g zissou zissou

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set path to use venv
ENV PATH="/opt/venv/bin:$PATH"

# Install ffmpeg for pydub and curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl && rm -rf /var/lib/apt/lists/*

# Copy application code from builder stage
COPY --from=builder --chown=zissou:zissou /app .

# Switch to non-root user
USER zissou

# Set metadata
LABEL maintainer="The Zissou Crew"
LABEL description="Zissou: Turn articles into your personal podcast."

# Expose port and run application
EXPOSE 8080
HEALTHCHECK CMD curl -f http://localhost:8080/healthz || exit 1
COPY run.sh .
CMD ["./run.sh"]
