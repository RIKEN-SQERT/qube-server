#!/bin/bash

export LIB_PATH=/root/lib
export PYTHONPATH=$PYTHONPATH:$LIB_PATH
export PYTHONPATH=$PYTHONPATH:$LIB_PATH/quelware/qube_master
export PYTHONPATH=$PYTHONPATH:$LIB_PATH/measurement_tool_orion:$LIB_PATH/measurement_tool_orion_automation
export QUBECALIB_PATH_TO_ROOT=$LIB_PATH/qube-calib
export LABRADNODE=quel-020_docker

# make log directory if it doesn't exist
mkdir -p $HOME/log

# start labrad as a background process
ulimit -n 65536
labrad --registry file:///root/config/registry.sqlite < /dev/null >& /root/log/labrad.log &

# env LABRADHOST=localhost \
#       LABRADPASSWORD=Cooper2e \
#       labrad &

# save labrad process id
labrad_pid=$!

# ToDo: need to write connection check script instead of sleep (need to wait until labrad starts)
sleep 5

# start data vault server
python $LIB_PATH/labrad-servers/data_vault.py >& $HOME/log/data-vault.log &

# start qube server
# NOTE: without QUBE_SERVER env, qube server will run in debug mode
QUBE_SERVER="QuBE Server" \
UDP_RW_BIND_ADDRESS="10.0.0.3" \
python $LIB_PATH/qubesrv/app.py >& $HOME/log/qube-server.log &
#python $LIB_PATH/qube-calib/QubeServer.py >& $HOME/log/qube-server.log &

# use 'wait' command to make the script pause and keep the container running
wait $labrad_pid

#tail -f
