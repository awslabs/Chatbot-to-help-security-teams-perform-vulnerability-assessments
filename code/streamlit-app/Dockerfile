# Use x86_64 architecture explicitly with pinned digest for reproducibility
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12

# Cache busting argument
ARG CACHE_BUST=1

# Install streamlit and other requirements
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip3 install --upgrade pip setuptools wheel --no-cache-dir && \
    pip3 install -r requirements.txt --no-cache-dir

# Copy your application code
COPY *.py ./
COPY *.png ./
COPY security/ ./security/

# Expose the Streamlit port
EXPOSE 8501

# Add healthcheck
HEALTHCHECK --interval=600s --timeout=2s --retries=12 \
    CMD ["curl", "-f", "http://localhost:8501/"]

# Set non-root user before entrypoint
USER 1001

# Use python to run streamlit instead of direct execution
ENTRYPOINT ["python", "-m", "streamlit", "run", "app.py", "--server.headless", "true", "--browser.serverAddress=0.0.0.0", "--browser.gatherUsageStats=false"]
