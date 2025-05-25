import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class EmailSettings(BaseSettings):
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    EMAIL_SENDER: str = ""
    EMAIL_PASSWORD: str = ""
    
    class Config:
        env_file = ".env"
        extra = "allow"

class CanvasSettings(BaseSettings):
    CANVAS_API_BASE: str = "https://ashesi.instructure.com"
    CANVAS_TOKEN: str = ""
    
    class Config:
        env_file = ".env"
        extra = "allow"

# Initialize settings
email_settings = EmailSettings()
canvas_settings = CanvasSettings()