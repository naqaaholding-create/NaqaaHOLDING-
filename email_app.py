from app import app
import email_verification

email_verification.install(app, __import__("app"))
