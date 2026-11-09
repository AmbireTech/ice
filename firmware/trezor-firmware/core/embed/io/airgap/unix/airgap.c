// Ambire boards, [ambire_airgap]: the emulator has no switch; never airgapped
#include <io/airgap.h>

bool airgap_engaged(void) { return false; }
