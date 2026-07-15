# =============================================================================
# VISTA artifact — top-level Makefile
#
# This repository ships CODE, not measurements. Every target here builds
# something from sources in this tree; none of them needs run data, and none
# of them reads a measurement archive, because no archive is distributed here.
#
#       make lib             vista/libvista.a — the deliverable
#       make app             app/vista_multicam — the experiment application
#       make example         vista/examples/minimal_pipeline
#       make clean           remove build products
#
# `make` with no target builds the library and the app.
#
# The C++ targets need DeepStream 7.1 headers; the library and the example
# build on any machine that has them, while running the application needs a
# Jetson-class board with engines and clips.
#
# TO PRODUCE DATA, AND THEN TABLES OR FIGURES — not a make target, on purpose:
#       harness/run_campaign.sh core     take the measurements (Jetson)
#       export VISTA_DATA_ROOT=...       point the analysis at them
#       python3 analysis/make_all.py     score them; write tables and figures
# See harness/README.md and analysis/README.md. There is no `make tables`:
# the tables it used to build were checked against a shipped archive that no
# longer ships, so the target could only ever fail. The analysis scripts run
# directly, against data you supply, and say so themselves if you supply none.
#
# DS_ROOT is the DeepStream install prefix; it is passed down to every C++
# build. Override for a versioned path:
#       make DS_ROOT=/opt/nvidia/deepstream/deepstream-7.1
# =============================================================================

DS_ROOT ?= /opt/nvidia/deepstream/deepstream

REPO    := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
EXAMPLE := $(REPO)/vista/examples/minimal_pipeline

export DS_ROOT

.PHONY: all lib app example clean help

all: lib app

help:
	@sed -n '2,30p' $(firstword $(MAKEFILE_LIST))

# The scheduler library. This is what an integrator consumes; it depends on
# nothing in app/ and knows nothing about the experiments.
lib:
	$(MAKE) -C $(REPO)/vista DS_ROOT=$(DS_ROOT)

# The experiment application: links the scheduler and adds the replay pacer,
# the skew injection, and the metrics/dets/sched writers the campaigns need.
# NOT the same thing as the library — see harness/vista_env.sh.
app:
	$(MAKE) -C $(REPO)/app DS_ROOT=$(DS_ROOT)

# The integration template. Builds vista's sources directly, so this is also
# the fastest check that the public header and the SDK headers agree.
example:
	$(MAKE) -C $(EXAMPLE) DS_ROOT=$(DS_ROOT)

# Removes build products only. It does not touch runs/ or anything under
# $VISTA_DATA_ROOT — deleting a 90-minute campaign because someone typed
# `make clean` is not a thing this repository will do.
clean:
	$(MAKE) -C $(REPO)/vista clean
	$(MAKE) -C $(REPO)/app clean
	$(MAKE) -C $(EXAMPLE) clean
	find $(REPO) -name '__pycache__' -type d -prune -exec rm -rf {} +

# These targets were removed when the measurement archive stopped shipping.
# They are declared .PHONY and made to fail: without this, the same-named
# directories (figures/, analysis/) shadow the goal and `make figures` exits 0
# with "Nothing to be done", which reads as success.
.PHONY: figures tables tables-raw gates
figures tables tables-raw gates:
	@echo 'vista: `make $@` no longer exists: this repository ships code, not runs.' >&2
	@echo '  Produce a run archive on a Jetson with harness/run_campaign.sh, then:' >&2
	@echo '    export VISTA_DATA_ROOT=<your run dir> && python3 analysis/make_all.py' >&2
	@echo '  See harness/README.md and analysis/README.md.' >&2
	@exit 2
