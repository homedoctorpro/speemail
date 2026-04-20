FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[standard]" 2>/dev/null || pip install --no-cache-dir \
    "fastapi>=0.115.0,<0.116" \
    "uvicorn[standard]>=0.32.0,<0.33" \
    "jinja2>=3.1.4,<3.2" \
    "python-multipart>=0.0.12,<0.1" \
    "msal>=1.31.0,<2.0" \
    "httpx>=0.28.0,<0.29" \
    "sqlalchemy>=2.0.36,<2.1" \
    "alembic>=1.14.0,<1.15" \
    "anthropic>=0.40.0,<1.0" \
    "apscheduler>=3.10.4,<4.0" \
    "pydantic-settings>=2.6.0,<2.7" \
    "python-dotenv>=1.0.1,<1.1"

# Copy source
COPY . .

# Install the package itself
RUN pip install --no-cache-dir -e .

# data/ is mounted as a Fly volume — create it so local runs work too
RUN mkdir -p data

EXPOSE 8765

ENV SERVER_MODE=true \
    PORT=8765

CMD ["python", "-m", "speemail"]
