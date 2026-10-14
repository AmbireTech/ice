/** CLI JSON config strips platformConfig; module form preserves it. */
export default {
  platformConfig: {
    /**
     * Placement courtyard overlaps (dense LED strip / module placeholders) currently
     * block the autorouter unless this is true. Connectivity is gated separately by
     * `npm run check:connectivity` — do not treat a green build as DRC-clean.
     */
    placementDrcChecksDisabled: true,
  },
}
