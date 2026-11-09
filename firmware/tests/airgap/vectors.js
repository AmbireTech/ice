// Test vectors for the airgapped QR flow, made with the Ambire app's own
// libraries (its node_modules): @keystonehq/bc-ur-registry-eth builds the
// eth-sign-request payloads, @ngraveio/bc-ur cuts them into the QR parts the
// app animates, ethers and @metamask/eth-sig-util give the digests the device
// must sign.
//
//   node vectors.js out.json        (AMBIRE_APP=~/Projects/ambire-app by default)

const fs = require('fs')
const path = require('path')

const APP = process.env.AMBIRE_APP || path.join(process.env.HOME, 'Projects/ambire-app')
const NM = path.join(APP, 'node_modules')
const { EthSignRequest, DataType } = require(path.join(NM, '@keystonehq/bc-ur-registry-eth'))
const { UREncoder } = require(path.join(NM, '@ngraveio/bc-ur'))
const { chooseFragments } = require(path.join(NM, '@ngraveio/bc-ur/dist/fountainUtils'))
const ethers = require(path.join(NM, 'ethers'))
const { TypedDataUtils, SignTypedDataVersion } = require(path.join(NM, '@metamask/eth-sig-util'))

const MNEMONIC = 'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about'
const root = ethers.HDNodeWallet.fromPhrase(MNEMONIC, '', 'm')
const account = root.derivePath("m/44'/60'/0'")
const masterFp = root.fingerprint.slice(2) // hex, 8 digits
const addressAt = (i) => root.derivePath(`m/44'/60'/0'/0/${i}`).address
const REQUEST_ID = '9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d'

// Deterministic data
let seed = 0x12345678
const rand = () => {
  seed = (Math.imul(seed, 1103515245) + 12345) >>> 0
  return seed
}
const randBytes = (n) => Buffer.from(Array.from({ length: n }, () => rand() & 0xff))

function request(name, signData, dataType, index, chainId, extra = {}) {
  const req = EthSignRequest.constructETHRequest(
    signData,
    dataType,
    `m/44'/60'/0'/0/${index}`,
    masterFp,
    REQUEST_ID,
    chainId,
    addressAt(index),
    'ambire'
  )
  const ur = req.toUR()
  // What the app shows: 200-byte fragments, upper case
  const encoder = new UREncoder(ur, 200, 0)
  const count = encoder.fragmentsLength === 1 ? 1 : 3 * encoder.fragmentsLength + 20
  const parts = []
  for (let i = 0; i < count; i++) parts.push(encoder.nextPart().toUpperCase())
  return {
    name,
    dataType,
    index,
    chainId: chainId === undefined ? null : chainId,
    address: addressAt(index).toLowerCase(),
    signData: signData.toString('hex'),
    urType: ur.type,
    cbor: ur.cbor.toString('hex'),
    seqLen: encoder.fragmentsLength,
    parts,
    ...extra,
  }
}

function tx(name, fields, index) {
  const t = ethers.Transaction.from(fields)
  const unsigned = Buffer.from(t.unsignedSerialized.slice(2), 'hex')
  return request(name, unsigned, fields.type === 0 ? DataType.transaction : DataType.typedTransaction, index, Number(fields.chainId), {
    digest: ethers.keccak256(unsigned).slice(2),
  })
}

function personal(name, message, index) {
  return request(name, message, DataType.personalMessage, index, undefined, {
    digest: ethers.hashMessage(message).slice(2),
  })
}

function typed(name, data, index) {
  const json = Buffer.from(JSON.stringify(data), 'utf8')
  const chainId = typeof data.domain?.chainId === 'number' ? data.domain.chainId : undefined
  return request(name, json, DataType.typedData, index, chainId, {
    digest: TypedDataUtils.eip712Hash(data, SignTypedDataVersion.V4).toString('hex'),
  })
}

