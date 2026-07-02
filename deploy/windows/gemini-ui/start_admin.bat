@echo off
cd /d C:\AIWork\opt\gemini-ui
call venv\Scripts\activate.bat
streamlit run admin.py ^
  --server.port 8508 ^
  --server.address 0.0.0.0 ^
  --server.headless true ^
  --browser.gatherUsageStats false
