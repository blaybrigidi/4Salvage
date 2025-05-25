from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from typing import List, Dict, Any
import httpx
from app.services.canvas_api import *
from pydantic_settings import BaseSettings
import json
import os
from datetime import datetime

router = APIRouter()

# Define cache file path
GRADES_CACHE_FILE = "grades_cache.json"

class EmailSettings(BaseSettings):
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    EMAIL_SENDER: str = ""
    EMAIL_PASSWORD: str = ""
    CANVAS_TOKEN: str = ""
    
    class Config:
        env_file = ".env"
        extra = "allow"

email_settings = EmailSettings()

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
async def get_assignments_for_course(course_id: int):
    if course_id is None:
        print(f"Course ID '{course_id}' not valid.")
        return []
    assignments = await fetch_canvas_assignments(course_id)
    print(f"Found {len(assignments)} assignments for course ID '{course_id}':")
    for assignment in assignments:
        print(f" - {assignment['name']} (Due: {assignment.get('due_at')})")
    return assignments

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

@router.get("/monitor-grades-now")
async def trigger_grade_monitoring(background_tasks: BackgroundTasks):
    """Manually trigger the grade monitoring task"""
    from app.routes.grading import monitor_grades
    background_tasks.add_task(monitor_grades)
    return {"status": "Grade monitoring task started"}

