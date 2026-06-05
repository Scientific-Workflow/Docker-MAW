FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# System deps — NO OpenMPI, no MPI anything
# LAMMPS is built from source in serial mode (BUILD_MPI=off)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
    cmake \
    wget \
    git \
    libfftw3-dev \
    libpng-dev \
    libjpeg-dev \
    libosmesa6 \
    libgl1 \
    libegl1 \
    libopengl0 \
    libglib2.0-0 \
    libxkbcommon0 \
    libxkbcommon-x11-0 \
    libdbus-1-3 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-render-util0 \
    libxcb-xinerama0 \
    libxcb-xkb1 \
    libxrender1 \
    libxi6 \
    libxtst6 \
    && rm -rf /var/lib/apt/lists/*

# Build LAMMPS from source WITHOUT MPI — serial shared library only
RUN cd /tmp && \
    wget -q https://github.com/lammps/lammps/archive/refs/tags/stable_2Aug2023_update3.tar.gz && \
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

ENV LD_LIBRARY_PATH=/usr/local/lib

# Install LAMMPS Python bindings from source
RUN cd /tmp && \
    wget -q https://github.com/lammps/lammps/archive/refs/tags/stable_2Aug2023_update3.tar.gz && \
    tar xzf stable_2Aug2023_update3.tar.gz && \
    cd lammps-stable_2Aug2023_update3/python && \
    pip3 install . --break-system-packages && \
    cd /tmp && rm -rf lammps-stable_2Aug2023_update3 stable_2Aug2023_update3.tar.gz

# Verify LAMMPS works
RUN python3 -c "from lammps import lammps; lmp=lammps(cmdargs=['-screen','none']); print('LAMMPS OK version:', lmp.version()); lmp.close()"

# Install Python packages
RUN pip3 install ovito --break-system-packages
RUN pip3 install 'parsl>=2024.0.0' numpy matplotlib Pillow --break-system-packages

WORKDIR /app

ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYOPENGL_PLATFORM=osmesa
ENV OVITO_GUI_MODE=0