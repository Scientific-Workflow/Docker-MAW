#!/bin/bash
set -e

# Build LAMMPS from source without MPI
cd /tmp
wget -q https://github.com/lammps/lammps/archive/refs/tags/stable_2Aug2023_update3.tar.gz
tar xzf stable_2Aug2023_update3.tar.gz
cd lammps-stable_2Aug2023_update3
mkdir build && cd build
cmake ../cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX \
  -DCMAKE_PREFIX_PATH=$CONDA_PREFIX \
  -DBUILD_MPI=off \
  -DBUILD_OMP=off \
  -DBUILD_SHARED_LIBS=on \
  -DLAMMPS_EXCEPTIONS=on \
  -DPKG_MANYBODY=on \
  -DPKG_MOLECULE=on \
  -DPKG_KSPACE=on \
  -DPKG_RIGID=on \
  -DPKG_PYTHON=on \
  -DFFT=FFTW3 \
  -DFFTW3_ROOT=$CONDA_PREFIX
make -j$(nproc)
make install
cd /tmp/lammps-stable_2Aug2023_update3/python
pip install .
rm -rf /tmp/lammps-stable_2Aug2023_update3 /tmp/stable_2Aug2023_update3.tar.gz