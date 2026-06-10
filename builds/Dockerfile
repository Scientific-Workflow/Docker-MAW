FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

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
    zlib1g-dev \
    libosmesa6 \
    libgl1 \
    libegl1 \
    libopengl0 \
    libglib2.0-0 \
    libxkbcommon0 \
    libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages \
    ovito \
    "parsl>=2024.0.0" \
    numpy \
    matplotlib \
    Pillow

RUN cd /tmp && \
    wget -q https://github.com/lammps/lammps/archive/refs/tags/stable_2Aug2023_update3.tar.gz && \
    tar xzf stable_2Aug2023_update3.tar.gz && \
    cd lammps-stable_2Aug2023_update3 && \
    mkdir build && cd build && \
    cmake ../cmake \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DBUILD_MPI=off \
      -DBUILD_OMP=off \
      -DBUILD_SHARED_LIBS=on \
      -DLAMMPS_EXCEPTIONS=on \
      -DPKG_MANYBODY=on \
      -DPKG_MOLECULE=on \
      -DPKG_KSPACE=on \
      -DPKG_RIGID=on \
      -DPKG_PYTHON=on \
      -DFFT=FFTW3 && \
    make -j$(nproc) && \
    make install && \
    echo '/usr/local/lib' > /etc/ld.so.conf.d/local.conf && ldconfig && \
    cd /tmp/lammps-stable_2Aug2023_update3/python && \
    PYTHONNOUSERSITE=1 pip3 install . --break-system-packages && \
    rm -rf /tmp/lammps-stable_2Aug2023_update3 /tmp/stable_2Aug2023_update3.tar.gz

WORKDIR /app

ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYOPENGL_PLATFORM=osmesa
ENV OVITO_GUI_MODE=0
ENV LD_LIBRARY_PATH=/usr/local/lib