/*
 * Ambire boards, [ambire_camera]: quirc's build configuration, included first
 * by every file that compiles quirc or reads its structures (their layout
 * depends on it).
 */

#pragma once

// The Cortex-M33's FPU is single precision
#define QUIRC_FLOAT_TYPE float
#define QUIRC_USE_TGMATH

// quirc's internal QUIRC_ASSERTs: off, as in a release build of quirc
#ifndef NDEBUG
#define NDEBUG
#endif
