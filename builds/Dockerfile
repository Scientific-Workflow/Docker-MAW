FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y python3 python3-pip python3-dev build-essential wget git cmake libfftw3-dev libpng-dev libjpeg-dev && rm -rf /var/lib/apt/lists/*
RUN pip3 install --upgrade pip

# Build LAMMPS from source WITHOUT MPI (no MPI libraries installed at all)
RUN cd /tmp && \
    wget https://github.com/lammps/lammps/archive/refs/tags/stable_2Aug2023_update3.tar.gz && \
    tar xzf stable_2Aug2023_update3.tar.gz && \
    cd lammps-stable_2Aug2023_update3 && \
    mkdir build && cd build && \
    cmake ../cmake \
      -DBUILD_MPI=off \
      -DBUILD_OMP=off \
      -DBUILD_SHARED_LIBS=on \
      -DLAMMPS_EXCEPTIONS=on \
      -DPKG_MANYBODY=on \
      -DPKG_MOLECULE=on \
      -DPKG_KSPACE=on \
      -DPKG_RIGID=on \
      -DPKG_PYTHON=on \
      -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DFFT=FFTW3 && \
    make -j$(nproc) && \
    make install && \
    cd /tmp && rm -rf lammps-stable_2Aug2023_update3 stable_2Aug2023_update3.tar.gz

ENV LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
ENV LAMMPS_POTENTIALS=/usr/local/share/lammps/potentials

# Install LAMMPS Python bindings
RUN cd /tmp && \
    wget https://github.com/lammps/lammps/archive/refs/tags/stable_2Aug2023_update3.tar.gz && \
    tar xzf stable_2Aug2023_update3.tar.gz && \
    cd lammps-stable_2Aug2023_update3/python && \
    pip3 install . && \
    cd /tmp && rm -rf lammps-stable_2Aug2023_update3 stable_2Aug2023_update3.tar.gz

# Verify LAMMPS works without MPI
RUN python3 -c "from lammps import lammps; lmp=lammps(cmdargs=['-screen','none']); print('LAMMPS version:', lmp.version()); lmp.close()"

# Install Python packages
RUN pip3 install ovito
RUN pip3 install "parsl>=2024.0.0"
RUN pip3 install numpy
RUN pip3 install matplotlib
RUN pip3 install pillow
RUN pip3 install imageio

WORKDIR /app
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYOPENGL_PLATFORM=osmesa
ENV OVITO_GUI_MODE=0
