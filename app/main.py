from fastapi import FastAPI
from app.routes import canvas, grading, email
from apscheduler.schedulers.asyncio import AsyncIOScheduler

app = FastAPI(title="Canvas Grade Checker")

# Include routers
app.include_router(canvas.router, prefix="/canvas", tags=["Canvas"])
app.include_router(grading.router, prefix="/grading", tags=["Grading"])
app.include_router(email.router, prefix="/email", tags=["Email"])

# Initialize scheduler
scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(grading.monitor_grades, 'interval', hours=6)
        scheduler.start()
        print("Grade monitoring scheduler started")
    else:
        print("Scheduler already running")

@app.on_event("shutdown")
async def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        print("Grade monitoring scheduler shut down")

@app.get("/")
async def root():
    return {
        "message": "Canvas Grade Checker API",
        "version": "1.0.0"
    }