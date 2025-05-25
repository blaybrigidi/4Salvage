import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import email_settings
from app.services.canvas_api import (
    fetch_course_instructor,
    fetch_course_details,
    fetch_assignment_details,
    fetch_current_user
)

async def send_email(email_data):
    """Send an email using SMTP"""
    if not email_settings.EMAIL_SENDER or not email_settings.EMAIL_PASSWORD:
        print("Email sending skipped - SMTP credentials not configured")
        return False
    
    message = MIMEMultipart()
    message["From"] = email_settings.EMAIL_SENDER
    message["To"] = email_data["to"]
    message["Subject"] = email_data["subject"]
    
    # Attach body
    message.attach(MIMEText(email_data["body"], "plain"))
    
    try:
        # Connect to SMTP server
        server = smtplib.SMTP(email_settings.SMTP_SERVER, email_settings.SMTP_PORT)
        server.starttls()
        server.login(email_settings.EMAIL_SENDER, email_settings.EMAIL_PASSWORD)
        
        # Send email
        server.send_message(message)
        server.quit()
        
        print(f"Email sent successfully to {email_data['to']}")
        return True
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False

async def draft_email_for_discrepancy(course_id: int, assignment_id: int, grade_check):
    """Draft an email for a grade discrepancy"""
    try:
        if not grade_check.get("analysis", {}).get("has_discrepancy", False):
            return None
        
        # Get instructor information
        instructor = await fetch_course_instructor(course_id)
        
        # Get assignment and course details
        assignment = await fetch_assignment_details(assignment_id)
        course = await fetch_course_details(course_id)
        
        # Get student information
        student = await fetch_current_user()
        
        # Draft the email
        email_draft = create_email_draft(
            student=student,
            instructor=instructor,
            course=course,
            assignment=assignment,
            grade_check=grade_check
        )
        
        return email_draft
    except Exception as e:
        print(f"Error drafting email: {str(e)}")
        return None

def create_email_draft(student, instructor, course, assignment, grade_check):
    """Create an email draft for a grade discrepancy"""
    analysis = grade_check["analysis"]
    
    # Format the criteria analysis for the email
    criteria_details = ""
    for criterion in analysis["criteria_analysis"]:
        if criterion.get("has_discrepancy"):
            criteria_details += f"- {criterion['description']}: {criterion['points_awarded']} / {criterion['possible_points']} points\n"
            criteria_details += f"  * Issue: {criterion['discrepancy_reason']}\n"
        else:
            criteria_details += f"- {criterion['description']}: {criterion['points_awarded']} / {criterion['possible_points']} points\n"
    
    email_subject = f"Grade Review Request: {assignment['name']} in {course['name']}"
    
    email_body = f"""
Dear Professor {instructor['name']},

I hope this email finds you well. I am writing to request a review of my grade for the assignment "{assignment['name']}" in {course['name']}.

Based on my review of the rubric, I believe there may be a discrepancy of approximately {analysis['score_difference']} points between my current score of {analysis['actual_score']} and the calculated score of {analysis['calculated_score']} based on the rubric criteria.

Here's a breakdown of the rubric assessment:

{criteria_details}

I would greatly appreciate it if you could review my submission and rubric assessment at your convenience. 

Thank you for your time and consideration.

Sincerely,
{student['name']}
{student['email']}
"""
    
    return {
        "to": instructor['email'],
        "subject": email_subject,
        "body": email_body.strip()
    }