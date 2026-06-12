"""
cm_app_carbonmapper_only.py — Carbon Mapper notebook dashboard (UI + analysis)
----------------------------------------------------------------

This module is intentionally limited to Carbon Mapper data.
It does not use Sentinel-5P, TROPOMI, or Earth Engine matching.
It is suitable for the Stage 0 / interactive dashboard component of the thesis repository.
Features:
  • Pick country, (optional) states/provinces, date range, and gas (CH4/CO2)
  • Fetch plumes via Carbon Mapper API using a compact BBOX AOI (avoids HTTP 413)
  • Optional fast plume-area estimation (con_tif quantile threshold → vis RGBA fallback)
  • Per-state stats (count, area, emissions), bar charts, histogram
  • Choropleth map + plume distribution overlays (points and optional heatmap)
  • Raw CSV panel to list & download the original fetched CSVs
Usage:
  from cm_app_carbonmapper_only import CMApp
  app = CMApp(cm_token=os.environ.get("CM_TOKEN"))
  app.display()
"""
from __future__ import annotations

import os, json, warnings, tempfile, glob
from dataclasses import dataclass
from datetime import date
from typing import Optional, Iterable, Tuple

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import Point, shape as shp_shape, mapping as shp_mapping

import ipywidgets as w
from IPython.display import display, clear_output
import matplotlib.pyplot as plt
import folium
from folium.plugins import MarkerCluster, HeatMap

warnings.filterwarnings("ignore", message=".*unary_union.*")

def load_admin_boundaries(country_name: str, admin_level: int = 1):
    """Load administrative boundaries from Natural Earth only.

    This version uses only Carbon Mapper plume data and Natural Earth boundaries.
    """
    # Natural Earth fallback
    if admin_level == 0:
        urls = [
            "zip+https://naturalearth.s3.amazonaws.com/50m_cultural/ne_50m_admin_0_countries.zip",
            "zip+https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip",
        ]
        last = None
        for url in urls:
            try:
                gdf = gpd.read_file(url)
                for col in ["ADMIN","NAME","SOVEREIGNT","NAME_EN","ADMIN_EN"]:
                    if col in gdf.columns:
                        sub = gdf[gdf[col].str.lower()==country_name.lower()].copy()
                        if len(sub):
                            sub = sub.rename(columns={col:"admin_name"}).set_crs(4326)
                            return sub[["admin_name","geometry"]], "admin_name"
                last = f"{country_name} not found in {url}"
            except Exception as e:
                last = e
        raise RuntimeError(f"Natural Earth admin-0 load failed: {last}")
    else:
        urls = [
            "zip+https://naturalearth.s3.amazonaws.com/50m_cultural/ne_50m_admin_1_states_provinces.zip",
            "zip+https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_1_states_provinces.zip",
        ]
        last = None
        for url in urls:
            try:
                gdf = gpd.read_file(url)
                if "admin" in gdf.columns:
                    gdf = gdf[gdf["admin"].str.lower()==country_name.lower()].copy()
                name_col = next((c for c in ["name","name_en","name_local","provname"] if c in gdf.columns), None)
                if not name_col:
                    last = "no name col"; continue
                gdf = gdf.rename(columns={name_col:"admin_name"}).set_crs(4326)
                if len(gdf):
                    return gdf[["admin_name","geometry"]], "admin_name"
                last = "empty after filter"
            except Exception as e:
                last = e
        raise RuntimeError(f"Natural Earth admin-1 load failed: {last}")

# ------------------------- AOI + download -------------------------
def _to_wgs84_union(geodf: gpd.GeoDataFrame):
    try:
        epsg = geodf.crs.to_epsg() if geodf.crs else None
    except Exception:
        epsg = None
    gdf_wgs = geodf.to_crs(4326) if geodf.crs and epsg != 4326 else geodf
    return gdf_wgs.union_all() if hasattr(gdf_wgs, "union_all") else gdf_wgs.unary_union

def build_aoi_bbox(gdf_admin0: gpd.GeoDataFrame,
                   gdf_admin1: gpd.GeoDataFrame,
                   selected_names: Iterable[str] | None,
                   pad_deg: float = 0.0) -> dict:
    if selected_names:
        sel = gdf_admin1[gdf_admin1["admin_name"].isin(list(selected_names))]
        geom = _to_wgs84_union(sel)
    else:
        geom = _to_wgs84_union(gdf_admin0)
    minx, miny, maxx, maxy = geom.bounds
    if pad_deg:
        minx, miny, maxx, maxy = minx-pad_deg, miny-pad_deg, maxx+pad_deg, maxy+pad_deg
    return {"type":"Polygon","coordinates":[[[minx,miny],[maxx,miny],[maxx,maxy],[minx,maxy],[minx,miny]]]}

def to_geometry_geojson(aoi):
    if isinstance(aoi, dict):
        t = aoi.get("type","").lower()
        if t == "feature": return aoi["geometry"]
        if t == "featurecollection":
            geoms = [shp_shape(f["geometry"]) for f in aoi.get("features", []) if "geometry" in f]
            if not geoms: raise ValueError("Empty FeatureCollection")
            minx, miny, maxx, maxy = gpd.GeoSeries(geoms, crs=4326).total_bounds
            return {"type":"Polygon","coordinates":[[[minx,miny],[maxx,miny],[maxx,maxy],[minx,maxy],[minx,miny]]]}
        return aoi
    if isinstance(aoi, gpd.GeoDataFrame):
        minx, miny, maxx, maxy = aoi.to_crs(4326).total_bounds
        return {"type":"Polygon","coordinates":[[[minx,miny],[maxx,miny],[maxx,maxy],[minx,maxy],[minx,miny]]]}
    if isinstance(aoi, (tuple, list)) and len(aoi)==4:
        minx, miny, maxx, maxy = aoi
        return {"type":"Polygon","coordinates":[[[minx,miny],[maxx,miny],[maxx,maxy],[minx,maxy],[minx,miny]]]}
    try:
        from shapely.geometry.base import BaseGeometry
        if isinstance(aoi, BaseGeometry):
            return shp_mapping(aoi)
    except Exception:
        pass
    raise ValueError("Unsupported AOI format")

