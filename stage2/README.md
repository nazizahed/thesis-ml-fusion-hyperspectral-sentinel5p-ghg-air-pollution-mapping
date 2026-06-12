# Stage 2: Monthly Bivariate Mapping and Plume Matching

Stage 2 builds monthly Sentinel-5P methane context maps over the conterminous
United States and matches Carbon Mapper plume observations to the map for
their acquisition month.

The workflow links each plume point to monthly observation-support and methane
exceedance classes. It does not validate an individual Carbon Mapper plume
with Sentinel-5P.

## Main Notebook

```text
stage2_complete_bivariate_pipeline_with_plume_matching.ipynb
```

## Workflow

1. Authenticate Google Earth Engine.
2. Define the CONUS study area and a fixed 0.01-degree WGS84 export grid.
3. Build monthly Sentinel-5P valid-observation count (`Vc`) rasters.
4. Build monthly exceedance count (`Ec`) rasters using XCH4 > 1900 ppb.
5. Calculate optional monthly mean XCH4 rasters.
6. Encode fixed-bin bivariate classes, including NoData and NoEx.
7. Optionally submit monthly GeoTIFF exports to Google Drive.
8. Optionally reconstruct bivariate rasters locally from `Vc` and `Ec`.
9. Load and standardize a Carbon Mapper CH4 plume catalogue.
10. Sample the matching year-month bivariate raster at every plume point.
11. Produce NoData/NoEx/exceedance, emission, sector, and instrument
    summaries.

The full method and class encoding are described in
[../docs/stage2-technical-reference.md](../docs/stage2-technical-reference.md).

## Requirements

- Python 3.10-3.12 is recommended.
- Packages in the repository-level `requirements.txt`.
- Google Earth Engine access.
- Google Drive storage for Earth Engine exports.
- A Carbon Mapper plume CSV from Stage 0 or another catalogue export.
- Sufficient Drive capacity for monthly CONUS GeoTIFF products.

Install dependencies from the repository root:

```bash
python -m pip install -r requirements.txt
```

## Required Plume Input

The notebook searches for common column variants.

Required concepts:

| Concept | Accepted examples |
| --- | --- |
| Longitude | `plume_longitude`, `longitude`, `lon`, `source_longitude` |
| Latitude | `plume_latitude`, `latitude`, `lat`, `source_latitude` |
| Time | `datetime`, `acquired`, `timestamp`, `time`, `date` |

Optional concepts:

| Concept | Accepted examples |
| --- | --- |
| Gas | `gas`, `gas_norm`, `species` |
| Emission | `emission_auto`, `emission_rate`, `source_rate`, `emissions` |
| Sector | `ipcc_sector`, `sector`, `source_sector`, `sector_name` |
| Instrument | `instrument`, `platform`, `instrument_platform` |

When no gas column exists, the notebook assumes records are CH4. Otherwise,
it normalizes gas names and retains only CH4. Records also must contain valid
coordinates and timestamps between `START_YEAR` and `END_YEAR`.

## Configuration

Edit the `USER CONFIGURATION` cell before running the notebook.

### Earth Engine

Set an authorized project if your Earth Engine setup requires one:

```python
EE_PROJECT = "your-google-cloud-project-id"
```

The Sentinel-5P defaults are:

```python
S5P_COLLECTION = "COPERNICUS/S5P/OFFL/L3_CH4"
S5P_BAND = "CH4_column_volume_mixing_ratio_dry_air_bias_corrected"
XCH4_THRESHOLD_PPB = 1900
```

### Study period

```python
START_YEAR = 2023
END_YEAR = 2025
```

The export loop includes all 12 months in every configured year. The default
period therefore covers 36 months.

### Paths

The supplied paths target Google Colab and mounted Google Drive:

```python
GEE_DRIVE_FOLDER = "stage2_bivariate_exports"
LOCAL_RASTER_DIR = Path("/content/drive/MyDrive/stage2_bivariate_exports")
PLUME_CSV = Path("/content/drive/MyDrive/path_to_your_carbon_mapper_plumes.csv")
OUT_DIR = Path("/content/drive/MyDrive/stage2_bivariate_outputs")
```

Update `PLUME_CSV` to the real plume catalogue path. For local execution,
replace the `/content/drive/...` paths with local directories.

### Execution switches

```python
RUN_GEE_EXPORTS = False
REBUILD_BIVAR_LOCALLY = True
```

Keep `RUN_GEE_EXPORTS = False` while reviewing configuration. Setting it to
`True` starts four Drive export tasks per month:

