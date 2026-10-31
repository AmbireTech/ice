# The nRF52840 has peripherals sharing a base address (power/clock/bprot,
# acl/flash-controller); silence the devicetree compiler's warning about it,
# as Zephyr's own nRF52840 boards do.
list(APPEND EXTRA_DTC_FLAGS "-Wno-unique_unit_address_if_enabled")
