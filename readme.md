# Animo Backend

FastAPI backend for Animo, deployable as an Azure Functions v2 app.

## Requirements

- Python 3.11
- [Azure Functions Core Tools v4](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local) (for Azure local testing)

## Setup

```bash
# Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy the env template and fill in your secrets
cp .env.template .env
```

## Local Development

### With uvicorn (recommended for fast iteration)

```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`.
Interactive docs at `http://localhost:8000/docs`.

### With Azure Functions Core Tools

```bash
func start
```

The API will be available at `http://localhost:7071`.

## Project Structure

```
animo/
├── api/
│   └── routes/
│       └── health.py       # /health endpoint
├── main.py                 # FastAPI app (uvicorn entry point)
├── function_app.py         # Azure Functions v2 ASGI wrapper
├── host.json               # Azure Functions host config
├── local.settings.json     # Local env vars for Azure Functions
├── requirements.txt
├── .env.template
└── README.md
```

## Endpoints

| Method | Path      | Description        |
|--------|-----------|--------------------|
| GET    | `/`       | Root health check  |
| GET    | `/health` | Health check       |