- valid-observation count;
- exceedance count;
- monthly mean XCH4;
- bivariate class.

The default 36-month period submits 144 Earth Engine tasks. Check Earth
Engine task quotas, Drive capacity, and export configuration before enabling
the switch.

`REBUILD_BIVAR_LOCALLY = True` recreates a bivariate raster when matching
valid-count and exceedance-count rasters are available and a bivariate file
does not already exist.

## Running

From the repository root:

```bash
jupyter lab stage2/stage2_complete_bivariate_pipeline_with_plume_matching.ipynb
```

Recommended sequence:

1. Configure project, dates, paths, and threshold.
2. Run Earth Engine initialization.
3. Keep exports disabled and inspect the product definitions.
4. Enable exports only when ready and submit the monthly tasks.
5. Wait for Earth Engine tasks to complete in Google Drive.
6. Confirm `LOCAL_RASTER_DIR` contains the exported GeoTIFFs.
7. Run local reconstruction and raster code QA.
8. Set `PLUME_CSV` and run plume standardization.
9. Sample monthly rasters and generate summaries.

Earth Engine exports are asynchronous. Submitting tasks does not mean files
are immediately ready for local sampling.

## Bivariate Classes

Count classes are fixed:

| Class | Monthly count |
| ---: | ---: |
| 0 | No count |
| 1 | 1-6 |
| 2 | 7-12 |
| 3 | 13-18 |
| 4 | 19 or more |

Raster codes:

| Codes | Meaning |
| --- | --- |
| `0` | NoData: no valid Sentinel-5P support |
| `1-16` | Cross of `Vc1-Vc4` and `Ec1-Ec4` |
| `21-24` | NoEx: valid support with zero exceedances |

Examples:

- `1` means `Vc1-Ec1`.
- `8` means `Vc2-Ec4`.
- `16` means `Vc4-Ec4`.
- `23` means `Vc3-NoEx`.

## Outputs

### Earth Engine GeoTIFFs

```text
S5P_N_valid_YYYY_MM_CONUS.tif
S5P_N_exceed_YYYY_MM_CONUS.tif
S5P_XCH4_mean_YYYY_MM_CONUS.tif
bivariate_custombins_YYYY_MM_CONUS_K4plus.tif
```

### Local tables

```text
bivariate_raster_code_QA.csv
carbon_mapper_plumes_sampled_by_monthly_bivariate_class.csv
noex_vs_non_noex_emission_summary.csv
sector_counts_by_bivariate_class.csv
instrument_counts_by_bivariate_class.csv
```

Optional summaries are written only when their source columns exist and
contain usable values.

### Figures

```text
heatmap_plume_counts_by_bivariate_class.png
heatmap_emission_sums_by_bivariate_class.png
sector_composition_by_bivariate_class.png
```

Generated Stage 2 output directories are ignored by Git.

## Interpretation

- `Vc` measures monthly valid Sentinel-5P observation support.
- `Ec` counts valid observations above the fixed 1900 ppb threshold.
- NoEx means valid monthly support but zero threshold exceedances.
- NoData means no usable support or no matching monthly raster at the sampled
  plume location.
- Matching is by plume point and acquisition month, not by exact overpass
  time.
- Emission values are summed without unit conversion. Their units and quality
  remain those of the source Carbon Mapper field.
- Sector and instrument results describe the input plume catalogue and its
  sampling, not the full population of emission sources.

## Troubleshooting

**Earth Engine initialization fails**

Confirm that your Google account has Earth Engine access and that `EE_PROJECT`
is an authorized Cloud project.

**Exports do not appear locally**

Earth Engine tasks may still be queued or running. Check task status and the
configured Google Drive folder. Drive mounting or synchronization can also
delay local visibility.

**Missing valid/exceedance raster**

Verify both monthly files use the expected filename prefixes. Local bivariate
reconstruction requires matching shape, CRS, and affine transform.

**Missing bivariate months**

The plume month has no matching
`bivariate_custombins_YYYY_MM_CONUS_K4plus*.tif` file. Those records remain
unclassified.

**Most plume records are NoData**

Check raster coverage, nodata encoding, coordinate order, source dates,
monthly file availability, and whether plume locations fall inside CONUS.

**No emission summary**

No recognized emission column was found, or all emission values converted to
null. Plume-count summaries still run.

**No sector or instrument summary**

The input CSV lacks a recognized sector or instrument/platform column.
