import os

UNIFIED_ADMIN_URL = os.getenv("UNIFIED_ADMIN_URL", "/nai-ctrl/")
EXAM_DB_PATH = os.getenv("EXAM_DB_PATH", "/opt/exam/exam_app.db")
FBACK_DB_PATH = os.getenv("FBACK_DB_PATH", "/opt/fback/universal_feedback.db")
NAI2_ROOT = os.getenv("NAI2_ROOT", "/opt/gemini-ui2")
