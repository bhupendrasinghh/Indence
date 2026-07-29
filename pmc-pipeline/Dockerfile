# Use official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860

# Set working directory
WORKDIR /app

# Install system dependencies (lxml and sqlite requirements)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# Create user with UID 1000 and create necessary folders with correct permissions
RUN useradd -m -u 1000 user && \
    mkdir -p /app/data && \
    chown -R user:user /app

# Switch to non-root user for security and Hugging Face compatibility
USER user

# Copy dependency list and install them
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Add user local bin to PATH (for packages installed with --user)
ENV PATH="/home/user/.local/bin:${PATH}"

# Copy the rest of the application code
COPY --chown=user . .

# Copy config.yaml.example to config.yaml to ensure a default configuration is present
RUN cp config.yaml.example config.yaml

# Expose the default Hugging Face port
EXPOSE 7860

# Start the dashboard application
CMD ["python", "dashboard/app.py"]
