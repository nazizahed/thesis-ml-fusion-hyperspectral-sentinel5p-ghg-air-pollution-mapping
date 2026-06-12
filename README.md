# Machine Learning Fusion of Hyperspectral-derived and Sentinel-5P Data for Greenhouse Gas and Air Pollution Mapping

This repository contains the reproducible software workflows developed for a
thesis on combining high-resolution hyperspectral greenhouse-gas plume
observations with coarse-resolution Sentinel-5P atmospheric measurements.

The current implementation focuses on Carbon Mapper/Tanager methane plume
data and Sentinel-5P XCH4. It provides data access, plume prioritization,
cross-sensor temporal analysis, monthly bivariate mapping, and plume-to-raster
matching.

## Research Workflow

**Stage 0: Carbon Mapper data access**

Produces the shared Carbon Mapper plume CSV.

**Stage 0 output feeds Stage 1: Cross-sensor visibility**

Produces ranked Tanager plume events and daily Sentinel-5P context.

**Stage 0 output feeds Stage 2: Monthly bivariate matching**

Produces monthly Sentinel-5P classes and plume-level class summaries.

## Implemented Stages

| Stage | Purpose | Main entry point | Documentation |
| --- | --- | --- | --- |
| **Stage 0** | Query, inspect, map, and export Carbon Mapper CH4/CO2 plume records. | `stage0/launch_carbonmapper_dashboard_only.ipynb` | [User guide](stage0/README.md), [technical reference](docs/technical-reference.md) |
| **Stage 1** | Rank Tanager CH4 plumes, associate nearby events, and compare them with daily Sentinel-5P context. | `stage1/stage1_cross_sensor_visibility_final.ipynb` | [User guide](stage1/README.md), [technical reference](docs/stage1-technical-reference.md) |
| **Stage 2** | Create monthly Sentinel-5P observation/exceedance classes and match Carbon Mapper plumes to them. | `stage2/stage2_complete_bivariate_pipeline_with_plume_matching.ipynb` | [User guide](stage2/README.md), [technical reference](docs/stage2-technical-reference.md) |

## Repository Structure

```text
.
|-- stage0/
|   |-- README.md
|   |-- cm_app_carbonmapper_only.py
|   `-- launch_carbonmapper_dashboard_only.ipynb
|-- stage1/
|   |-- README.md
|   `-- stage1_cross_sensor_visibility_final.ipynb
|-- stage2/
|   |-- README.md
|   `-- stage2_complete_bivariate_pipeline_with_plume_matching.ipynb
|-- docs/
|   |-- technical-reference.md
|   |-- stage1-technical-reference.md
|   `-- stage2-technical-reference.md
|-- .gitignore
|-- README.md
`-- requirements.txt
```

Generated datasets, rasters, figures, and maps are intentionally excluded
from Git. Each stage README describes its expected inputs and outputs.

## Installation

Python 3.10-3.12 is recommended.

### Windows PowerShell

```powershell
git clone https://github.com/nazizahed/thesis-ml-fusion-hyperspectral-sentinel5p-ghg-air-pollution-mapping.git
cd thesis-ml-fusion-hyperspectral-sentinel5p-ghg-air-pollution-mapping
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### macOS or Linux

```bash
git clone https://github.com/nazizahed/thesis-ml-fusion-hyperspectral-sentinel5p-ghg-air-pollution-mapping.git
cd thesis-ml-fusion-hyperspectral-sentinel5p-ghg-air-pollution-mapping
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Geospatial packages such as GeoPandas and Rasterio depend on compiled
libraries. A Conda environment may be easier on systems where `pip`
installation fails.

## External Access

The workflows require some external services:

- **Stage 0:** a Carbon Mapper API token.
- **Stage 1:** Carbon Mapper raster access and Google Earth Engine.
- **Stage 2:** Google Earth Engine and Google Drive for monthly GeoTIFF
  exports.

No API token, password, or Earth Engine credential is stored in this
repository. Follow the authentication instructions in the relevant stage
guide.

## Running a Stage

Launch the required notebook from the repository root:

```bash
# Stage 0
jupyter lab stage0/launch_carbonmapper_dashboard_only.ipynb

# Stage 1
jupyter lab stage1/stage1_cross_sensor_visibility_final.ipynb

# Stage 2
jupyter lab stage2/stage2_complete_bivariate_pipeline_with_plume_matching.ipynb
```

Run only one command at a time. Configure the paths, credentials, Earth
Engine project, dates, and execution switches described in that stage's
README before running all cells.

## Data Handoff

Stage 0 exports Carbon Mapper plume CSV files. These CSVs are the principal
input to Stages 1 and 2.

Typical shared fields include:

```text
plume_latitude
plume_longitude
datetime
gas
platform
plume_tif
con_tif
emission_auto
ipcc_sector
instrument
```

Stage 1 requires the linked plume and concentration rasters for mask-based
statistics. Stage 2 primarily requires coordinates, timestamps, and gas, with
emission, sector, and instrument fields used for optional summaries.

## Scientific Scope

The workflows support exploratory and reproducible cross-sensor analysis.
They should not be presented as direct validation of an individual
high-resolution plume by Sentinel-5P.

Important distinctions:

- Carbon Mapper/Tanager and Sentinel-5P have different spatial resolutions,
  sampling times, retrieval methods, and measurement support.
- A plume catalogue is not a complete census of emission sources.
- Plume counts do not necessarily represent unique facilities or independent
  emission events.
- Stage 1 comparisons represent coarse temporal and spatial co-visibility.
- Stage 2 classes represent monthly Sentinel-5P context at plume point
  locations.
- Any emission values retain the units and limitations of their source
  fields unless a stage explicitly documents a conversion.

## Reproducibility

For thesis results, record:

- repository commit hash;
- input filenames and checksums;
- query or study dates;
- geographic domain;
- algorithm parameters and thresholds;
- Earth Engine collection, band, project, and export settings;
- software package versions;
- missing data, failed raster operations, and excluded records.

The stage-specific technical references provide more detailed checklists and
methodological limitations.

## Data Attribution

Users are responsible for complying with the access, licensing, citation, and
attribution requirements of Carbon Mapper, Sentinel-5P/Copernicus, Google
Earth Engine, Natural Earth, TIGER/Line, and any other source data used in an
analysis.
