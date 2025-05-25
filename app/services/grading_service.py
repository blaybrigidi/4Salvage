from app.services.canvas_api import (
    fetch_user_courses, 
    fetch_canvas_assignments, 
    fetch_my_canvas_grade, 
    fetch_assignment_rubric
)
from app.services.email_service import draft_email_for_discrepancy, send_email
from datetime import datetime
import json
import os

# Store for previous grades to detect changes
GRADES_CACHE_FILE = "grades_cache.json"

def load_grades_cache():
    """Load the cached grades from file"""
    if os.path.exists(GRADES_CACHE_FILE):
        with open(GRADES_CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_grades_cache(cache):
    """Save the grades cache to file"""
    with open(GRADES_CACHE_FILE, 'w') as f:
        json.dump(cache, f)

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
                        grade_check = await check_grade_against_rubric(course_id, assignment_id)
                        
                        if grade_check.get("analysis", {}).get("has_discrepancy", False):
                            print(f"⚠️ Grade discrepancy detected for {assignment['name']}: {grade_check['analysis']['score_difference']} points")
                            
                            # Draft and send email
                            email = await draft_email_for_discrepancy(course_id, assignment_id, grade_check)
                            if email:
                                await send_email(email)
                                print(f"📧 Email sent for grade discrepancy in {assignment['name']}")
                    
                    elif cached_submission.get("score") != submission.get("score"):
                        # Grade changed
                        print(f"Grade changed for assignment {assignment['name']} in {course['name']}: {cached_submission.get('score')} -> {submission.get('score')}")
                        
                        # Perform grade check
                        grade_check = await check_grade_against_rubric(course_id, assignment_id)
                        
                        if grade_check.get("analysis", {}).get("has_discrepancy", False):
                            print(f"⚠️ Grade discrepancy detected for {assignment['name']}: {grade_check['analysis']['score_difference']} points")
                            
                            # Draft and send email
                            email = await draft_email_for_discrepancy(course_id, assignment_id, grade_check)
                            if email:
                                await send_email(email)
                                print(f"📧 Email sent for grade discrepancy in {assignment['name']}")
                    
                    # Update cache
                    grades_cache[cache_key] = submission
                
                except Exception as e:
                    print(f"Error processing assignment {assignment_id}: {str(e)}")
                    continue
        
        # Save updated cache
        save_grades_cache(grades_cache)
        
    except Exception as e:
        print(f"Error in grade monitoring task: {str(e)}")

async def check_grade_against_rubric(course_id: int, assignment_id: int):
    """Compare a grade against the rubric criteria"""
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
    except Exception as e:
        print(f"Error checking grade: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }

def analyze_grade_against_rubric(submission, rubric_info, rubric_assessment):
    """
    Analyze a submission against the rubric criteria
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
