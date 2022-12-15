import os
import argparse
from importlib.metadata import entry_points
import shutil
import glob
import yaml
from textwrap import dedent
from RAiDER.cli import AttributeDict, DEFAULT_DICT
from RAiDER.cli.parser import add_cpus, add_out, add_verbose
from RAiDER.cli.validators import DateListAction, date_type
from RAiDER.logger import logger

## make it print the help correctly (using -h, --help, for correct process or for no args)


## --------------------------------------------------------------------delay.py
def read_template_file(fname):
    """
    Read the template file into a dictionary structure.
    Parameters: fname      - str, full path to the template file
                delimiter  - str, string to separate the key and value
                skip_chars - list of str, skip certain charaters in values
    Returns:    template   - dict, file content
    Examples:   template = read_template('raider.yaml')

    Modified from MintPy's 'read_template'
    """
    from RAiDER.cli.validators import (enforce_time, enforce_bbox, parse_dates,
                            get_query_region, get_heights, get_los, enforce_wm)
    with open(fname, 'r') as f:
        try:
            params = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            print(exc)
            raise ValueError('Something is wrong with the yaml file {}'.format(fname))

    # Drop any values not specified
    params = drop_nans(params)

    # Need to ensure that all the groups exist, even if they are not specified by the user
    group_keys = ['date_group', 'time_group', 'aoi_group', 'height_group', 'los_group', 'runtime_group']
    for key in group_keys:
        if not key in params.keys():
            params[key] = {}

    # Parse the user-provided arguments
    template = DEFAULT_DICT
    for key, value in params.items():
        if key == 'runtime_group':
            for k, v in value.items():
                if v is not None:
                    template[k] = v
        if key == 'weather_model':
            template[key]= enforce_wm(value)
        if key == 'time_group':
            template.update(enforce_time(AttributeDict(value)))
        if key == 'date_group':
            template['date_list'] = parse_dates(AttributeDict(value))
        if key == 'aoi_group':
            ## in case a DEM is passed and should be used
            dct_temp = {**AttributeDict(value),
                        **AttributeDict(params['height_group'])}
            template['aoi'] = get_query_region(AttributeDict(dct_temp))

        if key == 'los_group':
            template['los'] = get_los(AttributeDict(value))

        if key == 'look_dir':
            if value.lower() not in ['right', 'left']:
                raise ValueError(f"Unknown look direction {value}")
            template['look_dir'] = value.lower()

        if key == 'cube_spacing_in_m':
            template[key] = value

    # Have to guarantee that certain variables exist prior to looking at heights
    for key, value in params.items():
        if key == 'height_group':
            template.update(
                get_heights(
                    AttributeDict(value),
                    template['output_directory'],
                    template['station_file'],
                    template['bounding_box'],
                )
            )
    return AttributeDict(template)


def drop_nans(d):
    for key in list(d.keys()):
        if d[key] is None:
            del d[key]
        elif isinstance(d[key], dict):
            for k in list(d[key].keys()):
                if d[key][k] is None:
                    del d[key][k]
    return d


