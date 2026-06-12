# Machine Learning Fusion of Hyperspectral-derived and Sentinel-5P Data for Greenhouse Gas and Air Pollution Mapping

An interactive Jupyter dashboard for querying, exploring, mapping, and
summarizing Carbon Mapper methane (CH4) and carbon dioxide (CO2) plume
observations.

This repository currently contains **Stage 0** of the thesis implementation.
Stage 0 is a Carbon Mapper data-access and exploratory-analysis component. It
does not contain Sentinel-5P/TROPOMI processing, Google Earth Engine matching,
or formal validation of emission estimates.

## Main Features

- Query Carbon Mapper plume metadata by country, optional state/province,
  date range, and gas.
- Preserve the raw API response as CSV.
- Assign plume coordinates to first-level administrative units using a
  point-in-polygon spatial join.
- Calculate plume counts and available area/emission summary statistics.
- Display bar charts, plume-area histograms, choropleth maps, plume points,
  clusters, and heatmaps.
- Optionally estimate plume area from linked Carbon Mapper raster products.
- Export state/province statistics and interactive Folium maps.

## Repository Structure

```text
.
|-- stage0/
|   |-- cm_app_carbonmapper_only.py
|   `-- launch_carbonmapper_dashboard_only.ipynb
|-- docs/
|   `-- technical-reference.md
|-- .gitignore
|-- README.md
`-- requirements.txt
```

The implementation details, processing assumptions, and output fields are
documented in [docs/technical-reference.md](docs/technical-reference.md).

## Requirements

- Python 3.10-3.12 is recommended.
- JupyterLab, Jupyter Notebook, or Google Colab.
- A Carbon Mapper API token.
- Internet access to the Carbon Mapper API, Natural Earth boundary archives,
  and linked raster products when area estimation is enabled.

Geospatial Python packages may require platform-specific binary libraries.
Using Conda is often easier if `pip` cannot install GeoPandas or Rasterio.

## Local Installation

### Windows PowerShell

```powershell
git clone https://github.com/nazizahed/thesis-ml-fusion-hyperspectral-sentinel5p-ghg-air-pollution-mapping.git
cd thesis-ml-fusion-hyperspectral-sentinel5p-ghg-air-pollution-mapping
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
jupyter lab stage0/launch_carbonmapper_dashboard_only.ipynb
```

### macOS or Linux

```bash
git clone https://github.com/nazizahed/thesis-ml-fusion-hyperspectral-sentinel5p-ghg-air-pollution-mapping.git
cd thesis-ml-fusion-hyperspectral-sentinel5p-ghg-air-pollution-mapping
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
jupyter lab stage0/launch_carbonmapper_dashboard_only.ipynb
```

Run the notebook cells from top to bottom. The installation cell inside the
notebook is primarily intended for Colab and can be skipped after installing
`requirements.txt` locally.

## API Token

The repository does not contain an API token. The notebook asks for one using
a hidden prompt and stores it only in the current Python process.

Alternatively, set `CM_TOKEN` before starting Jupyter:

```powershell
# Windows PowerShell, current terminal session only
$env:CM_TOKEN = "your-token"
jupyter lab stage0/launch_carbonmapper_dashboard_only.ipynb
```

```bash
# macOS/Linux, current shell session only
export CM_TOKEN="your-token"
jupyter lab stage0/launch_carbonmapper_dashboard_only.ipynb
```

Do not put a real token in the notebook, Python module, `.env` files committed
to Git, screenshots, or issue reports.

## Using the Dashboard

1. Run all notebook cells through the cell that calls `app.display()`.
2. Select a country. The dashboard downloads the corresponding Natural Earth
   ADM1 state/province list.
3. Optionally select one or more states/provinces. Leave the list empty to use
   the whole country.
4. Choose a start date and end date.
5. Select CH4, CO2, or both gases.
6. Keep **Max plumes** small for an initial test, such as 500.
7. Leave **Estimate plume areas** disabled for the fastest first run.
8. Click **Fetch & Analyze**.
9. Use the chart and map controls after the status area reports that data was
   loaded.

A useful first test is the United States, CH4, a one-month date interval, no
state selection, and a 500-plume processing limit.

### Controls

| Control | Behavior |
| --- | --- |
| Country | Selects the country used to load ADM0/ADM1 boundaries. |
| States/Prov | Restricts the bounding query and later statistics; empty means all. |
| Start / End | Defines the API datetime interval sent by the app. |
| Gases | Filters the downloaded records to CH4 and/or CO2. |
| Max plumes | Caps rows processed in memory after download; it does not cap the API download. |
| Estimate plume areas | Downloads raster products and calculates exploratory area estimates. |
| Estimate areas now | Runs area estimation after plume metadata has already loaded. |
| Top N | Controls how many administrative units appear in charts/tables. |
| Show points / Cluster | Controls individual plume markers on the Folium map. |
| Heatmap | Adds a plume-density layer, area-weighted when area estimates exist. |
| Max points | Caps only the number of point markers rendered on the map. |

For multi-select widgets, use `Ctrl` on Windows/Linux or `Command` on macOS to
select or deselect multiple values.

## Generated Outputs

Raw API responses are written under:

```text
cm_app_outputs/plumes_<bbox>_<start>_<end>.csv
```

The folder is ignored by Git. The dashboard's **Raw CSV** panel lists these
files and provides a download action in Google Colab.

After data has loaded, notebook users can also run:

```python
app.export_state_stats(
    path="carbon_mapper_state_stats_ch4.csv",
    gas="CH4",
)

app.save_map(
    path="carbon_mapper_ch4_map.html",
    gas="CH4",
    metric="plume_count",
)
```

Valid map metrics depend on the returned data and may include
`plume_count`, `area_mean_km2`, `area_sum_km2`, and `emission_total`.

## Scientific Interpretation

- A plume count is a count of returned plume records, not a count of unique
  facilities or independent emission events.
- The optional plume-area calculation is a threshold-based exploratory
  estimate. It is not an official Carbon Mapper area product and should not be
  treated as validated plume geometry.
- `emission_auto` is aggregated only when that field is present in the API
  response. Units and interpretation must be taken from the source dataset
  metadata; the dashboard does not convert units.
- Administrative assignment uses the reported plume point and the `within`
  spatial predicate. Points on boundaries or outside the boundary dataset may
  remain unassigned.
- Results depend on API availability, catalog coverage, selected dates,
  boundary data, and the current Carbon Mapper response schema.

## Troubleshooting

**401 Unauthorized**

The token is missing, expired, rejected, or lacks access. Restart the runtime
and enter a fresh token.

**No plumes for this selection**

Try a wider date interval, another gas, fewer geographic restrictions, or
verify that the selected area has catalog coverage.

**Could not load ADM1 list**

Check internet access to Natural Earth. Country names must match names in the
boundary dataset.

**Downloaded CSV missing `plume_latitude` or `plume_longitude`**

The API response schema is incompatible with this Stage 0 implementation.
Preserve the raw CSV and report the schema change.

**Area values are empty**

The record may not include a usable `con_tif` or `plume_tif` URL, the raster
may be unavailable, or raster processing may have failed.

**Widgets do not appear**

Restart the kernel, confirm `ipywidgets` is installed, and rerun the notebook.
In Colab, rerun the widget-manager setup cell.

## Data and Attribution

Users are responsible for following Carbon Mapper's access, attribution, and
data-use requirements. Administrative boundaries are loaded from Natural
Earth. Any thesis publication should identify the data access date, query
interval, geographic selection, gas, software commit, and whether exploratory
area estimation was enabled.
