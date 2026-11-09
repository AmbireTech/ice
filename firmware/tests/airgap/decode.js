// Decodes the device's own QR texts (crypto-hdkey and eth-signature URs, as
// the firmware encodes them) with the Ambire app's libraries, as the app
// does (UrQrProtocolAdapter.parseAccountPayload / parseSignatureResponse),
// and checks every field.
//
//   node decode.js vectors.json out_dir

const fs = require('fs')
const path = require('path')
const assert = require('assert')

const APP = process.env.AMBIRE_APP || path.join(process.env.HOME, 'Projects/ambire-app')
const NM = path.join(APP, 'node_modules')
const { CryptoHDKey, ETHSignature } = require(path.join(NM, '@keystonehq/bc-ur-registry-eth'))
const { URDecoder } = require(path.join(NM, '@ngraveio/bc-ur'))
const ethers = require(path.join(NM, 'ethers'))

const v = JSON.parse(fs.readFileSync(process.argv[2]))
const out = process.argv[3]

function decode(file, type) {
  const text = fs.readFileSync(path.join(out, file), 'utf8').trim()
  // The firmware's QR component holds 311 alphanumeric characters
  assert(text.length <= 311, `${file}: ${text.length} characters`)
  assert(/^[0-9A-Z $%*+\-./:]+$/.test(text), `${file}: not QR alphanumeric`)
  const decoder = new URDecoder()
  decoder.receivePart(text)
  assert(decoder.isSuccess(), `${file}: ${decoder.resultError()}`)
  const ur = decoder.resultUR()
  assert.strictEqual(ur.type, type)
  console.log(`${file}: ${text.length} characters`)
  return ur.cbor
}

// Pairing: what parseAccountPayload reads
const hdKey = CryptoHDKey.fromCBOR(decode('hdkey.ur', 'crypto-hdkey'))
const root = ethers.HDNodeWallet.fromPhrase(v.mnemonic, '', 'm')
const account = root.derivePath("m/44'/60'/0'")
assert.strictEqual(hdKey.getBip32Key(), account.neuter().extendedKey, 'xpub')
assert.strictEqual(hdKey.getOrigin().getPath(), "44'/60'/0'", 'origin path')
assert.strictEqual(hdKey.getOrigin().getSourceFingerprint().toString('hex'), v.masterFingerprint, 'master fingerprint')
assert.strictEqual(hdKey.getParentFingerprint().toString('hex'), v.account.parentFingerprint, 'parent fingerprint')
assert.strictEqual(hdKey.getChildren().getPath(), '0/*', 'children')
assert.strictEqual(hdKey.getName(), 'Ambire', 'name')
assert.strictEqual(hdKey.isPrivateKey(), false)
// The app derives its accounts from the xpub: they must be the device's
const xpub = ethers.HDNodeWallet.fromExtendedKey(hdKey.getBip32Key())
for (let i = 0; i < 3; i++) {
  assert.strictEqual(xpub.derivePath(`0/${i}`).address, root.derivePath(`m/44'/60'/0'/0/${i}`).address)
}

// Signature: what parseSignatureResponse reads
const input = JSON.parse(fs.readFileSync(path.join(out, 'signature-input.json')))
const sig = ETHSignature.fromCBOR(decode('signature.ur', 'eth-signature'))
assert.strictEqual(Buffer.from(sig.getRequestId()).toString('hex'), input.requestId, 'request id')
assert.strictEqual(sig.getSignature().toString('hex'), input.signature, 'signature')
const s = sig.getSignature()
assert.strictEqual(Number(ethers.hexlify(s.slice(64))), s[64], 'v')
console.log('device URs decode in the app: ok')
