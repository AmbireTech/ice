# The device's QR decoder, built from the firmware's own sources (untouched)
set(QR ${CMAKE_CURRENT_LIST_DIR}/../../../../trezor-firmware/core/embed/io/camera)
idf_component_register(
    SRCS "main.c" "${CMAKE_CURRENT_LIST_DIR}/../../qrcommon/qr_roi.c"
         "${QR}/qr/qr_scan.c" "${QR}/qr/quirc_decode.c"
         "${QR}/qr/quirc_identify.c" "${QR}/qr/quirc_version_db.c"
    INCLUDE_DIRS "." "${CMAKE_CURRENT_LIST_DIR}/../../qrcommon" "${CMAKE_CURRENT_LIST_DIR}/../../qrdecode/stub" "${QR}/inc" "${QR}/qr"
    PRIV_REQUIRES esp32-camera esp_timer esp_psram esp_driver_gpio esp_driver_uart esp_partition esp_rom)
set_source_files_properties(
    "${QR}/qr/qr_scan.c" "${QR}/qr/quirc_decode.c"
    "${QR}/qr/quirc_identify.c" "${QR}/qr/quirc_version_db.c"
    PROPERTIES COMPILE_OPTIONS "-w")
