// Ambire boards, [ambire_camera]: quirc's identify.c, built with the firmware's
// warnings, less those its code trips (see quirc/README.ambire)
#include "quirc_config.h"
#pragma GCC diagnostic ignored "-Wfloat-conversion"
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "quirc/identify.c"
