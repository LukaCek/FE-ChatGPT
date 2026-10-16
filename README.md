# FE Gradiva – ChatGPT MCP for TrueNAS

Private, read-only FE study-materials MCP server for TrueNAS SCALE 25.10. Syncthing continues managing the FE dataset. Google Drive is unnecessary.

**Release:** `1.1.0` (do not assume deployed on TrueNAS until you explicitly update the app).

## Features

- Read `.pdf`, `.md`, `.txt`, `.py`, `.tex`, `.csv`, `.json` and `.log` files from one restricted FE root.
- Index document text and handwritten-page occupancy in persistent SQLite FTS5; inotify plus 180-second fallback rescan.
- Skip unfilled pages of large square-grid notebooks. Contents of handwriting are **not OCR'd**; manually inspected pages may be labeled in the index.
- Find documents by subject/type, report ambiguous names and penalize empty 1000-page templates (`resolve_document`).
- Find exact exercise references (`find_exercise`); image-only pages explicitly require visual verification.
- Render a full PDF page (`view_pdf_page`) or a normalized 0–1 crop (`view_pdf_region`) as bounded JPEG/PNG.
- File-change dates are **not** evidence of studying; page comparisons only track changes observed *since initial indexing*.
- Nothing writes to the FE dataset; optional `annotate_page` saves labels *only to the separate SQLite index*.

## TrueNAS deployment

1. Leave existing v1.0 running until the image is published and tests are green.
2. In **Apps → Installed**, back up the working Compose configuration of `fe-gradiva-chatgpt`.
3. Ensure these paths exist on the TrueNAS host:

   ```sh
   sudo install -d -m 750 /mnt/tank/apps/fe-mcp/index
   sudo test -d /mnt/tank/sync/FE && echo 'FE folder OK'
   sudo test -s /mnt/tank/apps/fe-mcp/secrets/openai_api_key && echo 'Tunnel key OK'
   ```
4. Copy [deploy/truenas-compose.yaml](deploy/truenas-compose.yaml) to **Apps → Installed → fe-gradiva-chatgpt → Edit**, replacing `tunnel_TVOJ_ID` with your already-created OpenAI tunnel ID. Preserve the secret **as a file**; never paste API keys into YAML/GitHub.
5. `Save` to start `ghcr.io/lukacek/fe-chatgpt:1.1.0` (the workflow builds from `Dockerfile` on pushes to `main`). The GHCR package must be public for anonymous pull; if private, authenticate TrueNAS against GHCR before updating.
6. Verify `fe-server` shows healthy and `tunnel-client` shows Up. ChatGPT → Plugins → FE Gradiva → **Refresh** to discover the new tools.

   ```sh
   sudo docker ps -a --filter name=ix-fe-gradiva-chatgpt
   sudo docker logs --tail 80 ix-fe-gradiva-chatgpt-fe-server-1
   sudo docker logs --tail 80 ix-fe-gradiva-chatgpt-tunnel-client-1
   ```

### Rollback

Restore the backed-up v1.0 YAML in the same TrueNAS app and Save. The new index is only in `/mnt/tank/apps/fe-mcp/index`; it is independent of the source FE folder. **Do not run `docker system prune --volumes`**. Do not start two tunnel clients with the same tunnel ID.

## API tools

`server_info`, `index_status`, `refresh_index`, `list_files`, `recent_files`, `recent_activity`, `file_info`, `populated_pages`, `search_files`, `find_study_context`, `resolve_document`, `find_exercise`, `read_document`, `view_pdf_page`, `view_pdf_region`, `annotate_page`.

**Source verification:** If a PDF is scanned or handwritten, the tools cannot know exercise text without viewing the original image; `find_exercise` will not invent a match. Responses should cite `path` and `page`.

## Local development

```sh
python -m pip install -r requirements.txt pytest
pytest -q
docker build -t fe-chatgpt:local .
```

GitHub Actions checks Python tests and container build. On a successful push to main, it publishes the `1.1.0` and `latest` tags to `ghcr.io/lukacek/fe-chatgpt` using `GITHUB_TOKEN` with `packages: write`. No API secret is stored in the repository. When changing code after release, use a new immutable version tag rather than silently replacing 1.1.0.

### Security

The TrueNAS FE mount is read-only. No ports are published on the host. A dedicated internal Docker network connects the MCP service and tunnel client; only the tunnel client reaches the external OpenAI tunnel. Indexed text and page annotations remain on NAS in the mounted SQLite volume. Requests to the MCP plugin share selected content with ChatGPT. Restrict access to your ChatGPT account and tunnel credentials.
