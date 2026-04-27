import uvicorn
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from routes import triage_router

load_dotenv()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Wipro Client",
        version='1.0'
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(triage_router.router)

    return app

app = create_app()

@app.get("/")
async def welcome_user():
    return {"message": "Wipro FastAPI App"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
