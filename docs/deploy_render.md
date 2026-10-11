# Deploying the dashboard on Render

The Streamlit app in `app/` is a public web service on Render's free plan.
It reads only the small pre-aggregated files in `app/data/` (about 0.1 MB), so
**no database, no `.env` and no secrets are needed** on Render.

## How the pieces fit

| File | Role |
|---|---|
| `app/streamlit_app.py` | The dashboard (4 pages) |
| `app/data/` | Pre-aggregated files the app reads (committed to git) |
| `app/requirements.txt` | The app's packages only: streamlit, altair, pandas, pyarrow |
| `app/.streamlit/config.toml` | Theme colors, headless mode, no usage statistics |
| `render.yaml` | Render "Blueprint": tells Render how to build and start the app |
| `analysis/build_app_data.py` | Rebuilds `app/data/` from the database (run locally, never on Render) |

## 0. Check it locally first

```powershell
# from the project root, in the project venv (or a fresh one)
pip install -r app/requirements.txt
streamlit run app/streamlit_app.py
```
Open http://localhost:8501 and click through the four pages.

Optional automated check (21 tests, no browser needed):
```powershell
pip install pytest
pytest app/test_app.py
```

## 1. Push the code to GitHub

Render deploys from a Git repository. If you haven't yet, follow the GitHub steps
from earlier (create an empty repo, `git remote add origin ...`, `git push -u origin main`).
Make sure `app/data/*.parquet` and `app/data/summary.json` are in the push:

```powershell
git ls-files app/data
```
should list 4 files. (The `.gitignore` blocks other `.parquet` files but makes an
exception for these.)

## 2. Create the service from the Blueprint

1. Sign in at https://dashboard.render.com (signing in with GitHub is simplest).
2. Click **New +** → **Blueprint**.
3. Connect your GitHub account if asked, and pick the `nyc-311-analysis` repository.
4. Render reads `render.yaml` and shows one web service, **nyc-311-dashboard**,
   on the **Free** plan. Click **Apply** (or **Deploy Blueprint**).
5. You will **not** be asked for any environment variables or secrets. The only
   setting, `PYTHON_VERSION = 3.13.5`, is already in `render.yaml`.

## 3. Watch the first deploy

Open the service and the **Logs** tab. You should see, in order:
1. `pip install -r requirements.txt` installing the four packages (a few minutes).
2. `streamlit run streamlit_app.py --server.port ... --server.address 0.0.0.0`.
3. The service turns **Live** once the health check at `/_stcore/health` answers.

Your public URL is at the top of the page, like
`https://nyc-311-dashboard.onrender.com` (Render adds a suffix if the name is taken).

## 4. Add the link to your README

Replace the placeholder in the README's "Live dashboard" line with your URL, then:
```powershell
git add README.md
git commit -m "Add live dashboard link"
git push
```
Each push to the branch redeploys automatically (`autoDeployTrigger: commit`).

## Updating the data later

The app only changes when `app/data/` changes. After rerunning the pipeline:
```powershell
python analysis/build_app_data.py
git add app/data
git commit -m "Refresh dashboard data"
git push
```

## Things to know

- **Free plan sleeps.** A free web service spins down after a period with no
  visitors; the next visit takes about a minute to wake it. Fine for a portfolio.
  Say so if you share the link in an interview.
- **Why `--server.address 0.0.0.0`:** Render sends traffic to the port in `$PORT`
  on the container's network interface. By default Streamlit only listens to
  `localhost`, which Render can't reach.
- **Why a separate `app/requirements.txt`:** the deployed app doesn't need
  PostgreSQL drivers, lifelines or scikit-learn. A smaller install builds faster
  and leaves nothing that could ask for credentials.
- **If the build fails on Python:** Render requires a full version in
  `PYTHON_VERSION` (e.g. `3.13.5`). Change it in `render.yaml` and push.
