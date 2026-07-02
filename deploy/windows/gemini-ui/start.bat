@echo off
cd /d C:\AIWork\opt\gemini-ui
call venv\Scripts\activate.bat
streamlit run app.py ^
  --server.port 8507 ^
  --server.address 0.0.0.0 ^
  --server.headless true ^
  --browser.gatherUsageStats false ^
  --server.maxUploadSize 500