def download_carbonmapper_plumes(token: str, aoi, start_date: str, end_date: str, out_dir: str | os.PathLike) -> str:
    assert token, "Set CM token"
    os.makedirs(out_dir, exist_ok=True)
    geom = to_geometry_geojson(aoi)
    shp = shp_shape(geom)
    minx, miny, maxx, maxy = shp.bounds
    bbox_str = f"{miny:.2f}_{maxy:.2f}_{minx:.2f}_{maxx:.2f}".replace(".","p")
    date_str = f"{start_date.replace('-','')}_{end_date.replace('-','')}"
    out_csv = os.path.join(out_dir, f"plumes_{bbox_str}_{date_str}.csv")

    base = "https://api.carbonmapper.org/api/v1/catalog/plume-csv"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/csv",
    }
    params = {"intersects": json.dumps(geom), "datetime": f"{start_date}/{end_date}", "limit": 1, "offset": 0}

    r = requests.get(base, params=params, headers=headers, timeout=60)
    if r.status_code == 401:
        raise requests.HTTPError(
            "401 Unauthorized: Your token was rejected. "
            "Re-copy a fresh API token from the Carbon Mapper portal and try again."
        )
    r.raise_for_status()

    total = int(r.headers.get("pagination-count", "0"))
    if total == 0:
        open(out_csv, "w", encoding="utf-8").write("")
        return out_csv

    params["limit"] = 1000
    params["offset"] = 0
    first = True
    with open(out_csv, "w", encoding="utf-8") as f:
        got = 0
        while True:
            r = requests.get(base, params=params, headers=headers, timeout=180)
            if r.status_code == 401:
                raise requests.HTTPError(
                    "401 Unauthorized during paging: token invalid/expired or missing scope."
                )
            r.raise_for_status()
            text = r.content.decode("utf-8", errors="ignore")
            lines = text.splitlines()
            if first:
                f.write(text)
                n_rows = max(len(lines) - 1, 0)
                first = False
            else:
                body = "\n".join(lines[1:]) + ("\n" if text.endswith("\n") else "")
                f.write(body)
                n_rows = body.count("\n")
            got += n_rows
            params["offset"] += params["limit"]
            if params["offset"] >= total:
                break
    return out_csv


# ------------------------- Area estimation -------------------------
AREA_CRS = "EPSG:6933"

def _reproject_mask_to_equal_area(mask_uint8, src_transform, src_crs):
    dst_transform, w, h = calculate_default_transform(
        src_crs or "EPSG:4326", AREA_CRS,
        mask_uint8.shape[1], mask_uint8.shape[0],
        *rasterio.transform.array_bounds(mask_uint8.shape[0], mask_uint8.shape[1], src_transform)
    )
    dst = np.empty((h, w), dtype="uint8")
    reproject(
        source=mask_uint8, destination=dst,
        src_transform=src_transform, src_crs=src_crs or "EPSG:4326",
        dst_transform=dst_transform, dst_crs=AREA_CRS,
        resampling=Resampling.nearest
    )
    return dst, dst_transform

def _mask_area_km2(mask_uint8, transform):
    px_area_m2 = abs(transform.a * transform.e)
    return float(mask_uint8.sum() * px_area_m2 / 1e6)

def _area_from_con_tif(url, quantile=95):
    r = requests.get(url, timeout=180); r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
        tmp.write(r.content); tmp.flush()
        with rasterio.open(tmp.name) as src:
            band = src.read(1).astype("float32")
            d = band[np.isfinite(band)]
            if d.size == 0: return np.nan
            thr = float(np.percentile(d, quantile))
            mask = (np.isfinite(band) & (band > thr)).astype("uint8")
            ea, ea_transform = _reproject_mask_to_equal_area(mask, src.transform, src.crs)
            return _mask_area_km2(ea, ea_transform)

def _area_from_vis_rgba_tif(url, s_min=0.6, v_min=0.35, v_max=0.9, hue_gate=True):
    r = requests.get(url, timeout=180); r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
        tmp.write(r.content); tmp.flush()
        with rasterio.open(tmp.name) as src:
            if src.count < 3: return np.nan
            R = (src.read(1).astype("float32")/255.0)
            G = (src.read(2).astype("float32")/255.0)
            B = (src.read(3).astype("float32")/255.0)
            A = (src.read(4) if src.count>=4 else np.full(R.shape,255,np.uint8)).astype(np.uint8)
            valid = A > 0
            from matplotlib.colors import rgb_to_hsv
            hsv = rgb_to_hsv(np.stack([R,G,B], axis=-1))
            h, s, v = hsv[...,0], hsv[...,1], hsv[...,2]
            mask = valid & (s>=s_min) & (v>=v_min) & (v<=v_max)
            if hue_gate:
                mask &= ((h<=0.17) | (h>=0.83))
            try:
                from scipy.ndimage import binary_opening, binary_closing, label
                mask = binary_opening(mask, iterations=1)
                mask = binary_closing(mask, iterations=1)
                lbl, n = label(mask)
                if n>0:
                    sizes = np.bincount(lbl.ravel()); sizes[0]=0
                    mask = (lbl == sizes.argmax())
            except Exception:
                pass
            ea, ea_transform = _reproject_mask_to_equal_area(mask.astype("uint8"), src.transform, src.crs)
            return _mask_area_km2(ea, ea_transform)

