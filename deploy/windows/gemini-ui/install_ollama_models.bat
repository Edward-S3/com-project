@echo off
echo === Ollama model install (gemma4) ===
echo This may take a while depending on network speed.

ollama pull gemma4:e2b
ollama pull gemma4:e4b
ollama pull gemma4:12b
ollama pull gemma4:26b

echo.
echo === Installed models ===
ollama list
echo.
echo Next: start.bat
