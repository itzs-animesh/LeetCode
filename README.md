# RankFinder

A small LeetCode contest rank lookup app. It serves the UI and API from one Python web service, so deploy it as a **Web Service** (not a Static Site).

## Deploy to Render with the included Blueprint

1. Push this repository to GitHub, GitLab, or Bitbucket.
2. In Render, choose **New → Blueprint** and select the repository.
3. Render reads `render.yaml`, installs `requirements.txt`, verifies the UI files exist, starts `python server.py`, and checks `/api/health`.
4. Confirm the deployment is live, then open the generated `onrender.com` URL.

Make sure the repository contains all three files under `public/`: `index.html`, `app.js`, and `styles.css`. The Blueprint build check stops deployment with a clear failure if any are missing.

## Configure a Web Service manually

If you prefer not to use the Blueprint, create a **Web Service** and set:

- **Runtime:** Python 3
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python server.py`
- **Health check path:** `/api/health`

No `PORT` value needs to be configured: Render supplies it, and the server binds to `0.0.0.0` and reads that environment variable automatically. `.python-version` selects Python 3.13.

## Run locally

```bash
python -m pip install -r requirements.txt
python server.py
```

Then visit `http://localhost:3000`.
