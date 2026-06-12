# Carbon Mapper Dashboard Thesis

This repository contains the implementation artifacts for the Carbon Mapper
dashboard component of the thesis.

## Stage 0

The first stage provides a Jupyter-based interactive dashboard for exploring
Carbon Mapper methane and carbon dioxide plume data.

### Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
jupyter lab stage0/launch_carbonmapper_dashboard_only.ipynb
```

The dashboard requires a Carbon Mapper API token. The notebook prompts for the
token at runtime and keeps it out of the repository. Alternatively, set the
`CM_TOKEN` environment variable before launching Jupyter.

### Stage 0 files

- `stage0/cm_app_carbonmapper_only.py`: dashboard UI and analysis module.
- `stage0/launch_carbonmapper_dashboard_only.ipynb`: notebook launcher.