def calcDelays(iargs=None):
    """ Parse command line arguments using argparse. """
    import RAiDER
    from RAiDER.delay import tropo_delay
    from RAiDER.checkArgs import checkArgs
    from RAiDER.processWM import prepareWeatherModel
    from RAiDER.utilFcns import writeDelays
    examples = 'Examples of use:' \
        '\n\t raider.py customTemplatefile.cfg' \
        '\n\t raider.py -g'

    p = argparse.ArgumentParser(
        description =
            'Command line interface for RAiDER processing with a configure file.'
            'Default options can be found by running: raider.py --generate_config',
        epilog=examples, formatter_class=argparse.RawDescriptionHelpFormatter)

    p.add_argument(
        'customTemplateFile', nargs='?',
        help='custom template with option settings.\n' +
        "ignored if the default smallbaselineApp.cfg is input."
    )

    p.add_argument(
        '-g', '--generate_template', action='store_true',
        help='generate default template (if it does not exist) and exit.'
    )

    p.add_argument(
        '--download_only', action='store_true',
        help='only download a weather model.'
    )

    ## if not None, will replace first argument (customTemplateFile)
    args = p.parse_args(args=iargs)

    # default input file
    template_file = os.path.join(os.path.dirname(RAiDER.__file__),
                                                        'cli', 'raider.yaml')

    if args.generate_template:
        dst = os.path.join(os.getcwd(), 'raider.yaml')
        shutil.copyfile(template_file, dst)
        logger.info('Wrote %s', dst)
        os.sys.exit()


    # check: existence of input template files
    if (not args.customTemplateFile
            and not os.path.isfile(os.path.basename(template_file))
            and not args.generate_template):
        msg = "No template file found! It requires that either:"
        msg += "\n  a custom template file, OR the default template "
        msg += "\n  file 'raider.yaml' exists in current directory."

        p.print_usage()
        print(examples)
        raise SystemExit(f'ERROR: {msg}')

    if  args.customTemplateFile:
        # check the existence
        if not os.path.isfile(args.customTemplateFile):
            raise FileNotFoundError(args.customTemplateFile)

        args.customTemplateFile = os.path.abspath(args.customTemplateFile)
    else:
        args.customTemplateFile = template_file

    # Read the template file
    params = read_template_file(args.customTemplateFile)

    # Argument checking
    params  = checkArgs(params)
    dl_only = True if params['download_only'] or args.download_only else False

    if not params.verbose:
        logger.setLevel(logging.INFO)

    delay_dct = {}
    for t, w, f in zip(
        params['date_list'],
        params['wetFilenames'],
        params['hydroFilenames']
    ):

        los = params['los']
        aoi = params['aoi']
        model = params['weather_model']

        if los.ray_trace():
            ll_bounds = aoi.add_buffer(buffer=1) # add a buffer for raytracing
        else:
            ll_bounds = aoi.bounds()

        ###########################################################
        # weather model calculation
        logger.debug('Starting to run the weather model calculation')
        logger.debug('Date: {}'.format(t.strftime('%Y%m%d')))
        logger.debug('Beginning weather model pre-processing')
        try:
            weather_model_file = prepareWeatherModel(
                model, t,
                ll_bounds=ll_bounds, # SNWE
                wmLoc=params['weather_model_directory'],
                zref=params['zref'],
                download_only=dl_only,
                makePlots=params['verbose'],
            )
        except RuntimeError:
            logger.exception("Date %s failed", t)
            continue

        # dont process the delays for download only
        if dl_only:
            continue

        # Now process the delays
        try:
            wet_delay, hydro_delay = tropo_delay(
                t, weather_model_file, aoi, los,
                params['height_levels'],
                params['output_projection'],
                params['look_dir'],
                params['cube_spacing_in_m']
            )
        except RuntimeError:
            logger.exception("Date %s failed", t)
            continue

        ###########################################################
        # Write the delays to file
        # Different options depending on the inputs

        if los.is_Projected():
            out_filename = w.replace("_ztd", "_std")
            f = f.replace("_ztd", "_std")
        elif los.ray_trace():
            out_filename = w.replace("_std", "_ray")
            f = f.replace("_std", "_ray")
        else:
            out_filename = w

        if hydro_delay is None:
            # means that a dataset cube was returned
            ds = wet_delay
            ext = os.path.splitext(out_filename)[1]
            if ext not in ['.nc', '.h5']:
                out_filename = f'{os.path.splitext(out_filename)[0]}.nc'

            out_filename = out_filename.replace("wet", "tropo")

            if out_filename.endswith(".nc"):
                ds.to_netcdf(out_filename, mode="w")
            elif out_filename.endswith(".h5"):
                ds.to_netcdf(out_filename, engine="h5netcdf", invalid_netcdf=True)
            logger.info('Wrote delays to: %s', out_filename)

        else:
            if aoi.type() == 'station_file':
                out_filename = f'{os.path.splitext(out_filename)[0]}.csv'

            if aoi.type() in ['station_file', 'radar_rasters', 'geocoded_file']:
                fmt = params['raster_format'] if not params['raster_format'] == 'nc' else 'GTiff'
                writeDelays(aoi, wet_delay, hydro_delay, out_filename, f, outformat=fmt)

            logger.info('Wrote wet delays to: %s', out_filename)
            logger.info('Wrote hydro delays to: %s', f)


        # delay_dct[t] = wet_delay, hydro_delay
        delay_dct[t] = out_filename, f

    return delay_dct


