# Deploying deckpager to Railway

The repo is already Railway-shaped: `railway.json` (NIXPACKS builder, `/healthz` healthcheck),
`Procfile`, `requirements.txt`, and `runtime.txt` (python-3.12). Nothing needs to be built or
containerised by hand.

---

## One-time setup

```bash
railway login                 # opens a browser; --browserless prints a pairing code instead
railway init                  # creates the project, or `railway link` to attach an existing one
```

The CLI is installed at `C:\Users\user\bin\railway.exe` (v5.41.2), which is on PATH.

## Variables

Set these on the service — never in a committed file. `.env` is git-ignored and is for local
development only.

| Variable | Required | Value / default |
|---|---|---|
| `ANTHROPIC_API_KEY` | **yes** | the key from the Anthropic Console; the app refuses to start without it |
| `APP_PASSWORD` | **strongly recommended** | HTTP Basic password. **Unset means the app is open — anyone with the URL can upload a deck and spend your API key.** |
| `APP_USERNAME` | no | defaults to `ten` |
| `DECKPAGER_MODEL` | no | `claude-opus-5` (from `config/default.toml`) |
| `DECKPAGER_EFFORT` | no | `high` |
| `MAX_CONCURRENT_JOBS` | no | `2` — each concurrent job is a paid API call |
| `MAX_UPLOAD_MB` | no | `25` |
| `JOB_TTL_MINUTES` | no | `180` — how long results stay downloadable |
| `RESEND_API_KEY` | no | set only if results should be emailed; the sending domain must be verified with Resend |

```bash
railway variables --set "ANTHROPIC_API_KEY=sk-ant-..." --set "APP_PASSWORD=..."
```

## Deploy

```bash
railway up                    # builds and deploys the current directory
railway domain                # generates the public *.up.railway.app URL
railway logs                  # follow the build and runtime logs
```

## Verify

```bash
curl -s https://<your-domain>/healthz
# {"ok":true,"version":"0.1.0","api_key_configured":true,"auth_enabled":true,"jobs":0}
```

`api_key_configured: true` and `auth_enabled: true` are the two that matter. Then open the URL,
sign in with `APP_USERNAME` / `APP_PASSWORD`, and upload a deck.

---

## Operational notes

- **A run takes minutes, not seconds.** The upload returns a job id immediately and the browser
  polls; the HTTP request is never held open, so Railway's proxy timeout can't kill paid work.
- **The filesystem is ephemeral.** Jobs and the extraction cache live in temp storage and do not
  survive a redeploy — results must be downloaded, not parked on the server. Attach a volume and
  point `JOBS_DIR` at it if results need to outlive a deploy.
- **Cost per deck** is roughly $0.15–$0.50 against Opus 5 at effort `high`, depending on slide count
  and how many slides carry images. `MAX_CONCURRENT_JOBS` is the only spend throttle — raise it
  deliberately.
- **No system packages are needed.** The renderer is ReportLab, so the build does not need
  pango/cairo/gdk-pixbuf. LibreOffice is absent on Railway, so `.ppt` uploads will fail with a clear
  message — `.pdf` and `.pptx` are unaffected.
- **Rotate the API key** if it has ever been pasted into a chat, an issue, or a commit.
