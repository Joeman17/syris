import argparse
import logging
import os
import time
import numpy as np
import quantities as q
import syris
from functools import partial
from multiprocessing import Lock, Pool
from concert.storage import write_libtiff
from syris.bodies.mesh import Mesh, read_blender_obj
from syris.geometry import Trajectory, X_AX, Y_AX, Z_AX


LOCK = Lock()
LOG = logging.getLogger(__name__)


def make_projection(shape, ps, axis, mesh, center, lamino_angle, tomo_angle):
    mesh.clear_transformation()
    mesh.translate(center)
    mesh.rotate(lamino_angle, X_AX)
    mesh.rotate(tomo_angle, axis)

    return mesh.project(shape, ps).get()


def scan(shape, ps, axis, mesh, angles, prefix, lamino_angle=45 * q.deg, index=0, num_devices=1,
         shift_coeff=1e4):
    """Make a scan of tomographic angles. *shift_coeff* is the coefficient multiplied by pixel size
    which shifts the triangles to get rid of faulty pixels.
    """
    psm = ps.simplified.magnitude
    log_fmt = '{}: {:>04}/{:>04} in {:6.2f} s, angle: {:>6.2f} deg, maxima: {}'

    # Move to the middle of the FOV
    point = (shape[1] * psm / 2, shape[0] * psm / 2, 0) * q.m

    # Compute this device portion of tomographic angles
    enumerated = list(enumerate(angles))
    num_angles = len(enumerated)
    per_device = num_angles / num_devices
    stop = None if index == num_devices - 1 else (index + 1) * per_device
    mine = enumerated[index * per_device:stop]

    last = None
    checked_indices = []
    for i, angle in mine:
        st = time.time()
        projs = [make_projection(shape, ps, axis, mesh, point, lamino_angle, angle)]
        max_vals = [projs[-1].max()]
        best = 0
        if last is not None and max_vals[0] > 2 * last or np.isnan(max_vals[0]):
            # Check for faulty pixels
            checked_indices.append(i)
            for shift in [-psm / shift_coeff, psm / shift_coeff]:
                shifted_point = point + (shift, 0, 0) * q.m
                projs.append(make_projection(shape, ps, axis, mesh, shifted_point,
                                             lamino_angle, angle))
                max_vals.append(projs[-1].max())
            best = np.argmin(max_vals)
        duration = time.time() - st
        with LOCK:
            LOG.info(log_fmt.format(index, i + 1, num_angles, duration,
                                    float(angle.magnitude), max_vals))
        write_libtiff(prefix.format(i), projs[best])
        last = max_vals[best]

    with LOCK:
        LOG.info('Checked indices: {}'.format(checked_indices))
        LOG.info('Which map to files: {}'.format([prefix.format(i) for i in checked_indices]))

    return projs[best]


def process(args, device_index):
    syris.init(device_index=device_index, logfile=args.logfile)
    path, ext = os.path.splitext(args.input)
    if ext == '.obj':
        tri = read_blender_obj(args.input)
    else:
        tri = np.load(args.input)
    tri = tri * q.um

    tr = Trajectory([(0, 0, 0)] * q.um)
    mesh = Mesh(tri, tr, center='bbox', iterations=2)

    fov = max([ends[1] - ends[0] for ends in mesh.extrema[1:]]) * 1.1
    n = int(np.ceil((fov / args.pixel_size).simplified.magnitude))
    shape = (n, n)
    # 360 degrees -> twice the number of tomographic projections
    num_projs = int(np.pi * n) if args.num_projections is None else args.num_projections
    angles = np.linspace(0, 360, num_projs, endpoint=False) * q.deg
    if device_index == 0:
        LOG.info('n: {}, ps: {}, FOV: {}'.format(n, args.pixel_size, fov))
        LOG.info('Number of projections: {}'.format(num_projs))
        LOG.info('--- Mesh info ---')
        log_attributes(mesh)
        LOG.info('--- Args info ---')
        log_attributes(args)

    return scan(shape, args.pixel_size, args.rotation_axis, mesh, angles, args.prefix,
                lamino_angle=args.lamino_angle, index=device_index, num_devices=args.num_devices)


def parse_args():
    parser = argparse.ArgumentParser(description='Mesh example')
    parser.add_argument('input', type=str, help='Blender .obj input file name')
    parser.add_argument('--dset', type=str,
                        help='Data set name, if not specified guessed from input')
    parser.add_argument('--num-projections', type=int, help='Number of projections')
    parser.add_argument('--out-directory', type=str, default='/mnt/LSDF/users/farago/share/cr7',
                        help="Output directory, result goes to 'out-directory/dset/projections'")
    parser.add_argument('--pixel-size', type=float, default=750., help='Pixel size in nm')
    parser.add_argument('--lamino-angle', type=float, default=5,
                        help='Laminographic angle in degrees')
    parser.add_argument('--rotation-axis', type=str, choices=['y', 'z'], default='y',
                        help='Rotation axis (y - up, z - beam direction)')
    parser.add_argument('--num-devices', type=int, default=1,
                        help='Number of compute devices to use')

    return parser.parse_args()


def main():
    args = parse_args()

    # Prepare output
    if args.dset is None:
        dset = os.path.splitext(os.path.basename(args.input))[0]
    else:
        dset = args.dset
    dset += '_lamino_angle_{:>02}_deg'.format(int(args.lamino_angle))
    dset += '_axis_{}'.format(args.rotation_axis)
    dset += '_ps_{:>04}_nm'.format(int(args.pixel_size))

    args.prefix = os.path.join(args.out_directory, dset, 'projections', 'projection_{:>04}.tif')
    args.logfile = os.path.join(args.out_directory, dset, 'simulation.log')
    directory = os.path.dirname(args.prefix)
    if not os.path.exists(directory):
        os.makedirs(directory, mode=0o755)
    args.pixel_size = args.pixel_size * q.nm
    args.lamino_angle = args.lamino_angle * q.deg
    args.rotation_axis = Y_AX if args.rotation_axis == 'y' else Z_AX

    if args.num_devices == 1:
        # Easier exception message handling for debugging
        proj = process(args, 0)
        from pltpreview import show
        show(proj, block=True)
    else:
        exec_func = partial(process, args)
        devices = range(args.num_devices)
        pool = Pool(processes=args.num_devices)
        pool.map(exec_func, devices)


def log_attributes(obj):
    """Log object *obj* attributes."""
    for attr in dir(obj):
        if not attr.startswith('_') and not callable(getattr(obj, attr)):
            LOG.info('{}: {}'.format(attr, getattr(obj, attr)))


if __name__ == '__main__':
    main()