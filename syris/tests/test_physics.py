import numpy as np
import pyopencl as cl
import quantities as q
import syris
from syris import physics, config as cfg
from syris.tests.base import SyrisTest


class TestPhysics(SyrisTest):

    def setUp(self):
        syris.init()
        self.energy = 20 * q.keV
        self.lam = 6.19920937165e-11 * q.m
        self.size = 64
        self.mem = cl.Buffer(cfg.OPENCL.ctx, cl.mem_flags.READ_WRITE,
                             size=self.size ** 2 * cfg.PRECISION.cl_cplx)
        self.res = np.empty((self.size, self.size), dtype=cfg.PRECISION.np_cplx)
        self.distance = 1 * q.m
        self.pixel_size = 1 * q.um

    def test_energy_to_wavelength(self):
        self.assertAlmostEqual(physics.energy_to_wavelength(self.energy).
                               rescale(self.lam.units), self.lam)

    def test_wavelength_to_energy(self):
        self.assertAlmostEqual(physics.wavelength_to_energy(self.lam).
                               rescale(self.energy.units), self.energy,
                               places=5)

    def test_attenuation(self):
        ref_index = 1e-7 + 1e-10j
        energy = 20 * q.keV
        lam = physics.energy_to_wavelength(energy)
        self.assertAlmostEqual(physics.
                               ref_index_to_attenuation_coeff(
                                   ref_index, energy),
                               physics.ref_index_to_attenuation_coeff(
                               ref_index, lam))

    def _get_propagator(self, apply_phase_factor=False):
        return physics.get_propagator(self.size, self.distance,
                                      self.lam, self.pixel_size,
                                      apply_phase_factor,
                                      copy_to_host=True)

    def _cpu_propagator(self, phase_factor=1):
        j, i = np.mgrid[-0.5:0.5:1.0 / self.size, -0.5:0.5:1.0 / self.size].\
            astype(cfg.PRECISION.np_float)

        return cfg.PRECISION.np_cplx(phase_factor) * \
            np.fft.fftshift(np.exp(- np.pi * self.lam.simplified *
                                   self.distance.simplified *
                                   (i ** 2 + j ** 2) /
                                   self.pixel_size.simplified ** 2 * 1j))

    def test_no_phase_factor(self):
        self.res = self._get_propagator()
        cpu = self._cpu_propagator()

        np.testing.assert_almost_equal(self.res, cpu, 5)

    def test_with_phase_factor(self):
        phase = np.exp(2 * np.pi / self.lam.simplified *
                       self.distance.simplified * 1j)

        self.res = self._get_propagator(True)
        cpu = self._cpu_propagator(phase)

        np.testing.assert_almost_equal(self.res, cpu, 5)
