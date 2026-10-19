# Hosting app from GitHub code

https://share.google/aimode/vgEfE3w46MVtSVkLF

Using **`uv`** (Astral's lightning-fast Python package installer and resolver) makes hosting highly efficient. Platforms like **Render**, **Railway**, and **Fly.io** fully support modern `uv`\-driven workflows. 

Because you are using `uv`, you will deploy using a `pyproject.toml` file or a locked `uv.lock` file instead of an old `requirements.txt`. 

How to host a `uv`\-managed Python application

| Step | Action | Instructions & Commands |
| --- | --- | --- |
| **1** | **Lock your dependencies** | Ensure your dependencies are completely up to date locally before pushing. Run: `uv lock` |
| **2** | **Verify files** | Make sure your `pyproject.toml` and `uv.lock` are in your root repository directory and committed to GitHub alongside your code. |
| **3** | **Choose your platform** | Create a free account on **[Render](https://render.com/)** or **[Railway](https://railway.app/)** and link your GitHub repository. |
| **4** | **Configure Build Command** | Use `uv` to handle the backend build quickly on the server host: `pip install uv && uv pip install -r pyproject.toml` _(or if the platform supports a raw shell script: `curl -LsSf https://astral.sh \\| sh && uv sync`)_ |
| **5** | **Configure Start Command** | Point the platform's execution environment to your `uv` execution command or your framework's runner. • For inline scripts: `uv run python main.py` • For standard ASGI/WSGI servers: `uv run uvicorn main:app --host 0.0.0.0 --port $PORT` |

Platform Recommendations

-   **Render:** Excellent for automated git-push triggers and easy port routing. Set your `PORT` environment variable to match the internal port dynamically assigned by Render.
-   **Railway:** Exceptional support for `uv` out of the box. It will automatically detect your configuration if it spots your `pyproject.toml`. 

## Our setup (SketchDynamics on Render, domain on DreamHost)

Decided Oct 19: host on **Render** from this repo, and point a DreamHost subdomain at it. DreamHost shared hosting runs Python through Passenger, which expects WSGI apps, while ours is ASGI (FastAPI). It would also likely buffer the live NDJSON stream and time out 30–90 s generations. A DreamHost VPS or DreamCompute server would work, but needs more upkeep than Render.

### Deploy

1. Commit and push. Render reads [`render.yaml`](../render.yaml) at the repo root; the repo can stay private.
2. render.com → **New → Blueprint** → connect `1iyiwei/motion-dsl-nebius-hackathon`. The Blueprint sets:
   - build: `pip install uv && uv sync --frozen --no-dev` (no `uv cache prune --ci` needed: that only trims a cache CI saves between runs, and Render doesn't keep uv's cache)
   - start: `uv run --frozen --no-dev sketchdynamics --host 0.0.0.0 --port $PORT --no-browser`
   - health check: `/healthz`
   - guardrails: `SKETCHDYNAMICS_RATE_LIMIT=30` per visitor per hour, `SKETCHDYNAMICS_DAILY_TOKENS=2000000`, `SKETCHDYNAMICS_BEHIND_PROXY=1`
3. When asked, enter the secrets `NEBIUS_API_KEY` and `SKETCHDYNAMICS_ACCESS_CODE`. They're never committed.
4. Open the `*.onrender.com` URL, enter the access code next to the title, and run one generate to check the key and the stream.

### Custom domain (DreamHost DNS)

1. Render service → **Settings → Custom Domains** → add e.g. `sketchdynamics.<your-domain>`.
2. DreamHost panel → **Domains → DNS** for that domain → add a **CNAME** record: name `sketchdynamics`, value the `*.onrender.com` host Render shows. Remove any existing A record for that subdomain first.
3. Back in Render, wait for the domain to verify; Render then issues the HTTPS certificate on its own. HTTPS matters: browsers allow the mic only on secure pages.

### Before and during judging (Dec 1–15)

- The free plan sleeps after about 15 idle minutes (the first visit then takes about a minute). Consider a paid plan for Dec 1–15, or at least mention the wait in the submission notes.
- Put the demo URL and the access code in the judges-only answer on the submission form.
- Check that the Token Factory credits cover the daily token budget for the whole judging window.
- Don't push breaking changes to `main` during judging. Render redeploys on push unless auto-deploy is turned off in the service settings.
- Counters (rate limit, budget) live in memory and reset when the service restarts or wakes, so the access code is the real gate.
