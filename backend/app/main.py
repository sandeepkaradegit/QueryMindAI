from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_routes import router as admin_router
from app.api.auth_routes import router as auth_router
from app.api.routes import health_router, router
from app.auth import get_current_user
from app.config import get_frontend_origin
from app.observability import RequestTrace, reset_current_trace, set_current_trace


app = FastAPI(
    title="QueryMind AI API",
    version="0.1.0",
    description="Natural-language analytics API foundation.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_frontend_origin()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(router, dependencies=[Depends(get_current_user)])
app.include_router(admin_router, dependencies=[Depends(get_current_user)])


@app.middleware("http")
async def trace_request(request: Request, call_next):
    trace = RequestTrace(request.method, request.url.path)
    context_token = set_current_trace(trace)
    try:
        response = await call_next(request)
    except Exception as error:
        trace.close(status_code=500, error=type(error).__name__)
        raise
    finally:
        reset_current_trace(context_token)

    response.headers["X-QueryMind-Trace-Id"] = str(trace.trace_id)
    trace.close(status_code=response.status_code)
    return response