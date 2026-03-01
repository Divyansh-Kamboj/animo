from fastapi import FastAPI
from api.routes import health

app = FastAPI(title="Animo API", version="0.1.0")

app.include_router(health.router)


@app.get("/")
def root():
    return {"message": "Animo API is running"}
