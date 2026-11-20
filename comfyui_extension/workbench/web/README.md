# Workbench sidebar panel (placeholder)

This directory is registered to ComfyUI through `WEB_DIRECTORY = "./web"` in
`../__init__.py`. The management-side panel (drafts, assets, LoRA metadata,
rule editing) will be implemented here as a ComfyUI frontend extension.

It talks to the existing workbench backend over loopback; no business data is
stored inside the ComfyUI install.
