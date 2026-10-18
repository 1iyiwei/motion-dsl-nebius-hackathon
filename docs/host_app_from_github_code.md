# Hosting app from GitHub code

https://share.google/aimode/837rvGCldWhtkgZjh

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