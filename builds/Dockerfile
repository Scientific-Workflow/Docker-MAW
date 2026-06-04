FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-dev \
    build-essential wget git \
    libosmesa6-dev libgl1-mesa-glx libglib2.0-0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --upgrade pip

RUN pip3 install lammps

RUN pip3 install ovito --extra-index-url https://pypi.ovito.org/

RUN pip3 install \
    numpy \
    matplotlib \
    pillow \
    imageio \
    imageio-ffmpeg \
    "parsl>=2024.0.0"

WORKDIR /app

ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYOPENGL_PLATFORM=osmesa
ENV OVITO_GUI_MODE=0
