PROJECT       := Klippy chelper
TARGET        := c_helper.so

CC            := $(CROSS_COMPILE)gcc
SRCS          := pyhelper.o serialqueue.o stepcompress.o itersolve.o trapq.o \
                pollreactor.o msgblock.o trdispatch.o \
                kin_cartesian.o kin_corexy.o kin_corexz.o kin_delta.o \
                kin_deltesian.o kin_polar.o kin_rotary_delta.o kin_winch.o \
                kin_extruder.o kin_shaper.o kin_idex.o
H_FILES       := *.h

CFLAGS        = -Wall -g -O2 -shared -fPIC -flto -fwhole-program -fno-use-linker-plugin
LDFLAGS       = $(CFLAGS)

# To put more focus on warnings, be less verbose as default
# Use 'make V=1' to see the full commands
ifeq ("$(origin V)", "command line")
  BUILD_VERBOSE = $(V)
endif
ifndef BUILD_VERBOSE
  BUILD_VERBOSE = 0
endif
ifeq ($(BUILD_VERBOSE),1)
  Q =
else
  Q = @
endif


all: $(TARGET)
	@echo "Build "$(PROJECT)" done."

$(TARGET): $(SRCS)
	@echo "  LD  " $^
	$(Q)$(CC) $(LDFLAGS) $^ -o $(TARGET)
	@echo "Generate file "$(TARGET)

%.o: %.c $(H_FILES)
	@echo "  CC  " $<
	$(Q)$(CC) $(CFLAGS) -c $< -o $@


.PHONY:clean
clean:
	@echo "Clear .o temporary files."
	$(Q)rm -rf $(SRCS)

.PHONY:disclean
disclean: clean
	@echo "Clear target file "$(TARGET)"."
	$(Q)rm -f $(TARGET)
