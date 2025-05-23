import httpx
from typing import Optional
from config.settings import CANVAS_TOKEN

CANVAS_API_BASE = "https://ashesi.instructure.com"

async def fetch_canvas_assignments(course_id: int):
    url = f"{CANVAS_API_BASE}/api/v1/courses/{course_id}/assignments"
    headers = {
        "Authorization": f"Bearer {CANVAS_TOKEN}"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

async def get_course_id_by_name(course_name: str) -> Optional[int]:
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    
    async with httpx.AsyncClient() as client:
        # Remove enrollment_state filter to get ALL courses
        params = {
            "per_page": 100,  # Max courses per page
        }
        response = await client.get(f"{CANVAS_API_BASE}/api/v1/courses", headers=headers, params=params)
        
        print(f"Canvas API status: {response.status_code}")
        print(f"Canvas API response length: {len(response.text)}")
        
        if response.status_code == 200:
            courses = response.json()
            print(f"Found {len(courses)} courses")
            
            # Debug: Print all course names to help identify the issue
            for course in courses:
                print(f"Available course: '{course.get('name')}' (ID: {course.get('id')})")
            
            # Try exact match first
            for course in courses:
                if course.get("name", "").lower() == course_name.lower():
                    print(f"Exact match found: '{course.get('name')}'")
                    return course["id"]
            
            # Try partial match as fallback
            for course in courses:
                if course_name.lower() in course.get("name", "").lower():
                    print(f"Partial match found: '{course.get('name')}'")
                    return course["id"]
            
            print(f"No course found matching: '{course_name}'")
            return None
        else:
            print(f"Canvas API error: {response.status_code} - {response.text}")
            return None