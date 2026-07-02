@echo off
echo === gemini-ui same API test (stream vs non-stream) ===
echo.

echo [A] Non-stream (same as OLLAMA_USE_STREAM=0)
powershell -NoProfile -Command ^
  "$sw=[Diagnostics.Stopwatch]::StartNew();" ^
  "$body=@{model='gemma4:e4b';messages=@(@{role='user';content='Hello, one sentence.'});stream=$false;options=@{temperature=0.7}} | ConvertTo-Json -Depth 5;" ^
  "$r=Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/chat' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 120;" ^
  "$sw.Stop(); Write-Host ('OK ' + $sw.Elapsed.TotalSeconds.ToString('F1') + 's'); Write-Host $r.message.content"
echo.

echo [B] Stream (same as gemini-ui default)
powershell -NoProfile -Command ^
  "$sw=[Diagnostics.Stopwatch]::StartNew();" ^
  "$body='{""model"":""gemma4:e4b"",""messages"":[{""role"":""user"",""content"":""Hello, one sentence.""}],""stream"":true,""options"":{""temperature"":0.7}}';" ^
  "$req=[System.Net.HttpWebRequest]::Create('http://127.0.0.1:11434/api/chat');" ^
  "$req.Method='POST'; $req.ContentType='application/json'; $req.Timeout=120000;" ^
  "$bytes=[Text.Encoding]::UTF8.GetBytes($body);" ^
  "$req.ContentLength=$bytes.Length; $s=$req.GetRequestStream(); $s.Write($bytes,0,$bytes.Length); $s.Close();" ^
  "$resp=$req.GetResponse(); $rd=New-Object IO.StreamReader($resp.GetResponseStream());" ^
  "$text=''; while(-not $rd.EndOfStream){ $line=$rd.ReadLine(); if($line){ $j=$line | ConvertFrom-Json; $text+=$j.message.content; if($j.done){break}}};" ^
  "$sw.Stop(); Write-Host ('OK ' + $sw.Elapsed.TotalSeconds.ToString('F1') + 's'); Write-Host $text"
echo.

echo If [A] is fast but [B] hangs, set OLLAMA_USE_STREAM=0 in gemini-ui .env
pause
