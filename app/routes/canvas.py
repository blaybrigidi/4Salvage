from fastapi import APIRouter, HTTPException, Query
from app.services.canvas_api import get_course_id_by_name, fetch_canvas_assignments

router = APIRouter()

@router.get("/course-id")
async def get_course_id(course_name: str = Query(..., description="The name of the course")):
    try:
        course_id = await get_course_id_by_name(course_name)
        if course_id is None:
            raise HTTPException(status_code=404, detail="Course not found")
        return {"course_name": course_name, "course_id": course_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/assignments/{course_id}")
async def get_assignments_for_course(course_name: str):
    course_id = await get_course_id_by_name(course_name)
    if course_id is None:
        print(f"Course '{course_name}' not found.")
        return []
    
    assignments = await fetch_canvas_assignments(course_id)
    print(f"Found {len(assignments)} assignments in '{course_name}':")
    
    for assignment in assignments:
        print(f" - {assignment['name']} (Due: {assignment.get('due_at')})")

    return assignments

