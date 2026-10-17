# Placement experiments

Candidates for the next pass. `board/wallet.kicad_pcb` stays the board; nothing
here is merged into it yet.

| File | What it is |
|---|---|
| `radio-gate-west.kicad_pcb` | The board with the radio gate (NOR, load switch and their parts) moved next to the airgap switch, block wiring redone. Ready for hand routing like the main board. |
| `radio-gate-west-autorouted.kicad_pcb` | That candidate, autorouted to the end. A measure of how easy it is to route, not a board to keep. |
| `main-autorouted.kicad_pcb` | The main board autorouted the same way, for comparison. |

## Result (2026-10-17)

Same autorouter run on both. It repeats itself to within about 0.1 % on the same
input, so a 10 % difference comes from the placement. Blocks were pre-routed in both;
no GND via beside any GND pad; pours stitched.

| | Copper the autorouter had to add | Front / back | Signal vias | Detours and wiggles flagged | Left unconnected |
|---|---|---|---|---|---|
| Main board | 1395 mm | 867 / 528 mm | 104 | 20 | 0 |
| Radio gate by the airgap switch | 1260 mm (−10 %) | 811 / 449 mm | 99 | 22 | 3 → 1 after fixes |

Why it helps: the radio gate takes AIRGAP from the switch and feeds RADIO_3V3 to
the radio. In its old north-west corner, both lines ran 30–40 mm down the west
side. Next to the switch, both are a few millimetres.

Open in the candidate:
- One 3.3 V link (the load switch's CT capacitor and the airgap pull-up, 2.7 mm)
  needs a hand trace: no via fits there.
- RADIO_OFF still comes from the MCU's south side. Moving it to a west pin was
  tried and made the camera escape worse, so it was not kept.

Rejected variants:
- AIRGAP_SENSE moved to a west MCU pin: more detours in the camera bus.
- The UART translator re-oriented: no legal spot in its strip at any rotation.

Rebuild or try another variant with `scripts/placeVariant.py` and judge it with
`scripts/routeGauge.py`.
