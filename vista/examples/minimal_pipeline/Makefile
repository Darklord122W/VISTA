# Makefile — minimal_pipeline (VISTA integration template).
#
#   make            build ./minimal_pipeline
#   make clean
#
# Builds vista's sources directly (../../src) rather than linking libvista.a,
# so this example compiles from a fresh clone with one command and no install
# step. A real integration should link the library instead — see
# ../../Makefile ("make install") or ../../CMakeLists.txt (find_package(vista)).
#
# NOTE: no -DNDEBUG. The assert(st.ledger_closes()) in main.cpp is the point of
# this example; a release build that defines NDEBUG would silently delete it.
# (The explicit `if (!st.ledger_closes()) rc = 1;` survives regardless.)

APP      := minimal_pipeline
VISTA    := ../..
SRCS     := main.cpp $(VISTA)/src/vista_scheduler.cpp
OBJS     := $(notdir $(SRCS:.cpp=.o))

DS_ROOT  ?= /opt/nvidia/deepstream/deepstream
PKGS     := gstreamer-1.0

CXX      ?= g++
CXXFLAGS += -std=c++17 -O2 -Wall -Wextra \
            -I$(VISTA)/include \
            -I$(DS_ROOT)/sources/includes \
            $(shell pkg-config --cflags $(PKGS))
LDLIBS   += $(shell pkg-config --libs $(PKGS)) \
            -L$(DS_ROOT)/lib -lnvdsgst_meta -lnvds_meta \
            -Wl,-rpath,$(DS_ROOT)/lib \
            -lpthread

all: $(APP)

$(APP): $(OBJS)
	$(CXX) -o $@ $(OBJS) $(LDLIBS)

main.o: main.cpp
	$(CXX) $(CXXFLAGS) -MMD -MP -c -o $@ $<

vista_scheduler.o: $(VISTA)/src/vista_scheduler.cpp
	$(CXX) $(CXXFLAGS) -MMD -MP -c -o $@ $<

-include $(OBJS:.o=.d)

clean:
	rm -f $(OBJS) $(OBJS:.o=.d) $(APP)

.PHONY: all clean
