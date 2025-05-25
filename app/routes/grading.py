from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List, Dict, Any
import httpx
from datetime import datetime
import traceback
from app.services.canvas_api import (
    fetch_my_canvas_grade,
    fetch_assignment_rubric,
    fetch_user_courses,
    fetch_canvas_assignments,
    analyze_grade_against_rubric
)

from app.routes.canvas import load_grades_cache, save_grades_cache

router = APIRouter()

@router.get("/grades/{course_id}/{assignment_id}/self")
async def get_my_assignment_grade(course_id: int, assignment_id: int):
    """Get only the current user's submission for a specific assignment"""
    try:
        grade = await fetch_my_canvas_grade(course_id, assignment_id)
        return grade
    except httpx.HTTPStatusError as e:
        error_detail = f"Canvas API error: {e.response.status_code} - {e.response.text}"
        raise HTTPException(status_code=e.response.status_code, detail=error_detail)
    except Exception as e:
        error_detail = f"Error fetching grade: {str(e)}"
        raise HTTPException(status_code=500, detail=error_detail)

@router.get("/debug-rubric-assessment/{course_id}/{assignment_id}")
async def debug_rubric_assessment(course_id: int, assignment_id: int):
    """Debug endpoint to test rubric assessment parsing"""
    try:
        # Get your submission with rubric assessment
        submission = await fetch_my_canvas_grade(course_id, assignment_id)
        
        # Get the rubric
        rubric_info = await fetch_assignment_rubric(assignment_id)
        
        # Extract the rubric assessment
        rubric_assessment = submission.get("rubric_assessment", {})
        
        # Return detailed info for debugging
        return {
            "submission_id": submission.get("id"),
            "score": submission.get("score"),
            "rubric": rubric_info.get("rubric"),
            "rubric_assessment": rubric_assessment,
            "rubric_assessment_type": type(rubric_assessment).__name__,
            "assessment_keys": list(rubric_assessment.keys()) if isinstance(rubric_assessment, dict) else None
        }
    except Exception as e:
        error_detail = f"Error debugging rubric assessment: {str(e)}\n{traceback.format_exc()}"
        print(error_detail) 
        raise HTTPException(status_code=500, detail=error_detail)

@router.get("/rubrics/assignment/{assignment_id}")
async def get_assignment_rubric_endpoint(assignment_id: int):
    """Fetch the rubric for a specific assignment"""
    try:
        rubric = await fetch_assignment_rubric(assignment_id)
        return rubric
    except httpx.HTTPStatusError as e:
        error_detail = f"Canvas API error: {e.response.status_code} - {e.response.text}"
        raise HTTPException(status_code=e.response.status_code, detail=error_detail)
    except Exception as e:
        error_detail = f"Error fetching rubric: {str(e)}"
        raise HTTPException(status_code=500, detail=error_detail)

@router.get("/grade-check/{course_id}/{assignment_id}")
async def check_grade_against_rubric_endpoint(course_id: int, assignment_id: int):
    """Compare your grade against the rubric criteria"""
    try:
        # Get your submission
        submission = await fetch_my_canvas_grade(course_id, assignment_id)
        
        # Get the rubric
        rubric_info = await fetch_assignment_rubric(assignment_id)
        
        if not rubric_info.get("rubric"):
            return {
                "status": "no_rubric",
                "message": "No rubric found for this assignment",
                "submission": submission
            }
        
        # Get your rubric assessment if available
        rubric_assessment = submission.get("rubric_assessment", {})
        
        # Compare your grade with expected grade based on rubric
        analysis = analyze_grade_against_rubric(submission, rubric_info, rubric_assessment)
        
        return {
            "status": "completed",
            "submission": submission,
            "rubric": rubric_info,
            "analysis": analysis
        }
    except httpx.HTTPStatusError as e:
        error_detail = f"Canvas API error: {e.response.status_code} - {e.response.text}"
        raise HTTPException(status_code=e.response.status_code, detail=error_detail)
    except Exception as e:
        error_detail = f"Error checking grade: {str(e)}"
        raise HTTPException(status_code=500, detail=error_detail)

async def monitor_grades():
    """Background task to monitor for new or changed grades"""
    print(f"[{datetime.now()}] Running grade monitoring task...")
    
    # Load cached grades
    grades_cache = load_grades_cache()
    
    try:
        # Get all courses
        courses = await fetch_user_courses()
        
        for course in courses:
            course_id = course["id"]
            
            # Get all assignments for this course
            assignments = await fetch_canvas_assignments(course_id)
            
            for assignment in assignments:
                assignment_id = assignment["id"]
                
                # Skip ungraded assignments
                if not assignment.get("has_submitted_submissions", False):
                    continue
                
                # Get your submission
                try:
                    submission = await fetch_my_canvas_grade(course_id, assignment_id)
                    
                    # Skip if not graded
                    if submission.get("workflow_state") != "graded":
                        continue
                    
                    # Check if this is a new grade or grade change
                    cache_key = f"{course_id}_{assignment_id}"
                    cached_submission = grades_cache.get(cache_key)
                    
                    if cached_submission is None:
                        # New grade
                        print(f"New grade for assignment {assignment['name']} in {course['name']}: {submission.get('score')}")
                        
                        # Perform grade check
                        grade_check = await check_grade_against_rubric_endpoint(course_id, assignment_id)
                        
                        if grade_check.get("analysis", {}).get("has_discrepancy", False):
                            print(f"⚠️ Grade discrepancy detected for {assignment['name']}: {grade_check['analysis']['score_difference']} points")
                            
                            # Draft email
                            from app.routes.email import draft_grade_discrepancy_email
                            email = await draft_grade_discrepancy_email(course_id, assignment_id)
                            
                            # Send email
                            from app.routes.email import send_email
                            if email["status"] == "email_drafted":
                                await send_email(email["email"])
                                print(f"📧 Email sent for grade discrepancy in {assignment['name']}")
                            else:
                                print(f"❌ Email drafting failed for {assignment['name']}")
                    
                    elif cached_submission.get("score") != submission.get("score"):
                        # Grade changed
                        print(f"Grade changed for assignment {assignment['name']} in {course['name']}: {cached_submission.get('score')} -> {submission.get('score')}")
                        
                        # Perform grade check
                        grade_check = await check_grade_against_rubric_endpoint(course_id, assignment_id)
                        
                        if grade_check.get("analysis", {}).get("has_discrepancy", False):
                            print(f"⚠️ Grade discrepancy detected for {assignment['name']}: {grade_check['analysis']['score_difference']} points")
                            
                            # Draft email
                            from app.routes.email import draft_grade_discrepancy_email
                            email = await draft_grade_discrepancy_email(course_id, assignment_id)
                            
                            # Send email
                            from app.routes.email import send_email
                            if email["status"] == "email_drafted":
                                await send_email(email["email"])
                                print(f"📧 Email sent for grade discrepancy in {assignment['name']}")
                            else:
                                print(f"❌ Email drafting failed for {assignment['name']}")
                    
                    # Update cache
                    grades_cache[cache_key] = submission
                
                except Exception as e:
                    print(f"Error processing assignment {assignment_id}: {str(e)}")
                    continue
        
        # Save updated cache
        save_grades_cache(grades_cache)
        
    except Exception as e:
        print(f"Error in grade monitoring task: {str(e)}")

@router.get("/test")
async def test_route():
    return {"message": "Grading router is working"}