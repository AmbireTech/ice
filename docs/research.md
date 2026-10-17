# Ambire hardware signer — research and architecture (2026-10-14)

Three research passes (threat landscape, protocol and integration, JLC
sourcing). Sources are inline. "Inference" marks a judgement rather than a
sourced fact.

## What actually lost money, 2024 → Oct 2026

| Incident | What broke | Would hardware have stopped it? |
|---|---|---|
| Bybit, Feb 2025, ~$1.4 B | Safe{Wallet} frontend JS swapped `to`/`operation=1` (DELEGATECALL); signers' hardware wallets showed an undecodable payload ([NCC](https://www.nccgroup.com/research/in-depth-technical-analysis-of-the-bybit-hack/)) | Only with **on-device Safe transaction decoding** — a UX/firmware problem |
| WazirX (Jul 2024), Radiant (Oct 2024) | Multisig signers on hardware wallets blind-signed malicious payloads | Same |
| Coldcard, Jul 2026, ~$116–130 M | Build misconfiguration → seed generated from a weak software RNG ([TRM](https://www.trmlabs.com/resources/blog/the-largest-hardware-wallet-exploit-of-2026-inside-the-usd-116-million-coldcard-hack)) | Keys generated **inside a certified secure element** remove the MCU's RNG from the picture |
| Ledger via reseller, Sep–Oct 2026 | LTE implant with eSIM, hidden under the display/in the battery; visible tell was an antenna wire ([BleepingComputer](https://bleepingcomputer.com/news/security/ledger-confirms-hardware-wallet-spy-implant-behind-crypto-thefts)) | A **clear enclosure** makes this class visible |
| Donjon vs Trezor Safe 3 (2025), RP2350 challenge, Espressif AR2026-005 | Voltage/EM fault injection on general-purpose MCUs | MCU choice; keep secrets out of the MCU |
| Donjon vs TROPIC01 (Jun 2026), vs Tangem (Jul 2026) | Laser FI on secure elements | Layering; nothing is immune |

**Takeaway:** the large losses are UI deception (blind signing), entropy bugs
and physical implants. None of them needed a lab. A Safe-only signer that
decodes Safe transactions on the device and never holds a seed addresses the
first two directly; the clear case addresses the third.

## Architecture decision

**Key model: two non-extractable keys, generated inside two secure elements
from two vendors (NXP SE050 and Infineon OPTIGA Trust M), no seed phrase;
recovery is the Safe's other owners.** A Donjon-class laser attack on one chip
yields one key, which is not enough: the account treats the device as one owner
that needs both signatures (a 2-of-2 nested Safe as that owner, or an
equivalent signer contract). The OPTIGA has no secp256k1, so its key is P-256
and verifies through the passkey path below. A seed split across two chips, as
in seed-phrase wallets, does not apply: these keys never leave their chips.

- The SE050 holds **secp256k1** keys natively (Koblitz family in NXP's platform
  table; explicitly listed in the SE051 FIPS policy), so the device is a plain
  EOA owner on every Safe on every chain, verified by `ecrecover` — no
  precompile, no extra contract, no per-chain deployment.
- It also holds **P-256**, so the same device can act as a WebAuthn-style
  passkey owner via Safe's audited `SafeWebAuthnSignerFactory`, now cheap
  everywhere that matters: EIP-7951 P256VERIFY is live on mainnet since Fusaka
  (2025-12-03, 6,900 gas) and RIP-7212 is on OP Stack/Base, Arbitrum, zkSync,
  Scroll, Polygon ([EIP-7951](https://eips.ethereum.org/EIPS/eip-7951)).
- The **PIN and its retry counter live in the SE** (SE050 authentication
  objects + key policies). An MCU compromise then yields at most a signing
  oracle while the device is unlocked, never the key and never a PIN bypass.
- The SE050's datasheet and APDU spec are public and NXP's Plug & Trust host
  library is open source: firmware can be fully open.
- Post-quantum: no buyable SE does PQ signing yet (SEALSQ VaultIC409 samples
  Q2 2027). ML-DSA-44 signs in ~25 ms on a Cortex-M33 class core (pqm4), so a
  later PQ co-owner key is a firmware feature; on-chain ML-DSA/Falcon verifiers
  are still 0.85–4.9 M gas and unaudited, and EIP-8051/8052 are drafts.
  *Inference:* ship secp256k1/P-256 now; design the account for a hybrid PQ
  owner later — the smart account is what makes that swap possible.

**Main MCU: STM32U585 (Cortex-M33, TrustZone, PSA Level 3 / SESIP 3).** Same
family as Trezor Safe 5/7. Has DCMI for the camera and USB FS. Avoided: ESP32
(repeated FI advisories incl. AR2026-005 on the S3), RP2350 (OTP readout by
double glitch), STM32F4 (Safe 3 break). LQFP100 rather than WLCSP: bare die is
light-sensitive (the Raspberry Pi 2 xenon-flash reset) and the case is clear.

**A seed protected by the STM32U5 alone: a device-bound slow KDF.** For a
seed-phrase mode (a Trezor-style wallet) without relying on a second secure
element, the MCU's own hardware key does the work. The STM32U5's SAES engine
can use a derived hardware-unique key (DHUK) that software can never read.
Stretch the PIN by chaining SAES encryptions under that key, each step's output
fed into the next, roughly 100k-1M steps (*estimate*: about a second on the
device; measure at bring-up). The result decrypts the seed stored in flash.
- Unique to the device: the chain only runs inside this chip.
- Fast on the device, impossible off it: guessing PINs offline needs the DHUK,
  so an attacker must first extract it with a physical attack on ST's silicon
  (fault injection, invasive probing). That additional attack is the accepted
  compromise.
- Limit: it slows guessing only once the chip's key is out; it adds no secrecy
  after that. A 6-digit PIN is then a million guesses, so offer an optional
  passphrase for protection that does not depend on silicon.
- Optional extra layer: also mix in a secret the SE050 releases only for the
  right PIN (with its retry counter), so an attacker must break both NXP's and
  ST's chips. The second secure element (OPTIGA) adds a third vendor; its
  sourcing is deprioritized in favour of this mechanism.
- To verify, not assumed: that the DHUK is unreadable by software in our
  TrustZone/RDP configuration (RM0456), SAES throughput with the DHUK, and the
  STM32U5's published fault-injection results.

**Radio: a separate Nordic nRF52840 module (Ebyte E73), BLE only, untrusted.**
It is a pipe: it never sees a key or a PIN, so its own FI history does not
matter. Its supply, and the USB data lines, are cut by the **airgap switch in
hardware** (see below). No Wi-Fi on the board at all.

**Camera for QR mode.** Air-gapped clear signing has to be bidirectional:
a display-only device would have to sign a hash it cannot recompute. The
device scans BC-UR / ERC-4527 animated QR (Ambire already speaks ERC-4527 via
Keycard Shell) and shows the signature as a QR.

## Transports

| Host | Primary | Notes |
|---|---|---|
| Ambire extension (Chrome) | USB HID via WebHID | Works from the MV3 service worker once granted |
| Ambire extension, wireless | Web Bluetooth | Extension pages only, not the service worker |
| iOS app | BLE (CoreBluetooth) with app-layer authenticated encryption | Safari has no Web Bluetooth |
| Android app | BLE, USB-C | |
| Airgap | Animated QR both ways (BC-UR / ERC-4527) | Camera on the device |

NFC (ST25DV dynamic tag, JLC-stocked) is a cheap later add for iOS tap-to-sign;
left off v0 because a coil needs a ground-free area the size of the board's
free space.

## What the device must show for a Safe transaction

Never accept a hash. Receive all SafeTx fields and recompute the EIP-712 domain
and safeTxHash on the device. Show chain, Safe address (pinned at enrolment),
`to` (address-book labels), value, nonce; decode `data` with ERC-7730
descriptors, recursively through MultiSend; **DELEGATECALL gets a full-screen
warning and is refused unless the target code hash is on an allowlist**
(MultiSendCallOnly, SignMessageLib); flag addOwner/removeOwner/swapOwner/
changeThreshold/setGuard/enableModule/setFallbackHandler; show gas/refund fields
and warn on a refund to a non-owner. Values the device cannot verify (live nonce,
threshold) are shown as "claimed by the wallet".

## Clear enclosure

Precedent: Coldcard's see-through case. It makes bulky implants obvious (the
2026 Ledger implant's tell was a visible antenna wire); it does not detect a
chip swap, a part under a BGA or an interposer. Pair it with a published
reference photo per board revision, a serialised tamper bag, and an SE-attested
device certificate the Ambire app checks. The board is designed to be looked
at: all parts on the back face, which is the face the clear back shows.

## Licensing (not legal advice)

trezor-firmware is plain GPLv3; forking it makes the whole firmware GPLv3 and
GPLv3 §6 requires letting users install their own builds on a consumer device.
The owner accepts both (2026-10-17) and wants to start from Trezor's firmware:
see [direction.md](direction.md) for the closest Trezor model (T3T2, not Model T)
and what porting it takes.

## Competitive notes

Ledger Multisig (Oct 2025) is Safe-based with clear signing and a per-tx fee;
Keycard Shell (STM32H573 + JCOP card + global-shutter camera, switchable USB)
claims on-device Safe decoding. Austin Griffith's picowallet (2026-09) is a
working prototype of the same key model — P-256 generated in an ATECC608,
a vault contract, a timelocked recovery address — on an RP2350.
