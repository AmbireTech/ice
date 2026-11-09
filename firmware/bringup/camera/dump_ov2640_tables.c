#include <stdio.h>
// Dumps esp32-camera's OV2640 tables with their C expressions evaluated, for
// io/camera/stm32u5/camera_sensors.h:
//   echo '#define DRAM_ATTR' > esp_attr.h
//   cc -w -I. -I <esp32-camera>/sensors/private_include dump_ov2640_tables.c && ./a.out
#include "ov2640_settings.h"
static void dump(const char *name, const uint8_t (*t)[2]) {
  printf("static const uint8_t %s[][2] = {\n", name);
  for (int i = 0;; i++) {
    printf("    {0x%02x, 0x%02x},\n", t[i][0], t[i][1]);
    if (t[i][0] == 0) break;
  }
  printf("};\n\n");
}
int main(void) {
  dump("ov2640_init_regs", ov2640_settings_cif);
  dump("ov2640_to_cif_regs", ov2640_settings_to_cif);
  dump("ov2640_to_svga_regs", ov2640_settings_to_svga);
  dump("ov2640_to_uxga_regs", ov2640_settings_to_uxga);
  dump("ov2640_yuv422_regs", ov2640_settings_yuv422);
  printf("// ratio 1x1: %u %u %u %u\n", ratio_table[7].offset_x, ratio_table[7].offset_y, ratio_table[7].max_x, ratio_table[7].max_y);
  return 0;
}
