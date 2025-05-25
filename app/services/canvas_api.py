import httpx
from typing import Optional, List, Dict, Any
from app.core.config import canvas_settings

CANVAS_API_BASE = canvas_settings.CANVAS_API_BASE
CANVAS_TOKEN = canvas_settings.CANVAS_TOKEN

async def fetch_canvas_assignments(course_id: int) -> List[Dict[str, Any]]:
    """Fetch assignments for a course with pagination support"""
    base_url = f"{CANVAS_API_BASE}/api/v1/courses/{course_id}/assignments"
    headers = {
        "Authorization": f"Bearer {CANVAS_TOKEN}"
    }
    
    params = {
        "per_page": 100  # Request maximum items per page
    }
    
    all_assignments = []
    url = base_url
    
    async with httpx.AsyncClient() as client:
        while url:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            # Add assignments from current page to our collection
            page_assignments = response.json()
            all_assignments.extend(page_assignments)
            
            # Check if there's a next page
            links = response.headers.get('Link', '')
            url = None
            
            # Parse Link header to find next URL
            for link in links.split(','):
                if 'rel="next"' in link:
                    # Extract URL between < and >
                    url = link.split('<')[1].split('>')[0]
                    # Clear params as they're already in the next URL
                    params = {}
                    break
            
            print(f"Fetched page of assignments. Total so far: {len(all_assignments)}")
    
    return all_assignments

async def get_course_id_by_name(course_name: str) -> Optional[int]:
    """Get a course ID by name"""
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    
    async with httpx.AsyncClient() as client:
        params = {
            "per_page": 100,  # Max courses per page
        }
        response = await client.get(f"{CANVAS_API_BASE}/api/v1/courses", headers=headers, params=params)
        
        print(f"Canvas API status: {response.status_code}")
        
        if response.status_code == 200:
            courses = response.json()
            print(f"Found {len(courses)} courses")
            
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

