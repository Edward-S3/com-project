@echo off
setlocal EnableDelayedExpansion
echo === Ollama diagnostic for gemini-ui ===
echo.

echo [1] Ollama version
ollama --version
echo.

echo [2] Installed models
ollama list
echo.

echo [3] GPU (nvidia-smi)
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>nul
if errorlevel 1 echo nvidia-smi not available
echo.

echo [4] Ollama API tags
curl -s http://127.0.0.1:11434/api/tags
echo.
echo.

echo [5] Quick chat test (gemma4:e4b, 120s max) - measure response time
echo Prompt: Hello, reply in one short sentence.
powershell -NoProfile -Command ^
  "$sw=[Diagnostics.Stopwatch]::StartNew();" ^
  "$body=@{model='gemma4:e4b';messages=@(@{role='user';content='Hello, reply in one short sentence.'});stream=$false} | ConvertTo-Json -Depth 5;" ^
  "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/chat' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 120; $sw.Stop(); Write-Host ('OK in ' + $sw.Elapsed.TotalSeconds.ToString('F1') + 's'); Write-Host $r.message.content } catch { $sw.Stop(); Write-Host ('FAIL after ' + $sw.Elapsed.TotalSeconds.ToString('F1') + 's: ' + $_.Exception.Message) }"
echo.

echo [6] If step 5 failed, try llama3.2 (baseline)
powershell -NoProfile -Command ^
  "$sw=[Diagnostics.Stopwatch]::StartNew();" ^
  "$body=@{model='llama3.2:latest';messages=@(@{role='user';content='Hello'});stream=$false} | ConvertTo-Json -Depth 5;" ^
  "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/chat' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 120; $sw.Stop(); Write-Host ('OK in ' + $sw.Elapsed.TotalSeconds.ToString('F1') + 's'); Write-Host $r.message.content } catch { $sw.Stop(); Write-Host ('FAIL: ' + $_.Exception.Message) }"
echo.

echo === Done ===
echo Send this full output when reporting results.
pause