const to = '0x' + 'ab'.repeat(20)
const requests = [
  tx('legacy', { type: 0, chainId: 1, nonce: 7, gasPrice: 20_000_000_000n, gasLimit: 21000, to, value: 10n ** 16n, data: '0x' }, 0),
  tx('legacy-bsc-data', { type: 0, chainId: 56, nonce: 0, gasPrice: 3_000_000_000n, gasLimit: 100000, to, value: 0, data: '0xa9059cbb' + '00'.repeat(12) + 'cd'.repeat(20) + '00'.repeat(31) + '64' }, 1),
  tx('eip1559', { type: 2, chainId: 137, nonce: 300, maxPriorityFeePerGas: 30_000_000_000n, maxFeePerGas: 200_000_000_000n, gasLimit: 90000, to, value: 123456789n, data: '0x' + randBytes(100).toString('hex'),
    accessList: [{ address: '0x' + '12'.repeat(20), storageKeys: ['0x' + '00'.repeat(31) + '01', '0x' + 'ff'.repeat(32)] }] }, 2),
  tx('eip1559-create', { type: 2, chainId: 8453, nonce: 1, maxPriorityFeePerGas: 1n, maxFeePerGas: 2n, gasLimit: 3_000_000, to: null, value: 0, data: '0x' + randBytes(1500).toString('hex') }, 0),
  tx('eip1559-large', { type: 2, chainId: 1, nonce: 2, maxPriorityFeePerGas: 1n, maxFeePerGas: 2n, gasLimit: 3_000_000, to, value: 0, data: '0x' + randBytes(9000).toString('hex') }, 5),
  tx('oversize', { type: 2, chainId: 1, nonce: 2, maxPriorityFeePerGas: 1n, maxFeePerGas: 2n, gasLimit: 3_000_000, to, value: 0, data: '0x' + randBytes(17000).toString('hex') }, 0),
  personal('personal', Buffer.from('Hello from Ambire!\nSign in please.', 'utf8'), 0),
  personal('personal-hash', randBytes(32), 3),
  typed('ambire-operation', {
    types: {
      EIP712Domain: [
        { name: 'name', type: 'string' }, { name: 'version', type: 'string' }, { name: 'chainId', type: 'uint256' },
        { name: 'verifyingContract', type: 'address' }, { name: 'salt', type: 'bytes32' },
      ],
      AmbireOperation: [{ name: 'account', type: 'address' }, { name: 'hash', type: 'bytes32' }],
    },
    primaryType: 'AmbireOperation',
    domain: { name: 'Ambire', version: '1', chainId: 1, verifyingContract: '0x' + '34'.repeat(20), salt: '0x' + '00'.repeat(32) },
    message: { account: '0x' + '56'.repeat(20), hash: '0x' + randBytes(32).toString('hex') },
  }, 0),
  typed('safe-tx', {
    types: {
      EIP712Domain: [{ name: 'chainId', type: 'uint256' }, { name: 'verifyingContract', type: 'address' }],
      SafeTx: [
        { name: 'to', type: 'address' }, { name: 'value', type: 'uint256' }, { name: 'data', type: 'bytes' },
        { name: 'operation', type: 'uint8' }, { name: 'safeTxGas', type: 'uint256' }, { name: 'baseGas', type: 'uint256' },
        { name: 'gasPrice', type: 'uint256' }, { name: 'gasToken', type: 'address' }, { name: 'refundReceiver', type: 'address' },
        { name: 'nonce', type: 'uint256' },
      ],
    },
    primaryType: 'SafeTx',
    domain: { chainId: 10, verifyingContract: '0x' + '78'.repeat(20) },
    message: {
      to: '0x' + '9a'.repeat(20), value: '1000000000000000000', data: '0x' + randBytes(68).toString('hex'), operation: '0',
      safeTxGas: '0', baseGas: '0', gasPrice: '0', gasToken: '0x' + '00'.repeat(20), refundReceiver: '0x' + '00'.repeat(20), nonce: '42',
    },
  }, 1),
  typed('mail-arrays', {
    types: {
      EIP712Domain: [{ name: 'name', type: 'string' }, { name: 'version', type: 'string' }, { name: 'chainId', type: 'uint256' }, { name: 'verifyingContract', type: 'address' }],
      Person: [{ name: 'name', type: 'string' }, { name: 'wallets', type: 'address[]' }],
      Mail: [
        { name: 'from', type: 'Person' }, { name: 'to', type: 'Person[]' }, { name: 'contents', type: 'string' },
        { name: 'flags', type: 'bool[2]' }, { name: 'delta', type: 'int32' }, { name: 'tag', type: 'bytes4' },
      ],
    },
    primaryType: 'Mail',
    domain: { name: 'Ether Mail', version: '1', chainId: 1, verifyingContract: '0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC' },
    message: {
      from: { name: 'Cow', wallets: ['0xCD2a3d9F938E13CD947Ec05AbC7FE734Df8DD826', '0xDeaDbeefdEAdbeefdEadbEEFdeadbeEFdEaDbeeF'] },
      to: [{ name: 'Bob', wallets: ['0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB'] }, { name: 'Ünïcödé "quoted"\n', wallets: [] }],
      contents: 'Hello, Bob!', flags: [true, false], delta: '-12345', tag: '0xdeadbeef',
    },
  }, 2),
  typed('no-domain-type', {
    types: { Message: [{ name: 'note', type: 'string' }, { name: 'n', type: 'uint256' }] },
    primaryType: 'Message',
    domain: { name: 'ignored by v4' },
    message: { note: 'domain type absent', n: 5 },
  }, 0),
]

// The fragment chooser, for exact agreement with the reference
const fragments = []
for (let i = 0; i < 400; i++) {
  const seqLen = 1 + (rand() % 128)
  const seqNum = seqLen + 1 + (rand() % 1000)
  const checksum = rand()
  fragments.push({ seqNum, seqLen, checksum, indexes: chooseFragments(seqNum, seqLen, checksum) })
}

const out = {
  mnemonic: MNEMONIC,
  masterFingerprint: masterFp,
  account: {
    publicKey: account.publicKey.slice(2),
    chainCode: account.chainCode.slice(2),
    parentFingerprint: account.parentFingerprint.slice(2),
  },
  requestId: REQUEST_ID.replace(/-/g, ''),
  requests,
  fragments,
}
fs.writeFileSync(process.argv[2], JSON.stringify(out, null, 1))
console.log(`${requests.length} requests, ${fragments.length} fragment choices -> ${process.argv[2]}`)
