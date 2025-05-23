from fastapi import FastAPI
from app.routes import canvas

app = FastAPI()
app.include_router(canvas.router, prefix="/canvas", tags=["Canvas"])
@app.get("/")
def read_root():
    return {"message": "AutoGrader API is working!"}

