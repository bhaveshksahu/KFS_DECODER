# Project rules — KFS Decoder
Source of truth: docs/PRD.md. Read the relevant section before coding. If code and PRD conflict, ask me.

Non-negotiables:
1. The LLM NEVER computes APR, EMI, totals, or rule pass/fail. Code does. (PRD §6, §7, §9)
2. Gemini model ID comes from env var GEMINI_MODEL. Never hard-code a model name.
3. All Gemini calls live in backend/app/services/gemini_client.py using the google-genai SDK.
   Do not guess SDK syntax: check current official docs before writing calls.
4. Extraction must follow the schema in PRD §8. Missing fields are null, never guessed.
5. Every rule result carries an RBI citation (PRD §7).
6. No real personal data in the repo. No secrets in code. Use .env (gitignored).
7. Tests in backend/tests must pass before you say a task is done. Show me the pytest output.
8. Work only on the phase I name. Do not add features outside the current task.
9. Small, reviewable changes. Tell me which files you changed and how to run them.