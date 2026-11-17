# The device's video QR decoder, built from the firmware's own source (untouched)
set(QR ${CMAKE_CURRENT_LIST_DIR}/../../../../trezor-firmware/core/embed/io/camera)
idf_component_register(
    SRCS "main.c" "${QR}/qr/fqr.c"
    INCLUDE_DIRS "." "${QR}/qr"
    PRIV_REQUIRES esp32-camera esp_timer esp_psram esp_driver_gpio esp_driver_uart esp_partition esp_rom)
# FQR_PROF: time per decoder stage, for the serial "T" replay
set_source_files_properties("${QR}/qr/fqr.c" PROPERTIES COMPILE_DEFINITIONS "FQR_PROF")
