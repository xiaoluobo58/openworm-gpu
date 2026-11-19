# syntax=docker/dockerfile:1

FROM nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:29e5e3425e2e0f5a4e97c9fb4695ba4887cd78210a43cf94c3bcafc6ab01c5e6 AS pocl-cuda-build

ARG POCL_COMMIT=a6f7e5cc223cdf6254ff62ea1f37a9aa8a52d778
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates clang-18 cmake cuda-nvcc-12-9=12.9.86-1 git libclang-18-dev \
    libclang-cpp18-dev libhwloc-dev llvm-18-dev ocl-icd-opencl-dev \
    pkg-config python3 zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git init /src/pocl && git -C /src/pocl fetch --depth 1 https://github.com/pocl/pocl.git "$POCL_COMMIT" \
    && git -C /src/pocl checkout --detach FETCH_HEAD \
    && cmake -S /src/pocl -B /src/pocl/build \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/pocl-cuda \
        -DCMAKE_INSTALL_LIBDIR=lib -DWITH_LLVM_CONFIG=/usr/bin/llvm-config-18 \
        -DPOCL_INSTALL_ICD_VENDORDIR=/opt/pocl-cuda/etc/OpenCL/vendors \
        -DCUDAToolkit_ROOT=/usr/local/cuda -DCUDA_TOOLKIT_ROOT_DIR=/usr/local/cuda \
        -DENABLE_CUDA=ON -DENABLE_ICD=ON -DENABLE_HOST_CPU_DEVICES=OFF \
        -DENABLE_SPIRV=OFF -DENABLE_TESTS=OFF -DENABLE_EXAMPLES=OFF \
        -DINSTALL_OPENCL_HEADERS=OFF \
    && cmake --build /src/pocl/build --parallel 2 \
    && cmake --install /src/pocl/build

FROM ubuntu:24.04 AS openworm-base

LABEL maintainer="David Lung (lungdm@gmail.com); Padraig Gleeson (p.gleeson@gmail.com)"

ARG USR=ow
ENV USER=$USR

RUN apt-get update && \
  apt-get upgrade -y && \
  apt-get dist-upgrade -y

RUN mkdir -p /etc/sudoers.d && \
  export uid=1000 gid=1000 && \
  mkdir -p /home/$USER && \
  echo "$USER:x:${uid}:${gid}:$USER,,,:/home/$USER:/bin/bash" >> /etc/passwd && \
  echo "$USER:x:${uid}:" >> /etc/group && \
  echo "$USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/$USER && \
  chmod 0440 /etc/sudoers.d/$USER && \
  chown ${uid}:${gid} -R /home/$USER

ENV DEBIAN_FRONTEND=noninteractive


################################################################################
########     Update/install essential libraries

RUN apt-get update
RUN apt-get install -y --no-install-recommends apt-utils \
  wget nano htop build-essential make git automake autoconf \
  g++ rpm libtool libncurses5-dev zlib1g-dev bison flex \
  sudo xorg openbox x11-xserver-utils \
  libxext-dev libncurses-dev mercurial \
  freeglut3-dev libglu1-mesa-dev libglew-dev python3-dev python3-pip \
  maven openjdk-8-jdk \
  libnuma1 \
  openmpi-bin  libopenmpi-dev \
  libgl1 libglx-mesa0  libgl1-mesa-dri libfreetype6-dev \
  libxft-dev unzip ffmpeg xvfb tmux \
  ca-certificates clinfo ocl-icd-opencl-dev

#RUN  sudo pip install --upgrade pip

RUN sudo usermod -a -G video $USER

#USER $USER
ENV HOME=/home/$USER
WORKDIR $HOME



################################################################################
########     Install NEURON simulator

RUN sudo pip install neuron==8.2.6  --break-system-packages


################################################################################
########     Install c302 for building neuronal network models

RUN git clone https://github.com/openworm/c302.git && \
  cd c302 && \
  git checkout --detach 3869879308204fa83a7fda608fcc445f23920000 && \
  sudo pip install .  --break-system-packages

# Note: pyNeuroML installed with the above library



################################################################################
########     Install Sibernetic for the worm body model

COPY docker/sibernetic-opencl-env.patch /tmp/sibernetic-opencl-env.patch
RUN git clone https://github.com/openworm/sibernetic.git && \
  cd sibernetic && \
  git checkout --detach 5ee2b2262b8d59eaca00d451399a08ae0513afad && \
  git apply /tmp/sibernetic-opencl-env.patch


################################################################################
########     Install extra Python dependencies

RUN sudo pip install ruff  --break-system-packages


################################################################################
########     Set some paths//environment variables

ENV C302_HOME=$HOME/c302/c302
ENV SIBERNETIC_HOME=$HOME/sibernetic
ENV PYTHONPATH=$HOME/c302:$SIBERNETIC_HOME

ENV NEURON_MODULE_OPTIONS=-nogui


################################################################################
########     Build Sibernetic

RUN cd sibernetic && \
    install -D inc/OpenCL/cl.hpp /usr/local/include/CL/cl.hpp && \
    make all


################################################################################
########     Set up JupyterLab

#RUN sudo pip install notebook jupyterlab --break-system-packages


################################################################################
########     Copy master python script

# Not working with --chown=$USER:$USER
COPY master_openworm.py openworm_gpu.py $HOME/
RUN sudo chown $USER:$USER $HOME/master_openworm.py

RUN printf '\n\nalias cd..="cd .."\nalias h=history\nalias ll="ls -alth"\n' >> ~/.bashrc



RUN pip list

FROM openworm-base AS gpu

RUN apt-get update && apt-get install -y --no-install-recommends \
    clang-18 libhwloc15 && rm -rf /var/lib/apt/lists/*
COPY --from=pocl-cuda-build /opt/pocl-cuda/ /opt/pocl-cuda/
COPY --from=pocl-cuda-build /usr/local/cuda-12.9/targets/x86_64-linux/lib/libcudart.so.12* /opt/pocl-cuda/lib/
COPY --from=pocl-cuda-build /usr/local/cuda-12.9/nvvm/libdevice/ /usr/local/cuda/nvvm/libdevice/
RUN printf '%s\n' /opt/pocl-cuda/lib > /etc/ld.so.conf.d/openworm-pocl-cuda.conf && ldconfig

ENV OW_DEVICE=GPU \
    OCL_ICD_VENDORS=/opt/pocl-cuda/etc/OpenCL/vendors \
    POCL_DEVICES=cuda \
    POCL_CUDA_TOOLKIT_PATH=/usr/local/cuda \
    POCL_CACHE_DIR=/var/cache/openworm-pocl/cuda \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    LD_LIBRARY_PATH=/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/lib/wsl/lib \
    PYTHONUNBUFFERED=1
CMD ["python3", "master_openworm.py"]

FROM openworm-base AS cpu

RUN apt-get update && apt-get install -y --no-install-recommends pocl-opencl-icd \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1
CMD ["python3", "master_openworm.py"]

