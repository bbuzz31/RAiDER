import os
import pytest
import subprocess
import shutil
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import xarray as xr

test_dir = Path(__file__).parents[0]

TEST_DIR = test_dir.absolute()
DATA_DIR = os.path.join(TEST_DIR, 'data')
GEOM_DIR = os.path.join(TEST_DIR, 'test_geom')
WM_DIR   = os.path.join(TEST_DIR, 'weather_files')
ORB_DIR  = os.path.join(TEST_DIR, 'orbit_files')

WM = 'GMAO'

@contextmanager
def pushd(dir):
    """
    Change the current working directory within a context.
    """
    prevdir = os.getcwd()
    os.chdir(dir)
    yield
    os.chdir(prevdir)


def update_yaml(dct_cfg:dict, dst:str='temp.yaml'):
    """ Write a new yaml file from a dictionary.

    Updates parameters in the default 'raider.yaml' file.
    Each key:value pair will in 'dct_cfg' will overwrite that in the default
    """
    import RAiDER, yaml

    template_file = os.path.join(
                    os.path.dirname(RAiDER.__file__), 'cli', 'raider.yaml')

    with open(template_file, 'r') as f:
        try:
            params = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            print(exc)
            raise ValueError(f'Something is wrong with the yaml file {template_file}')

    params = {**params, **dct_cfg}

    with open(dst, 'w') as fh:
        yaml.safe_dump(params, fh,  default_flow_style=False)

    return dst


def makeLatLonGrid(bbox, reg, out_dir, spacing=0.1):
    """ Make lat lons at a specified spacing """
    S, N, W, E = bbox
    lat_st, lat_en = S, N
    lon_st, lon_en = W, E

    lats = np.arange(lat_st, lat_en, spacing)
    lons = np.arange(lon_st, lon_en, spacing)
    Lat, Lon = np.meshgrid(lats, lons)
    da_lat = xr.DataArray(Lat.T, name='data', coords={'lon': lons, 'lat': lats}, dims='lat lon'.split())
    da_lon = xr.DataArray(Lon.T, name='data', coords={'lon': lons, 'lat': lats}, dims='lat lon'.split())

    dst_lat = os.path.join(out_dir, f'lat_{reg}.nc')
    dst_lon = os.path.join(out_dir, f'lon_{reg}.nc')
    da_lat.to_netcdf(dst_lat)
    da_lon.to_netcdf(dst_lon)

    return dst_lat, dst_lon