def estimate_plume_area_km2(row):
    con = row.get("con_tif") if isinstance(row, dict) else (row["con_tif"] if "con_tif" in row else None)
    if isinstance(con, str) and con.lower().endswith(".tif"):
        try:
            a = _area_from_con_tif(con, quantile=95)
            if np.isfinite(a): return a
        except Exception:
            pass
    vis = row.get("plume_tif") if isinstance(row, dict) else (row["plume_tif"] if "plume_tif" in row else None)
    if isinstance(vis, str) and vis.lower().endswith(".tif"):
        try:
            return _area_from_vis_rgba_tif(vis)
        except Exception:
            return np.nan
    return np.nan

# ------------------------- Stats helpers -------------------------
def ensure_gas_norm(df: pd.DataFrame) -> pd.Series:
    if "gas" in df.columns:
        s = (df["gas"].astype(str).str.upper()
             .str.replace("₄","4").str.replace("₂","2"))
        s = s.replace({"METHANE":"CH4","CARBONDIOXIDE":"CO2"})
        return s.where(s.isin(["CH4","CO2"]))
    def _infer(row):
        for c in ["plume_tif","con_tif","rgb_tif","plume_png","rgb_png"]:
            if c in row and isinstance(row[c], str):
                t = row[c].upper()
                if "CH4" in t or "METHANE" in t: return "CH4"
                if "CO2" in t or "CARBON"  in t: return "CO2"
        return np.nan
    return df.apply(_infer, axis=1)

def state_stats_for_gas(gdf_joined: gpd.GeoDataFrame, gas: str,
                        selected_states: Optional[Iterable[str]] = None) -> pd.DataFrame:
    base = gdf_joined.copy()
    if selected_states:
        base["admin_name"] = base["admin_name"].astype(str).str.strip()
        base = base[base["admin_name"].isin(list(selected_states))]
    if "gas_norm" not in base.columns:
        base["gas_norm"] = ensure_gas_norm(base)
    sub = base[(base["gas_norm"]==gas) & base["admin_name"].notna()].copy()
    if sub.empty:
        return pd.DataFrame(columns=["rank","admin_name","plume_count"])
    counts = sub.groupby("admin_name", as_index=True).size().rename("plume_count")
    if "plume_area_km2" in sub.columns:
        area = sub.groupby("admin_name")["plume_area_km2"].agg(
            area_sum_km2="sum", area_mean_km2="mean", area_median_km2="median",
            area_min_km2="min", area_max_km2="max", area_std_km2="std")
    else:
        area = pd.DataFrame(index=counts.index)
    if "emission_auto" in sub.columns:
        emis = (sub.dropna(subset=["emission_auto"])
                  .groupby("admin_name")["emission_auto"]
                  .agg(emission_total="sum", emission_mean="mean",
                       emission_median="median", emission_count="count"))
    else:
        emis = pd.DataFrame(index=counts.index)
    out = (pd.concat([counts, area, emis], axis=1)
             .sort_values("plume_count", ascending=False)
             .reset_index())
    out.insert(0, "rank", np.arange(1, len(out)+1))
    for c in ["area_sum_km2","area_mean_km2","area_median_km2","area_min_km2","area_max_km2","area_std_km2",
              "emission_total","emission_mean","emission_median"]:
        if c in out.columns:
            out[c] = out[c].astype(float).round(4)
    return out

# ------------------------- App -------------------------
@dataclass
class CMAppState:
    cm_token: Optional[str] = None
    ee_project: Optional[str] = None
    country: str = "United States of America"
    gases: Tuple[str, ...] = ("CH4","CO2")
    date_start: date = date(2023,1,1)
    date_end: date = date.today()
    limit: int = 2000
    compute_areas: bool = False
    selected_states: Tuple[str, ...] = tuple()

