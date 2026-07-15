# Makefile — vista_multicam: the VISTA paper's reference app
# (Jetson AGX Orin / DeepStream 7.1; see app/README.md).
#
#   make            build ./vista_multicam
#   make clean      remove objects + binary
#
# This app links the VISTA scheduler from ../vista/ as a source dependency —
# there is no installed library and no copy of the scheduler under app/src/.
# vista/ is self-contained and knows nothing about this app; the only coupling
# is the public header included below.
#
# Dependencies (all present on a stock JetPack 6 + DeepStream 7.1 image):
#   gstreamer-1.0 dev, yaml-cpp dev, DeepStream SDK headers + libs.
#
# DS_ROOT overrides the DeepStream install prefix:
#   make DS_ROOT=/opt/nvidia/deepstream/deepstream-7.1

APP      := vista_multicam
SRCDIR   := src
VISTADIR := ../vista

# Objects land next to their sources; the vista object is built out-of-tree
# into build/ so that ../vista/ is never written to by an app build.
BUILDDIR := build
SRCS     := $(wildcard $(SRCDIR)/*.cpp)
OBJS     := $(SRCS:.cpp=.o)
VISTA_SRCS := $(VISTADIR)/src/vista_scheduler.cpp
VISTA_OBJS := $(patsubst $(VISTADIR)/src/%.cpp,$(BUILDDIR)/%.o,$(VISTA_SRCS))
ALL_OBJS := $(OBJS) $(VISTA_OBJS)

DS_ROOT  ?= /opt/nvidia/deepstream/deepstream
PKGS     := gstreamer-1.0 yaml-cpp

CXX      ?= g++
CXXFLAGS += -std=c++17 -O2 -Wall -Wextra \
            -I$(VISTADIR)/include \
            -I$(DS_ROOT)/sources/includes \
            $(shell pkg-config --cflags $(PKGS))
LDLIBS   += $(shell pkg-config --libs $(PKGS)) \
            -L$(DS_ROOT)/lib -lnvdsgst_meta -lnvds_meta \
            -Wl,-rpath,$(DS_ROOT)/lib

all: $(APP)

$(APP): $(ALL_OBJS)
	$(CXX) -o $@ $(ALL_OBJS) $(LDLIBS)

# -MMD emits a .d file per object so header edits rebuild exactly the objects
# that include them (instead of everything).
$(SRCDIR)/%.o: $(SRCDIR)/%.cpp
	$(CXX) $(CXXFLAGS) -MMD -MP -c -o $@ $<

$(BUILDDIR)/%.o: $(VISTADIR)/src/%.cpp | $(BUILDDIR)
	$(CXX) $(CXXFLAGS) -MMD -MP -c -o $@ $<

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

-include $(ALL_OBJS:.o=.d)

clean:
	rm -f $(OBJS) $(OBJS:.o=.d) $(APP)
	rm -rf $(BUILDDIR)

.PHONY: all clean
