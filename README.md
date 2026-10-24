# Invoice A2A Agent — Phase 1 and 2

## Windows PowerShell setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

If activation is blocked, run `.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload` directly.

Open another PowerShell terminal:

```powershell
$h = @{ Authorization='Bearer dev-token-alpha'; 'A2A-Version'='1.0'; 'Content-Type'='application/a2a+json' }
$body = @{ message=@{ messageId='test-001'; role='ROLE_USER'; parts=@(@{ mediaType='application/vnd.ga5.invoice-claim-batch+json'; data=@{ batchId='batch-001'; policyRevision='r1'; packages=@() } }) }; configuration=@{ returnImmediately=$false } } | ConvertTo-Json -Depth 20
Invoke-RestMethod http://127.0.0.1:8000/.well-known/agent-card.json
$r = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/a2a/message:send' -Headers $h -Body $body
$r | ConvertTo-Json -Depth 20
$id = $r.task.id
Invoke-RestMethod -Uri "http://127.0.0.1:8000/a2a/tasks/$id" -Headers $h
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/a2a/tasks' -Headers $h
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/a2a/tasks/${id}:cancel" -Headers $h -Body '{}'
```

Run automated tests:

```powershell
python -m pytest -q
```

## Important scope

`A2A_DEMO_MODE=true` creates local-only SUBMITTED Tasks without invoice proposals. It is **not contract-compliant for grading**. Set `A2A_DEMO_MODE=false` for production; initial batch requests then return 501 until the Phase 3 decision engine is implemented. Phase 4 will add validated result continuations and receipts.

Do not deploy or submit this scaffold to the grader as a complete solution. SQLite works for local single-process tests. For multi-instance deployment use a shared transactional database such as PostgreSQL. Never commit `.env` or real tokens.