class CMApp:
    def __init__(self, cm_token: Optional[str], ee_project: Optional[str] = None,
                 country_choices: Optional[list[str]] = None):
        # ee_project is accepted only for backward compatibility and is not used here.
        self.s = CMAppState(cm_token=cm_token, ee_project=ee_project)
        self.BASE_DIR = "./cm_app_outputs"
        os.makedirs(self.BASE_DIR, exist_ok=True)
        self.country_choices = country_choices or [
            "United States of America","Canada","Mexico","Brazil","Argentina",
            "United Kingdom","France","Germany","Italy","Spain",
            "India","China","Japan","Australia","South Africa"
        ]

        # Data holders
        self.gdf_admin0 = None
        self.gdf_admin1 = None
        self.gdf_joined = None
        self.df_plumes = None

        # Build UI
        self._build_widgets()

    # ---- UI ----
    def _build_widgets(self):
        self.country_dd   = w.Dropdown(options=self.country_choices, value=self.s.country, description="Country:")
        self.date_start   = w.DatePicker(value=self.s.date_start, description="Start:")
        self.date_end     = w.DatePicker(value=self.s.date_end, description="End:")
        self.gases_ms     = w.SelectMultiple(options=[("Methane (CH4)","CH4"),("Carbon dioxide (CO2)","CO2")],
                                             value=self.s.gases, description="Gases:")
        self.state_ms     = w.SelectMultiple(options=[], description="States/Prov:", rows=8, layout=w.Layout(width="50%"))
        self.limit_slider = w.IntSlider(value=self.s.limit, min=100, max=10000, step=100, description="Max plumes:")
        self.compute_chk  = w.Checkbox(value=self.s.compute_areas, description="Estimate plume areas")
        self.compute_btn  = w.Button(description="Estimate areas now", button_style="info", disabled=True)
        self.run_btn      = w.Button(description="Fetch & Analyze", button_style="success")
        self.status_out   = w.Output()

        # Raw CSV panel
        self.raw_pick = w.Dropdown(options=[], description="Raw CSV:")
        self.raw_refresh_btn = w.Button(description="Refresh list")
        self.raw_dl_btn = w.Button(description="Download", button_style="primary")
        self.raw_out = w.Output()
        self.raw_panel_box = w.VBox([
            w.HTML("<b>Raw CSV exports (Carbon Mapper)</b>"),
            w.HBox([self.raw_pick, self.raw_refresh_btn, self.raw_dl_btn]),
            self.raw_out
        ])

        # Chart/UI for analysis
        self.gas_toggle = w.ToggleButtons(options=[("CH4","CH4"),("CO2","CO2")], value="CH4", description="Gas:")
        self.view_dd    = w.Dropdown(options=[
            "Bar: plume_count by state",
            "Bar: area_mean_km2 by state",
            "Histogram: plume_area_km2 (per-plume)",
            "Bar: emission_total by state (if available)"
        ], value="Bar: plume_count by state", description="View:")
        self.topn       = w.IntSlider(value=10, min=5, max=30, step=1, description="Top N:")
        self.show_tbl   = w.Checkbox(value=True, description="Show table")
        self.charts_out = w.Output()

        # Map controls
        self.gas_map   = w.ToggleButtons(options=[("CH4","CH4"),("CO2","CO2")], value="CH4", description="Gas:")
        self.metric_dd = w.Dropdown(options=["plume_count"], value="plume_count", description="Metric:")
        self.show_points_chk = w.Checkbox(value=True, description="Show points")
        self.cluster_chk     = w.Checkbox(value=True, description="Cluster points")
        self.show_heat_chk   = w.Checkbox(value=False, description="Heatmap")
        self.max_pts_slider  = w.IntSlider(value=2000, min=200, max=10000, step=200, description="Max points")
        self.heat_rad_slider = w.IntSlider(value=18, min=5, max=40, step=1, description="Heat radius")
        self.pt_rad_slider   = w.IntSlider(value=5, min=2, max=12, step=1, description="Point radius")
        self.map_out         = w.Output()

        # Wire events
        self.country_dd.observe(self._refresh_states, names="value")
        self.run_btn.on_click(self._run_pipeline)
        self.compute_btn.on_click(self._on_compute_clicked)

        self.raw_refresh_btn.on_click(self._refresh_raw_list)
        self.raw_dl_btn.on_click(self._download_raw)

        self.gas_toggle.observe(self._update_charts, names="value")
        self.view_dd.observe(self._update_charts, names="value")
        self.topn.observe(self._update_charts, names="value")
        self.show_tbl.observe(self._update_charts, names="value")

        for wdg in [self.gas_map, self.metric_dd, self.show_points_chk, self.cluster_chk,
                    self.show_heat_chk, self.max_pts_slider, self.heat_rad_slider, self.pt_rad_slider]:
            wdg.observe(self._update_map, names="value")

        # Initial states list
        self._refresh_states()

    def display(self):
        # Top controls
        display(w.HBox([self.country_dd]))
        display(w.HBox([self.date_start, self.date_end]))
        display(self.gases_ms)
        display(self.state_ms)
        display(self.limit_slider)
        display(w.HBox([self.compute_chk, self.compute_btn]))
        display(self.run_btn, self.status_out)


        # Raw CSV panel
        display(self.raw_panel_box)
        self._refresh_raw_list()

        # Charts
        display(w.HBox([self.gas_toggle, self.view_dd, self.topn, self.show_tbl]))
        display(self.charts_out)

        # Map controls + map
        display(w.VBox([w.HBox([self.gas_map, self.metric_dd]),
                        w.HBox([self.show_points_chk, self.cluster_chk, self.show_heat_chk]),
                        w.HBox([self.max_pts_slider, self.heat_rad_slider, self.pt_rad_slider])]))
        display(self.map_out)
        # initial renders (empty)
        self._update_charts()
        self._refresh_metric_opts()
        self._update_map()

    # ---- internal helpers ----
    def _refresh_states(self, *_):
        with self.status_out:
            clear_output(wait=True)
            print("Loading ADM1 list…")
            try:
                gdf_states, _ = load_admin_boundaries(self.country_dd.value, admin_level=1)
                opts = sorted(gdf_states["admin_name"].astype(str).unique().tolist())
                self.state_ms.options = opts
                print(f"Loaded {len(opts)} ADM1 units. (Leave empty for ALL.)")
            except Exception as e:
                self.state_ms.options = []
                print("Could not load ADM1 list:", e)

    def _compute_and_attach_areas(self, df: pd.DataFrame):
        print("Estimating plume areas (auto)…")
        if "plume_area_km2" not in df.columns:
            df["plume_area_km2"] = np.nan
        for i, row in df.iterrows():
            try:
                df.at[row.name, "plume_area_km2"] = estimate_plume_area_km2(row)
            except Exception:
                df.at[row.name, "plume_area_km2"] = np.nan
        s = df["plume_area_km2"].dropna()
        if not s.empty:
            print(f"Areas computed: {s.count()} | median={s.median():.4f} km² | p95={s.quantile(0.95):.4f} | max={s.max():.4f}")
        else:
            print("No areas computed (all NaN).")

    def _run_pipeline(self, _):
        with self.status_out:
            clear_output(wait=True)
            if not self.s.cm_token:
                print("❗ Set your Carbon Mapper token (cm_token) when creating CMApp.")
                return

            print("Loading boundaries…")
            self.gdf_admin0, _ = load_admin_boundaries(
                self.country_dd.value, admin_level=0
            )
            self.gdf_admin1, _ = load_admin_boundaries(
                self.country_dd.value, admin_level=1
            )

            aoi = build_aoi_bbox(self.gdf_admin0, self.gdf_admin1, self.state_ms.value, pad_deg=0.0)
            start = self.date_start.value.strftime("%Y-%m-%d")
            end   = self.date_end.value.strftime("%Y-%m-%d")
            print(f"Querying Carbon Mapper: {self.country_dd.value}  {start} → {end}")

            try:
                csv_path = download_carbonmapper_plumes(self.s.cm_token, aoi, start, end, self.BASE_DIR)
            except requests.HTTPError as e:
                print("Download failed:", e)
                return

            print("CSV:", csv_path)
            try:
                df = pd.read_csv(csv_path)
            except pd.errors.EmptyDataError:
                print("No plumes for this selection.")
                self.df_plumes = None
                self.gdf_joined = None
                self._refresh_raw_list()
                return
            if df.empty:
                print("No plumes for this selection.")
                self.df_plumes = None
                self.gdf_joined = None
                self._refresh_raw_list()
                return

            # Normalize/derive gas labels first (CH4/CO2)
            df["gas"] = ensure_gas_norm(df)

            # Gas filter follows the multi-select exactly. If both CH4 and CO2 are selected,
            # both are loaded; the chart/map gas toggle controls what is displayed later.
            if "gas" in df.columns:
                selected = list(self.gases_ms.value) if hasattr(self, "gases_ms") else []
                if selected:
                    df = df[df["gas"].isin(selected)].copy()
            if df.empty:
                print("No plumes after gas filter.")
                self.df_plumes = None
                self.gdf_joined = None
                self._refresh_raw_list()
                return

            # Limit number of rows if requested
            if len(df) > self.limit_slider.value:
                df = df.head(self.limit_slider.value).copy()

            # Optional area computations
            if self.compute_chk.value:
                self._compute_and_attach_areas(df)

            df = df.copy()
            df["row_id"] = np.arange(len(df))

            # Required columns check
            req_cols = {"plume_latitude", "plume_longitude"}
            if not req_cols.issubset(df.columns):
                print("Downloaded CSV missing columns:", req_cols - set(df.columns))
                self.df_plumes = None
                self.gdf_joined = None
                self._refresh_raw_list()
                return

            # Build GeoDataFrame and spatial join with admin1
            gdf_plumes_local = gpd.GeoDataFrame(
                df,
                geometry=[Point(xy) for xy in zip(df["plume_longitude"], df["plume_latitude"])],
                crs=4326
            )
            if self.gdf_admin1.crs != gdf_plumes_local.crs:
                self.gdf_admin1 = self.gdf_admin1.to_crs(gdf_plumes_local.crs)

            gdfj = gpd.sjoin(
                gdf_plumes_local, self.gdf_admin1[["admin_name", "geometry"]],
                how="left", predicate="within"
            ).drop(columns=["index_right"])

            # Save results to app state
            self.df_plumes = df
            self.gdf_joined = gdfj
            # Refresh UI
            self._refresh_raw_list()
            self._refresh_metric_opts()
            self._update_charts()
            self._update_map()

            print(f"Loaded {len(df)} plumes (after filters).")

    # public hooks
    def compute_areas_now(self):
        if self.df_plumes is None or self.gdf_joined is None:
            with self.status_out:
                print("No data loaded. Click 'Fetch & Analyze' first.")
            return
        with self.status_out:
            clear_output(wait=True)
            self._compute_and_attach_areas(self.df_plumes)
            if "row_id" in self.df_plumes.columns and "row_id" in self.gdf_joined.columns:
                self.gdf_joined = self.gdf_joined.drop(
                    columns=[c for c in ["plume_area_km2"] if c in self.gdf_joined.columns],
                    errors="ignore"
                )
                self.gdf_joined = self.gdf_joined.merge(
                    self.df_plumes[["row_id","plume_area_km2"]], on="row_id", how="left"
                )
            print("Areas attached. You can update charts/map.")

    def export_state_stats(self, path: str = "state_stats.csv", gas: str = "CH4"):
        if self.gdf_joined is None:
            with self.status_out:
                print("Load data first.")
            return None
        sel = list(self.state_ms.value) if len(self.state_ms.value)>0 else None
        df = state_stats_for_gas(self.gdf_joined, gas, selected_states=sel)
        df.to_csv(path, index=False)
        with self.status_out:
            print(f"Saved: {path}")
        return path

    def save_map(self, path: str = "map.html", gas: str = "CH4", metric: str = "plume_count"):
        m = self._make_map(gas, metric)
        if m is None:
            with self.status_out:
                print("No map to save.")
            return None
        m.save(path)
        with self.status_out:
            print(f"Saved map: {path}")
        return path

    # ---- RAW CSV PANEL METHODS ----
    def _list_raw_files(self):
        files = glob.glob(os.path.join(self.BASE_DIR, "plumes_*.csv"))
        files.sort(key=os.path.getmtime, reverse=True)
        return files

    def _refresh_raw_list(self, *_):
        files = self._list_raw_files()
        self.raw_pick.options = files
        self.raw_pick.value = files[0] if files else None
        with self.raw_out:
            clear_output(wait=True)
            if files:
                print(f"Found {len(files)} raw CSV file(s).")
            else:
                print("No raw CSV yet. Click ‘Fetch & Analyze’ to create one.")

    def _download_raw(self, *_):
        with self.raw_out:
            clear_output(wait=True)
            if not self.raw_pick.value:
                print("No raw CSV selected.")
                return
            path = self.raw_pick.value
            print("Preparing download:", path)
            try:
                from google.colab import files as gfiles
                gfiles.download(path)
            except Exception:
                print("Saved at:", os.path.abspath(path))

    # ---- charts ----
    def _update_charts(self, *_):
        with self.charts_out:
            clear_output(wait=True)
            if self.gdf_joined is None:
                print("Run Fetch & Analyze first.")
                return

            sel = list(self.state_ms.value) if len(self.state_ms.value)>0 else None
            df = state_stats_for_gas(self.gdf_joined, self.gas_toggle.value, selected_states=sel)
            if df.empty:
                print(f"No data for {self.gas_toggle.value}.")
                return

            if self.view_dd.value.startswith("Bar: plume_count"):
                plt.figure()
                sub = df.head(self.topn.value)
                plt.bar(sub["admin_name"], sub["plume_count"])
                plt.xticks(rotation=60, ha="right")
                plt.title(f"{self.gas_toggle.value}: Top states by plume count")
                plt.tight_layout(); plt.show()

            elif self.view_dd.value.startswith("Bar: area_mean"):
                if "area_mean_km2" in df.columns:
                    plt.figure()
                    sub = df.head(self.topn.value)
                    plt.bar(sub["admin_name"], sub["area_mean_km2"])
                    plt.xticks(rotation=60, ha="right")
                    plt.title(f"{self.gas_toggle.value}: Top states by mean plume area")
                    plt.tight_layout(); plt.show()
                else:
                    print("No plume_area_km2 available.")

            elif self.view_dd.value.startswith("Histogram"):
                base = self.gdf_joined.copy()
                if "gas_norm" not in base.columns:
                    base["gas_norm"] = ensure_gas_norm(base)
                if sel:
                    base["admin_name"] = base["admin_name"].astype(str).str.strip()
                    base = base[base["admin_name"].isin(sel)]
                ser = base.loc[base["gas_norm"]==self.gas_toggle.value, "plume_area_km2"].dropna()
                if ser.empty:
                    print("No plume_area_km2 available for current selection.")
                    return
                plt.figure()
                ser.plot(kind="hist", bins=40)
                plt.title(f"{self.gas_toggle.value}: plume areas (km²)")
                plt.tight_layout(); plt.show()

            else:
                sub = self.gdf_joined.copy()
                if "gas_norm" not in sub.columns:
                    sub["gas_norm"] = ensure_gas_norm(sub)
                if sel:
                    sub["admin_name"] = sub["admin_name"].astype(str).str.strip()
                    sub = sub[sub["admin_name"].isin(sel)]
                sub = sub[(sub["gas_norm"]==self.gas_toggle.value) & sub["admin_name"].notna()]
                if "emission_auto" not in sub.columns or sub["emission_auto"].dropna().empty:
                    print("No emission_auto available.")
                    return
                df_tot = (sub.dropna(subset=["emission_auto"])
                            .groupby("admin_name", as_index=False)["emission_auto"].sum()
                            .rename(columns={"emission_auto":"emission_total"}))
                plt.figure()
                sub2 = df_tot.sort_values("emission_total", ascending=False).head(self.topn.value)
                plt.bar(sub2["admin_name"], sub2["emission_total"])
                plt.xticks(rotation=60, ha="right")
                plt.title(f"{self.gas_toggle.value}: total emissions by state")
                plt.tight_layout(); plt.show()

            if self.show_tbl.value:
                display(df.head(self.topn.value))

    # ---- map ----
    def _refresh_metric_opts(self, *_):
        if self.gdf_joined is None:
            self.metric_dd.options = ["plume_count"]
            self.metric_dd.value = "plume_count"
            return
        sel = list(self.state_ms.value) if len(self.state_ms.value)>0 else None
        cols = list(state_stats_for_gas(self.gdf_joined, self.gas_map.value, selected_states=sel).columns)
        order = ["plume_count","area_mean_km2","area_sum_km2","emission_total"]
        opts = [c for c in order if c in cols] or ["plume_count"]
        self.metric_dd.options = opts
        self.metric_dd.value = opts[0]

    def _filter_plumes_for_map(self, gas: str):
        if self.gdf_joined is None:
            return None
        df = self.gdf_joined.copy()
        if "gas_norm" not in df.columns:
            df["gas_norm"] = ensure_gas_norm(df)
        df = df[df["gas_norm"] == gas]
        if len(self.state_ms.value)>0:
            sel = [s.strip() for s in self.state_ms.value]
            df["admin_name"] = df["admin_name"].astype(str).str.strip()
            df = df[df["admin_name"].isin(sel)]
        df = df.dropna(subset=["plume_latitude","plume_longitude"])
        keep = [c for c in ["datetime","gas_norm","admin_name","plume_area_km2","emission_auto",
                            "ipcc_sector","instrument","platform","plume_latitude","plume_longitude"] if c in df.columns]
        return df[keep].copy()

    def _make_map(self, gas: str, metric: str):
        if self.gdf_joined is None:
            return None
        sel = list(self.state_ms.value) if len(self.state_ms.value)>0 else None
        stats = state_stats_for_gas(self.gdf_joined, gas, selected_states=sel)
        if stats.empty or metric not in stats.columns:
            return None
        base_admin = self.gdf_admin1.copy()
        if sel:
            base_admin["admin_name"] = base_admin["admin_name"].astype(str).str.strip()
            base_admin = base_admin[base_admin["admin_name"].isin(sel)]
        gdf_merge = base_admin.merge(stats[["admin_name", metric]], on="admin_name", how="left").fillna({metric:0})
        if gdf_merge.empty:
            return None
        minx, miny, maxx, maxy = gdf_merge.total_bounds
        m = folium.Map(location=[(miny+maxy)/2,(minx+maxx)/2], zoom_start=5 if sel else 4, tiles="cartodbpositron")
        vals = gdf_merge[metric].astype(float).values
        if np.nanmax(vals)==np.nanmin(vals):
            bins = list(np.linspace(np.nanmin(vals), np.nanmax(vals)+1e-9, 6))
        else:
            q = np.unique(np.nanpercentile(vals, [0,20,40,60,80,100])).astype(float)
            bins = list(q if len(q)>=6 else np.linspace(np.nanmin(vals), np.nanmax(vals), 6))
        folium.Choropleth(
            geo_data=gdf_merge.to_json(),
            data=gdf_merge,
            columns=["admin_name", metric],
            key_on="feature.properties.admin_name",
            bins=bins, fill_opacity=0.8, line_opacity=0.8,
            legend_name=f"{gas} — {metric}", nan_fill_opacity=0.15,
            name=f"{gas} — {metric}"
        ).add_to(m)
        folium.GeoJson(
            gdf_merge,
            name="labels",
            style_function=lambda x: {"color":"transparent","fillOpacity":0},
            tooltip=folium.features.GeoJsonTooltip(fields=["admin_name",metric],
                                                   aliases=["State/Prov", metric], localize=True)
        ).add_to(m)

        # plume overlays
        dfp = self._filter_plumes_for_map(gas)
        if dfp is not None and not dfp.empty:
            # heatmap (optional)
            if self.show_heat_chk.value:
                if "plume_area_km2" in dfp.columns and dfp["plume_area_km2"].notna().any():
                    a = dfp["plume_area_km2"].fillna(0).clip(lower=0)
                    scale = np.nanpercentile(a, 95) or (a.max() or 1.0)
                    wts = (a / scale).clip(0, 1).values
                else:
                    wts = np.ones(len(dfp))
                pts = dfp[["plume_latitude","plume_longitude"]].values.tolist()
                HeatMap([[lat, lon, float(w)] for (lat, lon), w in zip(pts, wts)],
                        radius=int(self.heat_rad_slider.value),
                        blur=int(self.heat_rad_slider.value*0.8),
                        name="Plume heatmap").add_to(m)
            # points
            if self.show_points_chk.value:
                gas_color = {"CH4": "#2c7fb8", "CO2": "#d7301f"}.get(gas, "#3a3a3a")
                layer = folium.FeatureGroup(name="Plume points", show=True)
                cluster = MarkerCluster().add_to(layer) if self.cluster_chk.value else layer
                dfpp = dfp.head(self.max_pts_slider.value).copy()
                for row in dfpp.itertuples(index=False):
                    lat = float(getattr(row, "plume_latitude"))
                    lon = float(getattr(row, "plume_longitude"))
                    area = getattr(row, "plume_area_km2", None)
                    emis = getattr(row, "emission_auto", None)
                    dt   = getattr(row, "datetime", "")
                    adm  = getattr(row, "admin_name", "")
                    sec  = getattr(row, "ipcc_sector", "") if "ipcc_sector" in dfpp.columns else ""
                    inst = getattr(row, "instrument", "") if "instrument" in dfpp.columns else ""
                    plat = getattr(row, "platform", "") if "platform" in dfpp.columns else ""
                    area_txt = "" if area is None or (isinstance(area, float) and np.isnan(area)) else round(float(area), 4)
                    emis_txt = "" if emis is None or (isinstance(emis, float) and np.isnan(emis)) else emis
                    html = (
                        f"<b>{gas}</b> plume<br>"
                        f"<b>Date:</b> {dt}<br>"
                        f"<b>State/Prov:</b> {adm}<br>"
                        f"<b>Area (km²):</b> {area_txt}<br>"
                        f"<b>Emission:</b> {emis_txt}<br>"
                        f"<b>Sector:</b> {sec}<br>"
                        f"<b>Instrument/Platform:</b> {inst} {plat}"
                    )
                    marker = folium.CircleMarker(
                        location=[lat, lon],
                        radius=int(self.pt_rad_slider.value),
                        color=gas_color, fill=True, fill_opacity=0.7, weight=1,
                        popup=folium.Popup(html, max_width=300),
                    )
                    (cluster if self.cluster_chk.value else layer).add_child(marker)
                layer.add_to(m)

        folium.LayerControl().add_to(m)

        return m

    def _update_map(self, *_):
        with self.map_out:
            clear_output(wait=True)
            m = self._make_map(self.gas_map.value, self.metric_dd.value)
            if m is None:
                print("No data for current selection.")
            else:
                display(m)

    # ---- external button handlers ----
    def _on_compute_clicked(self, _):
        self.compute_areas_now()

    def _selected_states(self) -> list[str]:
        try:
            return [str(s) for s in (self.state_ms.value or []) if str(s).strip()]
        except Exception:
            return []

    def _ensure_gas_norm(self, df):
        if "gas_norm" in df.columns:
            return df["gas_norm"]
        ser = df.get("gas", pd.Series(index=df.index, dtype=object)).astype(str).str.upper()
        return ser.replace({"METHANE":"CH4","CH₄":"CH4","CH4":"CH4","CO2":"CO2","CARBON DIOXIDE":"CO2"})

    def joined_for_selection(self, gas: str | None = None) -> pd.DataFrame:
        """Return plumes joined to ADM1, limited to selected states (if any), and optionally to a gas."""
        if self.gdf_joined is None or self.gdf_joined.empty:
            return pd.DataFrame()
        dj = self.gdf_joined.copy()
        if "admin_name" in dj.columns:
            dj = dj[dj["admin_name"].notna()]
        sel = self._selected_states()
        if sel:
            dj = dj[dj["admin_name"].isin(sel)]
        if gas is not None:
            dj["gas_norm"] = self._ensure_gas_norm(dj)
            dj = dj[dj["gas_norm"] == gas]
        return dj

    def state_stats_for_gas_strict(self, gas: str) -> pd.DataFrame:
        dj = self.joined_for_selection(gas)
        if dj.empty:
            return pd.DataFrame(columns=["admin_name","plume_count"])
        agg = dj.groupby("admin_name").size().rename("plume_count").to_frame()

        if "plume_area_km2" in dj.columns:
            area = dj.groupby("admin_name")["plume_area_km2"].agg(
                area_sum_km2="sum", area_mean_km2="mean", area_median_km2="median"
            )
            agg = agg.join(area, how="left")

        if "emission_auto" in dj.columns:
            emis = dj.groupby("admin_name")["emission_auto"].agg(
                emission_total="sum", emission_mean="mean"
            )
            agg = agg.join(emis, how="left")

        return (agg.reset_index()
                   .sort_values("plume_count", ascending=False))

    def make_choropleth_selected(self, gas: str, metric: str):
        import folium, numpy as np
        stats = self.state_stats_for_gas_strict(gas)
        if stats.empty or metric not in stats.columns:
            return None

        base = self.gdf_admin1.copy()
        sel = self._selected_states()
        if sel:
            base = base[base["admin_name"].isin(sel)]
            if base.empty:
                return None

        gm = base.merge(stats[["admin_name", metric]], on="admin_name", how="left").fillna({metric:0.0})

        minx, miny, maxx, maxy = gm.total_bounds
        m = folium.Map(location=[(miny+maxy)/2,(minx+maxx)/2], zoom_start=5, tiles="cartodbpositron")

        vals = gm[metric].astype(float).values
        if np.nanmax(vals) == np.nanmin(vals):
            bins = list(np.linspace(np.nanmin(vals), np.nanmax(vals)+1e-9, 6))
        else:
            q = np.unique(np.nanpercentile(vals, [0,20,40,60,80,100])).astype(float)
            bins = list(q if len(q)>=6 else np.linspace(np.nanmin(vals), np.nanmax(vals), 6))

        folium.Choropleth(
            geo_data=gm.to_json(),
            data=gm,
            columns=["admin_name", metric],
            key_on="feature.properties.admin_name",
            bins=bins, fill_opacity=0.8, line_opacity=0.8,
            legend_name=f"{gas} — {metric}", nan_fill_opacity=0.15,
            name=f"{gas} — {metric}"
        ).add_to(m)

        folium.GeoJson(
            gm,
            name="labels",
            style_function=lambda x: {"color":"transparent","fillOpacity":0},
            tooltip=folium.features.GeoJsonTooltip(
                fields=["admin_name", metric],
                aliases=["State/Prov", metric],
                localize=True
            )
        ).add_to(m)

        self._add_plume_points_layer(m, gas=gas, cluster=True, show=True)
        folium.LayerControl().add_to(m)
        return m

    def _add_plume_points_layer(self, m, gas=None, cluster=True, show=True):
        import folium
        dj = self.joined_for_selection(gas)
        if dj.empty:
            return m
        dj = dj.head(5000).copy()  # cap for performance

        try:
            from folium.plugins import MarkerCluster
            fg = folium.FeatureGroup(name=f"Plumes {gas or 'ALL'} (selected)", show=show)
            group = MarkerCluster().add_to(fg) if cluster else fg
        except Exception:
            fg = folium.FeatureGroup(name=f"Plumes {gas or 'ALL'} (selected)", show=show)
            group = fg

        for _, r in dj.iterrows():
            popup = folium.Popup(f"{r.get('admin_name','?')} • {r.get('gas','?')} • {r.get('acquired','')}",
                                 max_width=300)
            folium.CircleMarker(
                location=[r["plume_latitude"], r["plume_longitude"]],
                radius=3, weight=0.5, fill=True, fill_opacity=0.8,
                color="#2c7fb8" if (str(gas).upper() == "CH4") else "#cb181d"
            ).add_child(popup).add_to(group)

        fg.add_to(m)
        return m