## ------------------------------------------------------ downloadGNSSDelays.py
def downloadGNSS():
    """Parse command line arguments using argparse."""
    from RAiDER.gnss.downloadGNSSDelays import main as dlGNSS
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=""" \
    Check for and download tropospheric zenith delays for a set of GNSS stations from UNR

    Example call to virtually access and append zenith delay information to a CSV table in specified output
    directory, across specified range of time (in YYMMDD YYMMDD) and all available times of day, and confined to specified
    geographic bounding box :
    downloadGNSSdelay.py --out products -y 20100101 20141231 -b '39 40 -79 -78'

    Example call to virtually access and append zenith delay information to a CSV table in specified output
    directory, across specified range of time (in YYMMDD YYMMDD) and specified time of day, and distributed globally :
    downloadGNSSdelay.py --out products -y 20100101 20141231 --returntime '00:00:00'


    Example call to virtually access and append zenith delay information to a CSV table in specified output
    directory, across specified range of time in 12 day steps (in YYMMDD YYMMDD days) and specified time of day, and distributed globally :
    downloadGNSSdelay.py --out products -y 20100101 20141231 12 --returntime '00:00:00'

    Example call to virtually access and append zenith delay information to a CSV table in specified output
    directory, across specified range of time (in YYMMDD YYMMDD) and specified time of day, and distributed globally but restricted
    to list of stations specified in input textfile :
    downloadGNSSdelay.py --out products -y 20100101 20141231 --returntime '00:00:00' -f station_list.txt

    NOTE, following example call to physically download zenith delay information not recommended as it is not
    necessary for most applications.
    Example call to physically download and append zenith delay information to a CSV table in specified output
    directory, across specified range of time (in YYMMDD YYMMDD) and specified time of day, and confined to specified
    geographic bounding box :
    downloadGNSSdelay.py --download --out products -y 20100101 20141231 --returntime '00:00:00' -b '39 40 -79 -78'
    """)

    # Stations to check/download
    area = p.add_argument_group(
        'Stations to check/download. Can be a lat/lon bounding box or file, or will run the whole world if not specified')
    area.add_argument(
        '--station_file', '-f', default=None, dest='station_file',
        help=('Text file containing a list of 4-char station IDs separated by newlines'))
    area.add_argument(
        '-b', '--bounding_box', dest='bounding_box', type=str, default=None,
        help="Provide either valid shapefile or Lat/Lon Bounding SNWE. -- Example : '19 20 -99.5 -98.5'")
    area.add_argument(
        '--gpsrepo', '-gr', default='UNR', dest='gps_repo',
        help=('Specify GPS repository you wish to query. Currently supported archives: UNR.'))

    misc = p.add_argument_group("Run parameters")
    add_out(misc)

    misc.add_argument(
        '--date', dest='dateList',
        help=dedent("""\
            Date to calculate delay.
            Can be a single date, a list of two dates (earlier, later) with 1-day interval, or a list of two dates and interval in days (earlier, later, interval).
            Example accepted formats:
               YYYYMMDD or
               YYYYMMDD YYYYMMDD
               YYYYMMDD YYYYMMDD N
            """),
        nargs="+",
        action=DateListAction,
        type=date_type,
        required=True
    )

    misc.add_argument(
        '--returntime', dest='returnTime',
        help="Return delays closest to this specified time. If not specified, the GPS delays for all times will be returned. Input in 'HH:MM:SS', e.g. '16:00:00'",
        default=None)

    misc.add_argument(
        '--download',
        help='Physically download data. Note this option is not necessary to proceed with statistical analyses, as data can be handled virtually in the program.',
        action='store_true', dest='download', default=False)

    add_cpus(misc)
    add_verbose(misc)

    args =  p.parse_args()

    dlGNSS(args)
    return


## ------------------------------------------------------------ prepFromGUNW.py
# make clear which group is getting overwritten
def calcDelaysGUNW(iargs=None):
    from RAiDER.aria.prepFromGUNW import main as GUNW_prep
    from RAiDER.aria.calcGUNW import tropo_gunw_inf as GUNW_calc

    p = argparse.ArgumentParser(
        description='Calculate a cube of interferometic delays for GUNW files')

    p.add_argument(
        'file', type=str,
        help='1 ARIA GUNW netcdf file'
        )

    p.add_argument(
        '-m', '--model', default='HRRR', type=str,
        help='Weather model (Default=HRRR).'
        )

    p.add_argument(
        '-o', '--output_directory', default=os.getcwd(), type=str,
        help='Directory to store results (Default=./).'
        )

    p.add_argument(
        '-w', '--write', default=True,
        help='Optionally write the delays into the given GUNW product (Default=True).'
        )


    args       = p.parse_args(args=iargs)
    args.argv  = iargs if iargs else os.sys.argv[1:]
    # args.files = glob.glob(args.files) # eventually support multiple files

    ## below are placeholders and not yet implemented
    ## prep the config needed for delay calcs
    path_cfg, wavelength   = GUNW_prep(args)

    ## write the delays to disk using config and return dictionary of:
        # date: wet/hydro filename
    dct_delays = calcDelays([path_cfg])

    ## calculate the interferometric phase and write it out
    GUNW_calc(dct_delays, args.file, wavelength, args.output_directory, args.write)

    return


def main():
    parser = argparse.ArgumentParser(prefix_chars='+',
                        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '++process', choices=['calcDelays', 'downloadGNSS', 'calcDelaysGUNW'],
                     default='calcDelays',
                     help='Select the entrypoint to use'
    )
    args, unknowns = parser.parse_known_args()
    os.sys.argv = [args.process, *unknowns]

    process_entry_point = entry_points(group='console_scripts',
                                       name=f'{args.process}.py')[0]

    process_entry_point.load()()
    # ret_code = os.sys.exit(process_entry_point.load()()) # only if fn returns nothing


if __name__ == '__main__':
    main()
