# Makefile — libvista.a, the plain-make path.
#
#   make                    build ./libvista.a
#   make install PREFIX=/usr/local
#   make clean
#
# This mirrors CMakeLists.txt exactly (same flags, same libraries, same install
# layout). It exists because a DeepStream app that is already a hand-written
# Makefile should not have to adopt CMake to use VISTA. If you use CMake, use
# CMakeLists.txt instead — it also generates vistaConfig.cmake.
#
# Dependencies (all present on a stock JetPack 6 + DeepStream 7.1 image):
#   gstreamer-1.0 dev, DeepStream SDK headers + libs.

LIB      := libvista.a
SRCS     := src/vista_scheduler.cpp
OBJS     := $(SRCS:.cpp=.o)

DS_ROOT  ?= /opt/nvidia/deepstream/deepstream
PREFIX   ?= /usr/local
PKGS     := gstreamer-1.0

CXX      ?= g++
AR       ?= ar
CXXFLAGS += -std=c++17 -O2 -Wall -Wextra -fPIC \
            -Iinclude \
            -I$(DS_ROOT)/sources/includes \
            $(shell pkg-config --cflags $(PKGS))

# Not used to build this archive (ar does not link), but published here because
# it is the link line YOUR application needs. `make print-ldlibs` emits it, so a
# consuming Makefile can do:  LDLIBS += $(shell make -sC path/to/vista print-ldlibs)
VISTA_LDLIBS := -L$(DS_ROOT)/lib -lnvdsgst_meta -lnvds_meta \
                -Wl,-rpath,$(DS_ROOT)/lib \
                $(shell pkg-config --libs $(PKGS)) -lpthread

all: $(LIB)

$(LIB): $(OBJS)
	$(AR) rcs $@ $(OBJS)

# -MMD emits a .d file per object so header edits rebuild exactly the objects
# that include them (instead of everything).
%.o: %.cpp
	$(CXX) $(CXXFLAGS) -MMD -MP -c -o $@ $<

-include $(OBJS:.o=.d)

print-ldlibs:
	@echo '$(VISTA_LDLIBS)'

# The mux INI installs alongside the library on purpose: a VISTA deployment
# whose nvstreammux is configured differently produces numbers that are not
# comparable to the paper's, and the failure is silent. See config/mux_vista.txt.
install: $(LIB)
	install -d $(DESTDIR)$(PREFIX)/lib
	install -d $(DESTDIR)$(PREFIX)/include/vista
	install -d $(DESTDIR)$(PREFIX)/share/vista
	install -m 644 $(LIB) $(DESTDIR)$(PREFIX)/lib/
	install -m 644 include/vista/vista_scheduler.hpp $(DESTDIR)$(PREFIX)/include/vista/
	install -m 644 config/mux_vista.txt $(DESTDIR)$(PREFIX)/share/vista/

clean:
	rm -f $(OBJS) $(OBJS:.o=.d) $(LIB)

.PHONY: all clean install print-ldlibs
