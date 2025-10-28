# specify python version as build arg
ARG PYTHON_VERSION=3.9.17

# use official python image as base
# https://hub.docker.com/_/python
FROM python:$PYTHON_VERSION

# github token for private repos
ARG GITHUB_TOKEN

# specify labrad (scalabrad) version
# https://github.com/labrad/scalabrad/releases
ARG LABRAD_VERSION=0.8.3

# labrad environment variables
# https://github.com/labrad/pylabrad/blob/master/CONFIG.md
ENV LABRADUSER=
ENV LABRADPASSWORD=Cooper2e
ENV LABRADHOST=localhost
ENV LABRADPORT=7682
ENV LABRAD_TLS=starttls
ENV LABRAD_TLS_PORT=7643

# set working directory to root
USER root
WORKDIR /root

# install apt dependencies
# jre (java runtime environment) is required to run scalabrad
RUN apt update && \
    apt install --yes --no-install-recommends \
    default-jre \
    vim nano curl wget \
    libgirepository1.0-dev \
    iputils-ping iproute2 dnsutils \
    libcairo2-dev pkg-config gir1.2-gtk-3.0 libgtk-3-dev

ENV JAVA_HOME=/usr/lib/jvm/default-java

# download and extract precompiled scalabrad
RUN curl -L -O https://github.com/labrad/scalabrad/releases/download/v${LABRAD_VERSION}/scalabrad-${LABRAD_VERSION}.tar.gz && \
    tar -xzf scalabrad-${LABRAD_VERSION}.tar.gz && \
    rm scalabrad-${LABRAD_VERSION}.tar.gz
# add labrad command to path
ENV PATH=$PATH:/root/scalabrad-${LABRAD_VERSION}/bin

# download libraries in /root/lib
ENV LIB_PATH=/root/lib
ENV PYTHONPATH=$PYTHONPATH:$LIB_PATH
WORKDIR $LIB_PATH

# # install pip dependencies
# # pylabrad is a python wrapper for labrad (scalabrad)
# # https://github.com/labrad/pylabrad
# RUN pip install --upgrade pip && \
#     pip install --no-cache-dir \
#     pylabrad \
#     h5py

# not upgrade pip
RUN pip install git+https://github.com/labrad/pylabrad.git@u/maffoo/actions \
    h5py

# install pip dependencies for MT
RUN pip install tqdm IPython typer scikit-learn lmfit qutip networkx matplotlib numpy scipy pygobject==3.50.0

# install quelware
RUN git clone -b 0.8.13 https://github.com/quel-inc/quelware.git
WORKDIR $LIB_PATH/quelware/quel_ic_config
RUN ./download_prebuilt.sh
RUN tar xfv quelware_prebuilt.tgz 
RUN pip install -r requirements_simplemulti_standard.txt -r requirements_dev_addon.txt

# back to root directory
WORKDIR /root

# copy local files into container image
COPY config/ config/
COPY script/ script/

# make scripts executable
RUN chmod +x script/*
ENV PATH=$PATH:/root/script

# run labrad and servers when container is started
CMD ["start.sh"]