@router.get("/assignments-with-rubrics/{course_id}")
async def get_assignments_with_rubrics(course_id: int):
    """Get all assignments for a course and check which ones have rubrics"""
    try:
        # Get all assignments for the course
        assignments = await fetch_canvas_assignments(course_id)
        
        assignments_with_rubric_status = []
        
        for assignment in assignments:
            assignment_id = assignment["id"]
            assignment_name = assignment["name"]
            due_date = assignment.get("due_at")
            points_possible = assignment.get("points_possible")
            
            # Check if this assignment has a rubric
            try:
                rubric_info = await fetch_assignment_rubric(assignment_id)
                has_rubric = rubric_info.get("rubric") is not None
                rubric_criteria_count = len(rubric_info.get("rubric", [])) if has_rubric else 0
                
                assignments_with_rubric_status.append({
                    "assignment_id": assignment_id,
                    "assignment_name": assignment_name,
                    "due_date": due_date,
                    "points_possible": points_possible,
                    "has_rubric": has_rubric,
                    "rubric_criteria_count": rubric_criteria_count,
                    "rubric_message": rubric_info.get("message", "Rubric found") if not has_rubric else "Rubric available"
                })
                
            except Exception as e:
                assignments_with_rubric_status.append({
                    "assignment_id": assignment_id,
                    "assignment_name": assignment_name,
                    "due_date": due_date,
                    "points_possible": points_possible,
                    "has_rubric": False,
                    "rubric_criteria_count": 0,
                    "rubric_message": f"Error checking rubric: {str(e)}"
                })
        
        # Summary statistics
        total_assignments = len(assignments_with_rubric_status)
        assignments_with_rubrics = sum(1 for a in assignments_with_rubric_status if a["has_rubric"])
        assignments_without_rubrics = total_assignments - assignments_with_rubrics
        
        return {
            "course_id": course_id,
            "summary": {
                "total_assignments": total_assignments,
                "assignments_with_rubrics": assignments_with_rubrics,
                "assignments_without_rubrics": assignments_without_rubrics,
                "percentage_with_rubrics": round((assignments_with_rubrics / total_assignments * 100), 1) if total_assignments > 0 else 0
            },
            "assignments": assignments_with_rubric_status
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching assignments with rubrics: {str(e)}")

@router.get("/analyze-non-rubric-assignment/{course_id}/{assignment_id}")
async def analyze_non_rubric_assignment(course_id: int, assignment_id: int):
    """Analyze an assignment without a rubric using alternative methods"""
    try:
        # Get submission details
        submission = await fetch_my_canvas_grade(course_id, assignment_id)
        
        # Get assignment details
        assignment = await fetch_assignment_details(assignment_id)
        
        analysis = {
            "assignment_id": assignment_id,
            "assignment_name": assignment.get("name"),
            "your_score": submission.get("score"),
            "points_possible": assignment.get("points_possible"),
            "percentage": round((submission.get("score", 0) / assignment.get("points_possible", 1)) * 100, 1) if assignment.get("points_possible") else None,
            "submission_comments_analysis": analyze_submission_comments(submission.get("submission_comments", [])),
            "assignment_description_analysis": analyze_assignment_description(assignment.get("description", "")),
            "grade_flags": []
        }
        
        # Add grade flags based on analysis
        if analysis["submission_comments_analysis"]["has_point_deductions"]:
            analysis["grade_flags"].append("Point deductions mentioned in comments")
        
        if analysis["submission_comments_analysis"]["sentiment_score"] < -0.5 and analysis["percentage"] > 85:
            analysis["grade_flags"].append("Negative feedback but high grade - potential discrepancy")
        
        if analysis["submission_comments_analysis"]["sentiment_score"] > 0.5 and analysis["percentage"] < 70:
            analysis["grade_flags"].append("Positive feedback but low grade - potential discrepancy")
        
        if not analysis["submission_comments_analysis"]["has_feedback"] and analysis["percentage"] < 80:
            analysis["grade_flags"].append("Low grade with no feedback - consider requesting explanation")
        
        if analysis["assignment_description_analysis"]["has_point_breakdown"] and analysis["assignment_description_analysis"]["calculated_total"] != assignment.get("points_possible"):
            analysis["grade_flags"].append("Point breakdown in description doesn't match total points")
        
        return analysis
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing non-rubric assignment: {str(e)}")

def analyze_submission_comments(comments):
    """Analyze submission comments for grading insights"""
    if not comments:
        return {
            "has_feedback": False,
            "comment_count": 0,
            "has_point_deductions": False,
            "sentiment_score": 0,
            "key_phrases": []
        }
    
    all_text = " ".join([comment.get("comment", "") for comment in comments if comment.get("comment")])
    
    # Look for point deductions
    import re
    point_patterns = [
        r'-?\d+\s*points?',
        r'lost\s+\d+',
        r'deduct\w*\s+\d+',
        r'minus\s+\d+',
        r'-\d+',
        r'(\d+)\s*points?\s*(off|deducted|lost)'
    ]
    
    has_point_deductions = any(re.search(pattern, all_text.lower()) for pattern in point_patterns)
    
    # Simple sentiment analysis
    positive_words = ['good', 'great', 'excellent', 'well done', 'nice', 'impressive', 'strong', 'clear']
    negative_words = ['poor', 'weak', 'missing', 'unclear', 'confusing', 'incomplete', 'wrong', 'error']
    
    positive_count = sum(1 for word in positive_words if word in all_text.lower())
    negative_count = sum(1 for word in negative_words if word in all_text.lower())
    
    sentiment_score = (positive_count - negative_count) / max(len(all_text.split()), 1)
    
    return {
        "has_feedback": True,
        "comment_count": len(comments),
        "has_point_deductions": has_point_deductions,
        "sentiment_score": sentiment_score,
        "key_phrases": extract_key_phrases(all_text),
        "full_comments": [comment.get("comment", "") for comment in comments]
    }

def analyze_assignment_description(description):
    """Analyze assignment description for grading criteria"""
    if not description:
        return {
            "has_point_breakdown": False,
            "calculated_total": 0,
            "criteria_found": []
        }
    
    import re
    
    # Look for point breakdowns
    point_patterns = [
        r'(\d+)\s*points?',
        r'worth\s+(\d+)',
        r'(\d+)\s*pts?',
        r'(\d+)%'
    ]
    
    points_found = []
    for pattern in point_patterns:
        matches = re.findall(pattern, description.lower())
        points_found.extend([int(match) for match in matches])
    
    # Look for common grading criteria
    criteria_patterns = [
        r'graded\s+on',
        r'criteria\s*:',
        r'rubric',
        r'points\s+for',
        r'will\s+be\s+evaluated',
        r'assessment\s+criteria'
    ]
    
    criteria_found = [pattern for pattern in criteria_patterns if re.search(pattern, description.lower())]
    
    return {
        "has_point_breakdown": len(points_found) > 0,
        "calculated_total": sum(points_found),
        "points_breakdown": points_found,
        "criteria_found": criteria_found,
        "has_grading_info": len(criteria_found) > 0
    }

def extract_key_phrases(text):
    """Extract key phrases from feedback text"""
    if not text:
        return []
    
    # Common feedback phrases
    key_phrases = [
        'well done', 'good job', 'excellent work', 'needs improvement',
        'missing', 'unclear', 'confusing', 'great analysis', 'weak argument',
        'strong points', 'consider revising', 'good effort', 'incomplete'
    ]
    
    found_phrases = [phrase for phrase in key_phrases if phrase in text.lower()]
    return found_phrases[:5]  # Return top 5 matches

@router.get("/comprehensive-grade-analysis/{course_id}")
async def comprehensive_grade_analysis(course_id: int):
    """Analyze all assignments in a course for potential grading issues"""
    try:
        # Get all assignments
        assignments = await fetch_canvas_assignments(course_id)
        
        analysis_results = []
        flagged_assignments = []
        
        for assignment in assignments:
            assignment_id = assignment["id"]
            assignment_name = assignment["name"]
            
            try:
                # Check if assignment has a rubric
                rubric_info = await fetch_assignment_rubric(assignment_id)
                has_rubric = rubric_info.get("rubric") is not None
                
                # Get your submission
                submission = await fetch_my_canvas_grade(course_id, assignment_id)
                
                # Skip if not graded
                if submission.get("workflow_state") != "graded":
                    continue
                
                assignment_analysis = {
                    "assignment_id": assignment_id,
                    "assignment_name": assignment_name,
                    "has_rubric": has_rubric,
                    "your_score": submission.get("score"),
                    "points_possible": assignment.get("points_possible"),
                    "percentage": round((submission.get("score", 0) / assignment.get("points_possible", 1)) * 100, 1) if assignment.get("points_possible") else None,
                    "flags": [],
                    "analysis_type": "rubric" if has_rubric else "alternative"
                }
                
                if has_rubric:
                    # Use rubric analysis
                    from app.routes.grading import check_grade_against_rubric_endpoint
                    grade_check = await check_grade_against_rubric_endpoint(course_id, assignment_id)
                    
                    if grade_check.get("analysis", {}).get("has_discrepancy", False):
                        assignment_analysis["flags"].append(f"Rubric discrepancy: {grade_check['analysis']['score_difference']} points")
                        flagged_assignments.append(assignment_analysis)
                else:
                    # Use alternative analysis
                    alt_analysis = await analyze_non_rubric_assignment(course_id, assignment_id)
                    assignment_analysis["flags"] = alt_analysis["grade_flags"]
                    
                    if alt_analysis["grade_flags"]:
                        flagged_assignments.append(assignment_analysis)
                
                analysis_results.append(assignment_analysis)
                
            except Exception as e:
                print(f"Error analyzing assignment {assignment_id}: {str(e)}")
                continue
        
        # Calculate summary statistics
        total_graded = len(analysis_results)
        flagged_count = len(flagged_assignments)
        rubric_assignments = sum(1 for a in analysis_results if a["has_rubric"])
        non_rubric_assignments = total_graded - rubric_assignments
        
        average_score = sum(a["percentage"] for a in analysis_results if a["percentage"]) / max(total_graded, 1)
        
        return {
            "course_id": course_id,
            "summary": {
                "total_graded_assignments": total_graded,
                "flagged_assignments": flagged_count,
                "rubric_assignments": rubric_assignments,
                "non_rubric_assignments": non_rubric_assignments,
                "average_percentage": round(average_score, 1),
                "flag_rate": round((flagged_count / max(total_graded, 1)) * 100, 1)
            },
            "flagged_assignments": flagged_assignments,
            "all_assignments": analysis_results
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in comprehensive analysis: {str(e)}")

@router.get("/collect-marking-patterns/{course_id}")
async def collect_marking_patterns(course_id: int):
    """Collect marking pattern data for a specific lecturer/course"""
    try:
        # Get course and instructor info
        course = await fetch_course_details(course_id)
        instructor = await fetch_course_instructor(course_id)
        
        # Get all graded assignments
        assignments = await fetch_canvas_assignments(course_id)
        
        marking_data = {
            "course_id": course_id,
            "course_name": course.get("name"),
            "instructor_id": instructor.get("id"),
            "instructor_name": instructor.get("name"),
            "data_points": []
        }
        
        for assignment in assignments:
            assignment_id = assignment["id"]
            
            try:
                # Get your submission
                submission = await fetch_my_canvas_grade(course_id, assignment_id)
                
                # Skip if not graded
                if submission.get("workflow_state") != "graded":
                    continue
                
                # Collect comprehensive data point
                data_point = {
                    "assignment_id": assignment_id,
                    "assignment_name": assignment.get("name"),
                    "assignment_type": classify_assignment_type(assignment.get("name", ""), assignment.get("description", "")),
                    "points_possible": assignment.get("points_possible"),
                    "your_score": submission.get("score"),
                    "percentage": round((submission.get("score", 0) / assignment.get("points_possible", 1)) * 100, 1) if assignment.get("points_possible") else None,
                    "submission_date": submission.get("submitted_at"),
                    "graded_date": submission.get("graded_at"),
                    "late_submission": submission.get("late", False),
                    "attempt_count": submission.get("attempt", 1),
                    
                    # Feedback analysis
                    "feedback_data": extract_feedback_features(submission.get("submission_comments", [])),
                    
                    # Rubric data if available
                    "rubric_data": None,
                    
                    # Assignment characteristics
                    "assignment_features": extract_assignment_features(assignment)
                }
                
                # Get rubric data if available
                try:
                    rubric_info = await fetch_assignment_rubric(assignment_id)
                    if rubric_info.get("rubric"):
                        data_point["rubric_data"] = extract_rubric_features(submission, rubric_info)
                except:
                    pass
                
                marking_data["data_points"].append(data_point)
                
            except Exception as e:
                print(f"Error collecting data for assignment {assignment_id}: {str(e)}")
                continue
        
        # Save to file for ML training
        import json
        filename = f"marking_patterns_{course_id}_{instructor.get('id', 'unknown')}.json"
        with open(filename, 'w') as f:
            json.dump(marking_data, f, indent=2)
        
        return {
            "status": "success",
            "data_points_collected": len(marking_data["data_points"]),
            "instructor": instructor.get("name"),
            "course": course.get("name"),
            "filename": filename,
            "summary": analyze_marking_patterns(marking_data["data_points"])
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error collecting marking patterns: {str(e)}")

def classify_assignment_type(name, description):
    """Classify assignment type based on name and description"""
    name_lower = name.lower()
    desc_lower = description.lower() if description else ""
    
    if any(word in name_lower for word in ['quiz', 'test', 'exam']):
        return "quiz"
    elif any(word in name_lower for word in ['essay', 'paper', 'report', 'analysis']):
        return "essay"
    elif any(word in name_lower for word in ['lab', 'practical', 'experiment']):
        return "lab"
    elif any(word in name_lower for word in ['project', 'assignment']):
        return "project"
    elif any(word in name_lower for word in ['discussion', 'forum', 'post']):
        return "discussion"
    elif any(word in name_lower for word in ['homework', 'hw', 'problem']):
        return "homework"
    else:
        return "other"

def extract_feedback_features(comments):
    """Extract features from feedback comments"""
    if not comments:
        return {
            "feedback_length": 0,
            "feedback_sentiment": 0,
            "specific_points_mentioned": False,
            "improvement_suggestions": False,
            "positive_reinforcement": False
        }
    
    all_text = " ".join([comment.get("comment", "") for comment in comments])
    
    # Sentiment analysis
    positive_words = ['good', 'great', 'excellent', 'well done', 'nice', 'impressive', 'strong', 'clear', 'thorough']
    negative_words = ['poor', 'weak', 'missing', 'unclear', 'confusing', 'incomplete', 'wrong', 'error', 'lacking']
    improvement_words = ['improve', 'consider', 'try', 'next time', 'could', 'should', 'might']
    
    positive_count = sum(1 for word in positive_words if word in all_text.lower())
    negative_count = sum(1 for word in negative_words if word in all_text.lower())
    improvement_count = sum(1 for word in improvement_words if word in all_text.lower())
    
    return {
        "feedback_length": len(all_text),
        "feedback_sentiment": (positive_count - negative_count) / max(len(all_text.split()), 1),
        "specific_points_mentioned": bool(re.search(r'\d+\s*points?', all_text.lower())),
        "improvement_suggestions": improvement_count > 0,
        "positive_reinforcement": positive_count > 0,
        "word_count": len(all_text.split()),
        "comment_count": len(comments)
    }

def extract_assignment_features(assignment):
    """Extract features from assignment details"""
    return {
        "points_possible": assignment.get("points_possible", 0),
        "due_date_set": assignment.get("due_at") is not None,
        "has_description": bool(assignment.get("description")),
        "description_length": len(assignment.get("description", "")),
        "submission_types": assignment.get("submission_types", []),
        "allowed_attempts": assignment.get("allowed_attempts", 1)
    }

def extract_rubric_features(submission, rubric_info):
    """Extract features from rubric assessment"""
    rubric_assessment = submission.get("rubric_assessment", {})
    rubric = rubric_info.get("rubric", [])
    
    if not rubric_assessment or not rubric:
        return None
    
    criteria_scores = []
    for criterion in rubric:
        criterion_id = criterion.get("id")
        if criterion_id in rubric_assessment:
            awarded_points = rubric_assessment[criterion_id].get("points", 0)
            possible_points = criterion.get("points", 0)
            if possible_points > 0:
                criteria_scores.append(awarded_points / possible_points)
    
    return {
        "criteria_count": len(rubric),
        "criteria_scores": criteria_scores,
        "average_criteria_score": sum(criteria_scores) / len(criteria_scores) if criteria_scores else 0,
        "lowest_criteria_score": min(criteria_scores) if criteria_scores else 0,
        "highest_criteria_score": max(criteria_scores) if criteria_scores else 0,
        "score_variance": calculate_variance(criteria_scores) if len(criteria_scores) > 1 else 0
    }

def calculate_variance(scores):
    """Calculate variance of scores"""
    if len(scores) < 2:
        return 0
    mean = sum(scores) / len(scores)
    return sum((x - mean) ** 2 for x in scores) / len(scores)

def analyze_marking_patterns(data_points):
    """Analyze collected marking patterns"""
    if not data_points:
        return {}
    
    # Calculate statistics
    scores = [dp["percentage"] for dp in data_points if dp["percentage"] is not None]
    assignment_types = [dp["assignment_type"] for dp in data_points]
    
    type_scores = {}
    for dp in data_points:
        if dp["percentage"] is not None:
            if dp["assignment_type"] not in type_scores:
                type_scores[dp["assignment_type"]] = []
            type_scores[dp["assignment_type"]].append(dp["percentage"])
    
    return {
        "total_assignments": len(data_points),
        "average_score": sum(scores) / len(scores) if scores else 0,
        "score_range": {"min": min(scores), "max": max(scores)} if scores else {},
        "assignment_type_distribution": {t: type_scores[t] for t in type_scores},
        "average_by_type": {t: sum(type_scores[t]) / len(type_scores[t]) for t in type_scores},
        "feedback_patterns": analyze_feedback_patterns(data_points)
    }

def analyze_feedback_patterns(data_points):
    """Analyze feedback giving patterns"""
    feedback_lengths = [dp["feedback_data"]["feedback_length"] for dp in data_points]
    sentiment_scores = [dp["feedback_data"]["feedback_sentiment"] for dp in data_points]
    
    return {
        "average_feedback_length": sum(feedback_lengths) / len(feedback_lengths) if feedback_lengths else 0,
        "average_sentiment": sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0,
        "gives_specific_points": sum(1 for dp in data_points if dp["feedback_data"]["specific_points_mentioned"]) / len(data_points),
        "gives_improvement_suggestions": sum(1 for dp in data_points if dp["feedback_data"]["improvement_suggestions"]) / len(data_points)
    }

@router.get("/ml/train-marking-model/{course_id}")
async def train_marking_model(course_id: int):
    """Train ML model on lecturer's marking patterns"""
    try:
        # First collect the data
        collection_result = await collect_marking_patterns(course_id)
        
        if collection_result["data_points_collected"] < 5:
            return {
                "status": "insufficient_data",
                "message": f"Need at least 5 graded assignments, found {collection_result['data_points_collected']}",
                "suggestion": "Wait for more assignments to be graded before training"
            }
        
        # Get instructor ID
        instructor = await fetch_course_instructor(course_id)
        instructor_id = str(instructor.get("id", "unknown"))
        
        # Initialize and train the ML model
        from app.services.ml_marking_predictor import LecturerMarkingPredictor
        predictor = LecturerMarkingPredictor(instructor_id)
        
        # Train on the collected data
        training_result = predictor.train_model(collection_result["filename"])
        
        return {
            "status": "success",
            "instructor": instructor.get("name"),
            "course_id": course_id,
            "data_collection": collection_result,
            "training_result": training_result,
            "model_ready": training_result["status"] == "success"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error training ML model: {str(e)}")

@router.get("/ml/predict-grade/{course_id}/{assignment_id}")
async def predict_assignment_grade(course_id: int, assignment_id: int):
    """Predict expected grade for an assignment using ML model"""
    try:
        # Get instructor ID
        instructor = await fetch_course_instructor(course_id)
        instructor_id = str(instructor.get("id", "unknown"))
        
        # Load the ML model
        from app.services.ml_marking_predictor import LecturerMarkingPredictor
        predictor = LecturerMarkingPredictor(instructor_id)
        
        if not predictor.is_trained:
            return {
                "status": "model_not_trained",
                "message": "ML model not trained yet. Train the model first using /ml/train-marking-model/{course_id}",
                "instructor": instructor.get("name")
            }
        
        # Get assignment data
        assignment = await fetch_assignment_details(assignment_id)
        submission = await fetch_my_canvas_grade(course_id, assignment_id)
        
        # Prepare data for prediction
        assignment_data = {
            "assignment_id": assignment_id,
            "assignment_name": assignment.get("name"),
            "assignment_type": classify_assignment_type(assignment.get("name", ""), assignment.get("description", "")),
            "points_possible": assignment.get("points_possible"),
            "late_submission": submission.get("late", False),
            "attempt_count": submission.get("attempt", 1),
            "feedback_data": extract_feedback_features(submission.get("submission_comments", [])),
            "assignment_features": extract_assignment_features(assignment)
        }
        
        # Add rubric data if available
        try:
            rubric_info = await fetch_assignment_rubric(assignment_id)
            if rubric_info.get("rubric"):
                assignment_data["rubric_data"] = extract_rubric_features(submission, rubric_info)
        except:
            pass
        
        # Make prediction
        prediction = predictor.predict_expected_grade(assignment_data)
        
        return {
            "status": "success",
            "assignment_name": assignment.get("name"),
            "instructor": instructor.get("name"),
            "prediction": prediction,
            "assignment_data": assignment_data
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error predicting grade: {str(e)}")

@router.get("/ml/detect-anomaly/{course_id}/{assignment_id}")
async def detect_grading_anomaly_ml(course_id: int, assignment_id: int):
    """Detect grading anomalies using ML model"""
    try:
        # Get instructor ID
        instructor = await fetch_course_instructor(course_id)
        instructor_id = str(instructor.get("id", "unknown"))
        
        # Load the ML model
        from app.services.ml_marking_predictor import LecturerMarkingPredictor
        predictor = LecturerMarkingPredictor(instructor_id)
        
        if not predictor.is_trained:
            return {
                "status": "model_not_trained",
                "message": "ML model not trained yet. Train the model first.",
                "fallback_analysis": await analyze_non_rubric_assignment(course_id, assignment_id)
            }
        
        # Get assignment and submission data
        assignment = await fetch_assignment_details(assignment_id)
        submission = await fetch_my_canvas_grade(course_id, assignment_id)
        
        if submission.get("workflow_state") != "graded":
            return {
                "status": "not_graded",
                "message": "Assignment not graded yet"
            }
        
        # Prepare data for anomaly detection
        assignment_data = {
            "assignment_id": assignment_id,
            "assignment_name": assignment.get("name"),
            "assignment_type": classify_assignment_type(assignment.get("name", ""), assignment.get("description", "")),
            "points_possible": assignment.get("points_possible"),
            "late_submission": submission.get("late", False),
            "attempt_count": submission.get("attempt", 1),
            "feedback_data": extract_feedback_features(submission.get("submission_comments", [])),
            "assignment_features": extract_assignment_features(assignment)
        }
        
        # Add rubric data if available
        try:
            rubric_info = await fetch_assignment_rubric(assignment_id)
            if rubric_info.get("rubric"):
                assignment_data["rubric_data"] = extract_rubric_features(submission, rubric_info)
        except:
            pass
        
        # Detect anomaly
        actual_score = submission.get("score", 0)
        anomaly_result = predictor.detect_grading_anomaly(actual_score, assignment_data)
        
        return {
            "status": "success",
            "assignment_name": assignment.get("name"),
            "instructor": instructor.get("name"),
            "actual_score": actual_score,
            "anomaly_analysis": anomaly_result,
            "recommendation": get_anomaly_recommendation(anomaly_result)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error detecting anomaly: {str(e)}")

@router.get("/ml/model-stats/{course_id}")
async def get_ml_model_stats(course_id: int):
    """Get statistics about the trained ML model"""
    try:
        # Get instructor ID
        instructor = await fetch_course_instructor(course_id)
        instructor_id = str(instructor.get("id", "unknown"))
        
        # Load the ML model
        from app.services.ml_marking_predictor import LecturerMarkingPredictor
        predictor = LecturerMarkingPredictor(instructor_id)
        
        stats = predictor.get_model_stats()
        
        return {
            "status": "success",
            "instructor": instructor.get("name"),
            "course_id": course_id,
            "model_stats": stats
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting model stats: {str(e)}")

def get_anomaly_recommendation(anomaly_result):
    """Get recommendation based on anomaly analysis"""
    if anomaly_result["status"] != "success":
        return "Unable to analyze - check model status"
    
    if not anomaly_result["is_significant_anomaly"]:
        return "Grade appears consistent with lecturer's marking patterns"
    
    severity = anomaly_result["severity"]
    difference = anomaly_result["difference"]
    
    if severity == "high":
        return f"High anomaly detected ({difference}% difference). Strongly recommend contacting lecturer for clarification."
    elif severity == "medium":
        return f"Moderate anomaly detected ({difference}% difference). Consider reviewing with lecturer."
    else:
        return f"Minor anomaly detected ({difference}% difference). Monitor for patterns."

@router.get("/ml/train-marking-model-simple/{course_id}")
async def train_marking_model_simple(course_id: int):
    """Train ML model without requiring instructor permissions (for testing)"""
    try:
        # Get course details (this should work with student permissions)
        course = await fetch_course_details(course_id)
        
        # Use a simplified instructor ID based on course
        instructor_id = f"instructor_{course_id}"
        
        # Get all graded assignments
        assignments = await fetch_canvas_assignments(course_id)
        
        marking_data = {
            "course_id": course_id,
            "course_name": course.get("name"),
            "instructor_id": instructor_id,
            "instructor_name": f"Instructor ({course.get('name', 'Unknown')})",
            "data_points": []
        }
        
        graded_count = 0
        
        for assignment in assignments:
            assignment_id = assignment["id"]
            
            try:
                # Get your submission
                submission = await fetch_my_canvas_grade(course_id, assignment_id)
                
                # Skip if not graded
                if submission.get("workflow_state") != "graded":
                    continue
                
                graded_count += 1
                
                # Collect comprehensive data point
                data_point = {
                    "assignment_id": assignment_id,
                    "assignment_name": assignment.get("name"),
                    "assignment_type": classify_assignment_type(assignment.get("name", ""), assignment.get("description", "")),
                    "points_possible": assignment.get("points_possible"),
                    "your_score": submission.get("score"),
                    "percentage": round((submission.get("score", 0) / assignment.get("points_possible", 1)) * 100, 1) if assignment.get("points_possible") else None,
                    "submission_date": submission.get("submitted_at"),
                    "graded_date": submission.get("graded_at"),
                    "late_submission": submission.get("late", False),
                    "attempt_count": submission.get("attempt", 1),
                    
                    # Feedback analysis
                    "feedback_data": extract_feedback_features(submission.get("submission_comments", [])),
                    
                    # Rubric data if available
                    "rubric_data": None,
                    
                    # Assignment characteristics
                    "assignment_features": extract_assignment_features(assignment)
                }
                
                # Get rubric data if available
                try:
                    rubric_info = await fetch_assignment_rubric(assignment_id)
                    if rubric_info.get("rubric"):
                        data_point["rubric_data"] = extract_rubric_features(submission, rubric_info)
                except:
                    pass
                
                marking_data["data_points"].append(data_point)
                
            except Exception as e:
                print(f"Error collecting data for assignment {assignment_id}: {str(e)}")
                continue
        
        if graded_count < 5:
            return {
                "status": "insufficient_data",
                "message": f"Need at least 5 graded assignments, found {graded_count}",
                "suggestion": "Wait for more assignments to be graded before training",
                "graded_assignments": graded_count,
                "total_assignments": len(assignments)
            }
        
        # Save to file for ML training
        import json
        filename = f"marking_patterns_{course_id}_{instructor_id}.json"
        with open(filename, 'w') as f:
            json.dump(marking_data, f, indent=2)
        
        # Initialize and train the ML model
        from app.services.ml_marking_predictor import LecturerMarkingPredictor
        predictor = LecturerMarkingPredictor(instructor_id)
        
        # Train on the collected data
        training_result = predictor.train_model(filename)
        
        return {
            "status": "success",
            "instructor_id": instructor_id,
            "course_name": course.get("name"),
            "course_id": course_id,
            "graded_assignments": graded_count,
            "total_assignments": len(assignments),
            "data_collection": {
                "data_points_collected": len(marking_data["data_points"]),
                "filename": filename,
                "summary": analyze_marking_patterns(marking_data["data_points"])
            },
            "training_result": training_result,
            "model_ready": training_result["status"] == "success"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error training ML model: {str(e)}")

@router.get("/ml/test-permissions/{course_id}")
async def test_canvas_permissions(course_id: int):
    """Test what Canvas API endpoints we can access with current permissions"""
    results = {}
    
    # Test course details
    try:
        course = await fetch_course_details(course_id)
        results["course_details"] = {"status": "success", "name": course.get("name")}
    except Exception as e:
        results["course_details"] = {"status": "error", "error": str(e)}
    
    # Test assignments
    try:
        assignments = await fetch_canvas_assignments(course_id)
        results["assignments"] = {"status": "success", "count": len(assignments)}
    except Exception as e:
        results["assignments"] = {"status": "error", "error": str(e)}
    
    # Test instructor access
    try:
        instructor = await fetch_course_instructor(course_id)
        results["instructor"] = {"status": "success", "name": instructor.get("name")}
    except Exception as e:
        results["instructor"] = {"status": "error", "error": str(e)}
    
    # Test current user
    try:
        user = await fetch_current_user()
        results["current_user"] = {"status": "success", "name": user.get("name")}
    except Exception as e:
        results["current_user"] = {"status": "error", "error": str(e)}
    
    return {
        "course_id": course_id,
        "permission_tests": results,
        "recommendation": get_permission_recommendation(results)
    }

def get_permission_recommendation(results):
    """Get recommendation based on permission test results"""
    if results["course_details"]["status"] == "error":
        return "❌ Cannot access course details. Check your Canvas token and course ID."
    
    if results["assignments"]["status"] == "error":
        return "❌ Cannot access assignments. Check your enrollment in this course."
    
    if results["instructor"]["status"] == "error":
        return "⚠️ Cannot access instructor info (common limitation). Use simplified ML training endpoint."
    
    return "✅ All permissions working. You can use the full ML training endpoint."

