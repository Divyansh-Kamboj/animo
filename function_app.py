import azure.functions as func
from main import app

# Wraps the FastAPI ASGI app as an Azure Functions v2 app.
# All routes defined in main.py are automatically served.
function_app = func.AsgiFunctionApp(app=app, http_auth_level=func.AuthLevel.ANONYMOUS)
