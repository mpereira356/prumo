$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível criar o ambiente Python.' }
}
& $projectPython -c 'import flask, flask_wtf, waitress, tzdata'
if ($LASTEXITCODE -ne 0) {
    & $projectPython -m pip --isolated install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível instalar as dependências.' }
}
@'
from app import app
import sqlite3
connection = sqlite3.connect(app.config['DATABASE'])
count = connection.execute('SELECT COUNT(*) FROM users').fetchone()[0]
connection.close()
raise SystemExit(0 if count else 1)
'@ | & $projectPython -
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Crie seu acesso pessoal. A senha precisa ter pelo menos 12 caracteres.'
    & $projectPython -m flask --app app create-admin
    if ($LASTEXITCODE -ne 0) { throw 'Criação do acesso não concluída.' }
}
Write-Host 'Abra http://127.0.0.1:5000. Use Ctrl+C para encerrar.'
& $projectPython app.py