async def fetch_my_canvas_grade(course_id: int, assignment_id: int):
    """Fetch the current user's submission with rubric assessment"""
    url = f"{CANVAS_API_BASE}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self"
    
    headers = {
        "Authorization": f"Bearer {CANVAS_TOKEN}"
    }
    
    params = {
        "include[]": ["submission_comments", "rubric_assessment"]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        submission = response.json()
        
        # Add percentage calculation if possible
        if submission.get("score") is not None and "points_possible" in submission.get("assignment", {}):
            points_possible = submission["assignment"]["points_possible"]
            if points_possible:
                submission["percentage"] = (submission["score"] / points_possible) * 100
            else:
                submission["percentage"] = None
                
        return submission

async def fetch_assignment_rubric(assignment_id: int):
    """Fetch rubric details for an assignment"""
    url = f"{CANVAS_API_BASE}/api/v1/assignments/{assignment_id}"
    
    headers = {
        "Authorization": f"Bearer {CANVAS_TOKEN}"
    }
    
    params = {
        "include[]": ["rubric", "rubric_settings"]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        assignment = response.json()
        
        if "rubric" in assignment:
            return {
                "assignment_id": assignment_id,
                "assignment_name": assignment.get("name"),
                "rubric": assignment.get("rubric"),
                "rubric_settings": assignment.get("rubric_settings")
            }
        else:
            # Try to fetch via rubric_associations endpoint
            course_id = assignment.get('course_id')
            if not course_id:
                return {
                    "assignment_id": assignment_id,
                    "assignment_name": assignment.get("name"),
                    "rubric": None,
                    "message": "No course_id found for this assignment"
                }
                
            assoc_url = f"{CANVAS_API_BASE}/api/v1/courses/{course_id}/rubric_associations"
            assoc_params = {
                "include[]": ["rubric"],
                "style": "full"
            }
            
            assoc_response = await client.get(assoc_url, headers=headers, params=assoc_params)
            assoc_response.raise_for_status()
            associations = assoc_response.json()
            
            for association in associations:
                if association.get("association_id") == assignment_id and association.get("association_type") == "Assignment":
                    return {
                        "assignment_id": assignment_id,
                        "assignment_name": assignment.get("name"),
                        "rubric": association.get("rubric"),
                        "rubric_settings": association.get("rubric_settings")
                    }
            
            # No rubric found
            return {
                "assignment_id": assignment_id,
                "assignment_name": assignment.get("name"),
                "rubric": None,
                "message": "No rubric attached to this assignment"
            }

async def fetch_course_instructor(course_id: int):
    """Fetch the instructor information for a course"""
    url = f"{CANVAS_API_BASE}/api/v1/courses/{course_id}/users"
    
    headers = {
        "Authorization": f"Bearer {CANVAS_TOKEN}"
    }
    
    params = {
        "enrollment_type[]": "teacher"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            teachers = response.json()
            
            # Just return the first teacher for now
            if teachers:
                return teachers[0]
            else:
                return {"name": "Professor", "email": "", "id": "unknown"}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                # Permission denied - try alternative approach
                print(f"Permission denied for instructor access. Using fallback method.")
                return await fetch_course_instructor_fallback(course_id)
            else:
                # Re-raise other HTTP errors
                raise e
        except Exception as e:
            print(f"Error fetching instructor: {str(e)}")
            return {"name": "Professor", "email": "", "id": "unknown"}

async def fetch_course_instructor_fallback(course_id: int):
    """Fallback method to get instructor info when direct access is denied"""
    try:
        # Try to get course details which might include instructor info
        course_details = await fetch_course_details(course_id)
        
        # Some Canvas instances include teacher info in course details
        if "teachers" in course_details:
            teachers = course_details["teachers"]
            if teachers:
                return teachers[0]
        
        # Try to extract from course name or use generic info
        course_name = course_details.get("name", "Unknown Course")
        
        # Generate a pseudo-instructor ID based on course
        instructor_id = f"instructor_{course_id}"
        
        return {
            "name": f"Instructor ({course_name})",
            "email": f"instructor.{course_id}@institution.edu",
            "id": instructor_id
        }
        
    except Exception as e:
        print(f"Fallback instructor fetch failed: {str(e)}")
        # Return generic instructor info
        return {
            "name": f"Course {course_id} Instructor",
            "email": f"instructor.{course_id}@institution.edu", 
            "id": f"instructor_{course_id}"
        }

async def fetch_course_details(course_id: int):
    """Fetch details for a specific course"""
    url = f"{CANVAS_API_BASE}/api/v1/courses/{course_id}"
    
    headers = {
        "Authorization": f"Bearer {CANVAS_TOKEN}"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

async def fetch_assignment_details(assignment_id: int):
    """Fetch details for a specific assignment"""
    url = f"{CANVAS_API_BASE}/api/v1/assignments/{assignment_id}"
    
    headers = {
        "Authorization": f"Bearer {CANVAS_TOKEN}"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

async def fetch_current_user():
    """Get the current user's information"""
    url = f"{CANVAS_API_BASE}/api/v1/users/self"
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

async def fetch_user_courses():
    """Fetch all courses for the current user"""
    url = f"{CANVAS_API_BASE}/api/v1/courses"
    
    headers = {
        "Authorization": f"Bearer {CANVAS_TOKEN}"
    }
    
    params = {
        "enrollment_state": "active",
        "include[]": ["term"]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    
def analyze_grade_against_rubric(submission, rubric_info, rubric_assessment):
    """
    Analyze a submission against the rubric criteria
    
    This function implements the core grade-checking logic
    """
    # Get actual score and possible points
    actual_score = submission.get("score", 0)
    points_possible = submission.get("assignment", {}).get("points_possible", 0)
    
    # Get rubric criteria and points
    rubric = rubric_info.get("rubric", [])
    
    if not rubric:
        return {
            "status": "no_rubric_data",
            "message": "Unable to analyze without rubric data"
        }
    
    # Calculate what the score should be based on rubric assessments
    calculated_score = 0
    criteria_analysis = []
    
    for criterion in rubric:
        criterion_id = criterion.get("id")
        criterion_points = criterion.get("points")
        criterion_description = criterion.get("description")
        
        # Get the assessment for this criterion
        criterion_assessment = rubric_assessment.get(criterion_id, {})
        
        # Get points awarded from the assessment
        awarded_points = criterion_assessment.get("points", 0)
        
        # Get the rating_id from the assessment
        rating_id = criterion_assessment.get("rating_id")
        
        # Find the rating details
        rating_description = None
        expected_points = None
        for rating in criterion.get("ratings", []):
            if rating.get("id") == rating_id:
                rating_description = rating.get("description")
                expected_points = rating.get("points")
                break
        
        # Add to calculated score
        calculated_score += awarded_points
        
        # Check if there's a discrepancy for this criterion
        criterion_discrepancy = False
        discrepancy_reason = None
        
        if expected_points is not None and abs(expected_points - awarded_points) > 0.01:
            criterion_discrepancy = True
            discrepancy_reason = f"Rating '{rating_description}' should be worth {expected_points} points, but {awarded_points} were awarded"
        
        # Add to criteria analysis
        criteria_analysis.append({
            "criterion_id": criterion_id,
            "description": criterion_description,
            "possible_points": criterion_points,
            "points_awarded": awarded_points,
            "rating_id": rating_id,
            "rating_description": rating_description,
            "expected_points": expected_points,
            "has_discrepancy": criterion_discrepancy,
            "discrepancy_reason": discrepancy_reason,
            "comments": criterion_assessment.get("comments")
        })
    
    # Calculate the difference
    score_difference = abs(calculated_score - actual_score)
    
    # Determine if there's a discrepancy (using a small threshold to account for rounding)
    has_discrepancy = score_difference > 0.01
    
    # Count how many criteria have discrepancies
    criteria_with_discrepancies = sum(1 for c in criteria_analysis if c.get("has_discrepancy"))
    
    return {
        "status": "analysis_complete",
        "actual_score": actual_score,
        "calculated_score": calculated_score,
        "score_difference": score_difference,
        "has_discrepancy": has_discrepancy,
        "criteria_analysis": criteria_analysis,
        "criteria_with_discrepancies": criteria_with_discrepancies,
        "message": "Grade appears correct" if not has_discrepancy else f"Possible grade discrepancy of {score_difference} points"
    }