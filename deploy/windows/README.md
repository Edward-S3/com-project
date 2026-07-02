# Windows 検証機セットアップ手順

検証機: Windows 11 / 172.16.16.13 / `C:\AIWork\opt\`

## クイックスタート（優先順）

### A. ベース環境（Windows 管理者 PowerShell）

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
C:\AIWork\opt\deploy\windows\setup_phase0.ps1
```

### B. ジャンクション

```powershell
cmd /c mklink /J C:\opt C:\AIWork\opt
```

### C. gemini-ui 構築

```powershell
cd C:\AIWork\opt\deploy\windows\gemini-ui
.\setup_venv.bat
.\install_ollama_models.bat
# .env を編集（.env.example をコピー）
copy C:\AIWork\opt\gemini-ui\.env.example C:\AIWork\opt\gemini-ui\.env
notepad C:\AIWork\opt\gemini-ui\.env
.\start.bat
```

### D. 動作確認

- http://localhost:8507 （利用者 UI）
- http://localhost:8508 （管理画面）
- ローカルモデル `local:gemma4-*` でチャット応答

詳細は `/opt/HANDOFF_WINDOWS_MIGRATION.md` を参照。
