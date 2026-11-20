# Workbench sidebar panel

This directory is registered to ComfyUI through `WEB_DIRECTORY = "./web"` in
`../__init__.py`.

- `workbench_panel.js` — registers one sidebar tab. It probes the local
  workbench backend over loopback (`/api/health`, unauthenticated) and links to
  the full workbench web app. The backend CORS allow-list includes the ComfyUI
  loopback origins (`127.0.0.1:8188`, `localhost:8188`) so the probe can read
  the response.

Drafts, assets, LoRA metadata and rule editing stay in the workbench frontend.
This panel is a thin read-only shell; a read-only browser session for richer
read-only views is a later slice.
