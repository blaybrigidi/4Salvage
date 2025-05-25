from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.services.grading_service import monitor_grades

# Initialize scheduler
scheduler = AsyncIOScheduler()

def setup_scheduler():
    """Configure and initialize the scheduler"""
    if not scheduler.running:
        # Add jobs
        scheduler.add_job(monitor_grades, 'interval', hours=6)
        
        # Start scheduler
        scheduler.start()
        print("Grade monitoring scheduler started")
    else:
        print("Scheduler already running")

def shutdown_scheduler():
    """Shut down the scheduler"""
    if scheduler.running:
        scheduler.shutdown()
        print("Grade monitoring scheduler shut down")