FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y python3 python3-pip python3-dev build-essential wget git libopenmpi-dev openmpi-bin libgl1-mesa-glx libosmesa6-dev libglu1-mesa-dev freeglut3-dev mesa-utils libgl1-mesa-dev libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev ffmpeg && rm -rf /var/lib/apt/lists/*
RUN ln -sf $(find /usr/lib -name "libmpi.so.*" | grep -v libmpi_cxx | sort | tail -1) /usr/lib/x86_64-linux-gnu/libmpi.so.12 && ldconfig
RUN pip3 install --upgrade pip
RUN pip3 install lammps
RUN pip3 install mpi4py
RUN pip3 install ovito
RUN pip3 install "parsl>=2024.0.0"
RUN pip3 install numpy
RUN pip3 install matplotlib
RUN pip3 install pillow imageio
WORKDIR /app
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYOPENGL_PLATFORM=osmesa
ENV OVITO_GUI_MODE=0
ENV LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
